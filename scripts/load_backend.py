"""Run isolated mock backend calls; never reads private settings or constructs paid AI."""
import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from roma.eval.backend_lab import create_lab_app
from roma.eval.load import LoadPolicy, run_ladder
from roma.eval.native_services import disposable_services
from roma.eval.project_readiness import fingerprint
from roma.eval.script import load

ROOT = Path(__file__).resolve().parents[1]


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tier',choices=('mock','real'),default='mock')
    p.add_argument('--allow-real-models',action='store_true')
    p.add_argument('--spend-budget-inr',type=float)
    p.add_argument('--native-services',action='store_true',help='Start owned disposable PostgreSQL/Redis only')
    p.add_argument('--requests-per-user',type=int,default=2)
    p.add_argument('--max-error-rate',type=float)
    p.add_argument('--max-fallback-rate',type=float)
    p.add_argument('--max-p95-ms',type=float)
    p.add_argument('--max-stall-ms',type=float)
    p.add_argument('--max-peak-rss-mb',type=float)
    p.add_argument('--output',type=Path)
    p.add_argument('--serve',action='store_true',help='Serve isolated mock lab on loopback for optional Locust')
    p.add_argument('--port',type=int,default=8098)
    args=p.parse_args(argv)
    if args.tier=='real':
        if not args.allow_real_models or args.spend_budget_inr is None or args.spend_budget_inr<=0:
            p.error('Real tier requires explicit --allow-real-models and positive --spend-budget-inr')
        p.error('Real tier is not automated: use separately supervised acceptance with approved quality/latency stops, account limits and shared spend reservations')
    if args.allow_real_models or args.spend_budget_inr is not None:
        p.error('Model/spend flags are invalid for mock backend measurements')
    if not 1<=args.port<=65535:
        p.error('Port must be 1..65535')
    return args


async def run(args,urls):
    import httpx
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import create_async_engine
    engine=create_async_engine(urls[0],pool_size=5,max_overflow=5,hide_parameters=True) if urls else None
    redis=Redis.from_url(urls[1],socket_timeout=2) if urls else None
    fixture=ROOT/'evals/scripts/clean_lock.jsonl'
    script=await asyncio.to_thread(load,fixture)
    app=create_lab_app(script,engine=engine,redis=redis)
    try:
        if args.serve:
            import uvicorn
            await uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,log_level='warning')).serve()
            return None
        policy=LoadPolicy(args.max_error_rate,args.max_fallback_rate,args.max_p95_ms,
                          args.max_stall_ms,args.max_peak_rss_mb)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://isolated-lab') as client:
            async def workload():
                response=await client.post('/lab/call')
                return {'status':response.status_code,**response.json()}
            report=await run_ladder(workload,requests_per_user=args.requests_per_user,policy=policy)
        revision=(await asyncio.to_thread(subprocess.run,['git','rev-parse','HEAD'],capture_output=True,text=True,check=True)).stdout.strip()
        report.update(generated_at=datetime.now(UTC).isoformat(),revision=revision,
                      dataset_sha256=fingerprint(await asyncio.to_thread(fixture.read_text)),
                      configuration_sha256=fingerprint({'requests_per_user':args.requests_per_user,
                          'native_services':bool(urls),'policy':report['stop_limits']}),
                      hardware=f'{platform.system()} {platform.machine()}',
                      conditions='unprimed first request; mock calls; sequential tiers share process',
                      source_state='working_tree',python_version=platform.python_version(),
                      source_sha256={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (ROOT/'roma/eval/backend_lab.py',ROOT/'roma/eval/load.py',
                                       ROOT/'roma/eval/native_services.py',ROOT/'scripts/load_backend.py')},
                      dependency_versions={name:version(name) for name in ('fastapi','httpx','SQLAlchemy','asyncpg','redis')},
                      models={'llm':'fixture-driven MockLLMProvider','stt':None,'tts':None,'embeddings':None},
                      services={'postgres':'owned_disposable_native' if urls else 'unmeasured',
                                'redis':'owned_disposable_native' if urls else 'unmeasured'},
                      limitations=['In-process ASGI excludes sockets, carrier, speech, paid Claude and cross-process workers.',
                                   'SQL SELECT/probe connections and Redis PING are measured; these are not per-turn durable checkpoints.',
                                   'Twelve fixture turns execute production text orchestration, extraction parsing and final safety; no booking commit.',
                                   'RAG not invoked: RAG latency remains null. Process peak RSS includes imports and prior tiers.'])
        return report
    finally:
        if redis is not None:
            await redis.aclose()
        if engine is not None:
            await engine.dispose()


def main(argv=None):
    args=parse_args(argv)
    with disposable_services() if args.native_services else nullcontext(None) as urls:
        report=asyncio.run(run(args,urls))
    if report is None:
        return 0
    output=json.dumps(report,indent=2,allow_nan=False)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output)
    else:
        print(output,end='')
    return 1 if report['stopped'] else 0


if __name__=='__main__':
    raise SystemExit(main())
