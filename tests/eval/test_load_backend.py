import asyncio

from roma.eval.load import LoadPolicy, run_ladder


def test_all_attempts_including_failures_count_and_stop_prevents_later_tiers():
    calls=[]
    async def workload():
        calls.append(1)
        return {'status':503,'fallback':True,'redis_ms':None,'rag_ms':None,'db_connections':None}
    report=asyncio.run(run_ladder(workload,ladder=(1,10,25),requests_per_user=2,
                                  policy=LoadPolicy(max_error_rate=0.1)))
    assert len(calls)==2 and len(report['tiers'])==1
    tier=report['tiers'][0]
    assert tier['attempts']==2 and tier['failures']==2 and tier['latency_ms']['p95']>=0
    assert report['stopped']=='error_rate' and report['real_model_capacity'] is None


def test_http_workload_runs_full_controller_call_and_keeps_native_metrics_unknown():
    from pathlib import Path

    import httpx
    from roma.eval.backend_lab import create_lab_app
    from roma.eval.script import load
    async def run():
        app=create_lab_app(load(Path('evals/scripts/clean_lock.jsonl')))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://lab') as client:
            async def workload():
                response=await client.post('/lab/call')
                return {'status':response.status_code,**response.json()}
            return await run_ladder(workload,ladder=(1,10),requests_per_user=1)
    report=asyncio.run(run())
    assert [t['attempts'] for t in report['tiers']]==[1,10]
    assert [t['turns'] for t in report['tiers']]==[12,120]
    assert all(t['failures']==0 and t['vram_peak_mb'] is None for t in report['tiers'])


def test_real_tier_rejects_missing_spend_permission_before_any_model_or_private_config():
    import pytest
    from scripts.load_backend import parse_args
    with pytest.raises(SystemExit) as error:
        parse_args(['--tier','real'])
    assert error.value.code==2


def test_load_latency_stop_and_timeout_include_every_attempt():
    async def slow():
        await asyncio.sleep(.03)
        return {'status':200}
    report=asyncio.run(run_ladder(slow,ladder=(1,10),requests_per_user=2,
                                 request_timeout_secs=.005,policy=LoadPolicy(max_error_rate=0)))
    assert report['tiers'][0]['attempts']==2 and report['tiers'][0]['failures']==2
    assert report['stopped']=='error_rate' and len(report['tiers'])==1
