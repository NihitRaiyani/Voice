"""Bounded backend load experiment, deliberately independent of native speech capacity."""
from __future__ import annotations

import asyncio
import math
import resource
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from time import perf_counter


@dataclass(frozen=True)
class LoadPolicy:
    """Operator-selected provisional stop limits; None means unagreed, never accepted."""
    max_error_rate: float | None = None
    max_fallback_rate: float | None = None
    max_p95_ms: float | None = None
    max_stall_ms: float | None = None
    max_peak_rss_mb: float | None = None

    def __post_init__(self):
        for key, value in asdict(self).items():
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError('Stop limits must be finite and nonnegative')
            if 'rate' in key and value is not None and value > 1:
                raise ValueError('Stop rates must be between zero and one')


def percentiles(values):
    if not values:
        return dict.fromkeys(('p50','p95','p99','max'))
    ordered=sorted(values)
    return {**{name: ordered[max(0,math.ceil(len(ordered)*q)-1)]
               for name,q in (('p50',.5),('p95',.95),('p99',.99))},'max':ordered[-1]}


def _rss_mb():
    scale=1 if sys.platform=='darwin' else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*scale/1024**2


async def run_ladder(workload, *, ladder=(1,10,25,50,100), requests_per_user=2,
                     policy=None, request_timeout_secs=30):
    """Execute a fixed number of closed-loop calls at each concurrency, retaining failures.

    The injected workload is an HTTP call in the CLI. No providers, credentials or private
    settings are constructed here. No real-model mode exists in this automatic runner.
    """
    if (not ladder or any(type(n) is not int or not 1<=n<=100 for n in ladder)
            or list(ladder)!=sorted(set(ladder)) or type(requests_per_user) is not int
            or not 1<=requests_per_user<=100 or not math.isfinite(request_timeout_secs)
            or not 0<request_timeout_secs<=300):
        raise ValueError('Load bounds require increasing 1..100 concurrency and bounded attempts')
    policy=policy or LoadPolicy()
    tiers=[]
    stopped=None
    for users in ladder:
        samples=[]
        stalls=[]
        async def monitor(stalls=stalls):
            while True:
                start=perf_counter()
                await asyncio.sleep(.005)
                stalls.append(max(0,(perf_counter()-start-.005)*1000))
        async def user(samples=samples):
            for _ in range(requests_per_user):
                start=perf_counter()
                try:
                    async with asyncio.timeout(request_timeout_secs):
                        response=await workload()
                    status=response['status']
                    sample={key:response.get(key) for key in
                            ('fallback','redis_ms','rag_ms','db_connections','turns','quality_ok')}
                except Exception:  # noqa: BLE001 — all attempts count; no private exception text.
                    status=0
                    sample={'fallback':False,'redis_ms':None,'rag_ms':None,
                            'db_connections':None,'turns':0,'quality_ok':None}
                samples.append({**sample,'status':status,'latency_ms':(perf_counter()-start)*1000})
        cpu_start=resource.getrusage(resource.RUSAGE_SELF)
        started=perf_counter()
        monitor_task=asyncio.create_task(monitor())
        try:
            await asyncio.gather(*(user() for _ in range(users)))
            await asyncio.sleep(.005)
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        elapsed=perf_counter()-started
        cpu_end=resource.getrusage(resource.RUSAGE_SELF)
        failures=sum(not 200<=s['status']<300 for s in samples)
        fallbacks=sum(bool(s['fallback']) for s in samples)
        quality_failures=sum(s.get('quality_ok') is False for s in samples)
        connections=[s['db_connections'] for s in samples if s['db_connections'] is not None]
        tier={'concurrency':users,'attempts':len(samples),'completed':len(samples)-failures,
              'failures':failures,'fallbacks':fallbacks,'quality_failures':quality_failures,
              'turns':sum(s.get('turns') or 0 for s in samples),
              'status_counts':dict(Counter(str(s['status']) for s in samples)),
              'elapsed_secs':elapsed,'throughput_calls_per_sec':len(samples)/elapsed,
              'latency_ms':percentiles([s['latency_ms'] for s in samples]),
              'redis_latency_ms':percentiles([s['redis_ms'] for s in samples if s['redis_ms'] is not None]),
              'rag_latency_ms':percentiles([s['rag_ms'] for s in samples if s['rag_ms'] is not None]),
              'event_loop_stall_ms':percentiles(stalls),'event_loop_samples':len(stalls),
              'db_connections_max':max(connections) if connections else None,
              'process_cpu_seconds':cpu_end.ru_utime+cpu_end.ru_stime-cpu_start.ru_utime-cpu_start.ru_stime,
              'process_peak_rss_mb':_rss_mb(),'gpu_utilization_percent':None,'vram_peak_mb':None,
              'native_ttft_ms':None}
        tiers.append(tier)
        comparisons=(('error_rate',policy.max_error_rate,failures/len(samples)),
                     ('fallback_rate',policy.max_fallback_rate,fallbacks/len(samples)),
                     ('latency_p95',policy.max_p95_ms,tier['latency_ms']['p95']),
                     ('event_loop_stall',policy.max_stall_ms,tier['event_loop_stall_ms']['max']),
                     ('peak_rss',policy.max_peak_rss_mb,tier['process_peak_rss_mb']))
        if quality_failures:
            stopped='quality_failure'
        else:
            stopped=next((name for name,limit,value in comparisons
                          if limit is not None and value is not None and value>limit),None)
        if stopped:
            break
    return {'schema_version':1,'evidence_kind':'mock_backend','transport':'http_asgi_in_process',
            'workload':'full scripted text calls; mocked LLM/extraction; optional native SQL/Redis probes',
            'policy_status':'provisional' if any(v is not None for v in asdict(policy).values()) else 'unset',
            'stop_limits':asdict(policy),'stopped':stopped,'tiers':tiers,
            'real_model_capacity':None,'production_slo_passed':False,
            'resource_scope':'load generator and ASGI backend process; SQL/Redis server CPU/RAM unmeasured'}
