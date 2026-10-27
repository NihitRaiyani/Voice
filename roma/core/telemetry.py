"""Process-local bounded telemetry. No payloads, call IDs, arbitrary labels, or exporters.

Prometheus text 0.0.4 histograms retain cumulative bucket counts, never samples.
Optional OpenTelemetry spans use a private provider and contain only reviewed labels.
"""

import asyncio
import math
from collections import deque
from contextlib import contextmanager, nullcontext
from functools import wraps
from threading import Lock
from time import perf_counter

STAGES = frozenset({
    'endpointing', 'stt', 'brain', 'llm_first', 'tts_first', 'turn', 'safety',
    'rag', 'rag_embedding', 'rag_answer', 'queue_wait', 'checkpoint', 'lock',
    'event_loop_stall', 'provider',
})
OUTCOMES = frozenset({'ok', 'error', 'cancelled', 'timeout', 'rejected', 'fallback', 'rate_limit'})
ROUTES = frozenset({'none', 'knowledge', 'no_evidence', 'script', 'bypass', 'fallback'})
QUEUES = frozenset({'audio_in', 'partials', 'finals', 'tokens', 'chunks', 'safe_chunks',
                    'tts_text', 'tts_audio', 'audio'})
EVENTS = frozenset({'safety', 'rag_route', 'rag_fallback', 'queue_drop', 'checkpoint',
                    'lock', 'dead_letter', 'plc', 'turn', 'call', 'claude'})
BUCKETS = (.005, .01, .025, .05, .1, .25, .5, 1., 2.5, 5., 10., 30., 60.)


def _label(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError('Use a reviewed telemetry label')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Telemetry values must be finite and nonnegative')
    return float(value)


class Telemetry:
    def __init__(self, *, clock=perf_counter, tracer=None):
        self.clock, self.tracer = clock, tracer
        self._lock = Lock()
        self._histograms = {}
        self._counters = {}
        self._gauges = {}

    def observe_latency(self, stage, seconds, *, outcome='ok', route='none'):
        key = (_label(stage, STAGES), _label(outcome, OUTCOMES), _label(route, ROUTES))
        seconds = _number(seconds)
        with self._lock:
            buckets, count, total = self._histograms.get(key, ([0] * len(BUCKETS), 0, 0.))
            for index, bound in enumerate(BUCKETS):
                buckets[index] += int(seconds <= bound)
            self._histograms[key] = (buckets, count + 1, total + seconds)

    def event(self, name, *, outcome='ok', count=1):
        key = ('events', (_label(name, EVENTS), _label(outcome, OUTCOMES)))
        count = _number(count)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + count

    def queue(self, name, *, depth, wait_seconds=None, dropped=0):
        name = _label(name, QUEUES)
        depth, dropped = _number(depth), _number(dropped)
        if wait_seconds is not None:
            wait_seconds = _number(wait_seconds)
        with self._lock:
            self._gauges[('queue_depth', name)] = depth
            if dropped:
                key = ('queue_drops', (name,))
                self._counters[key] = self._counters.get(key, 0) + dropped
        if wait_seconds is not None:
            self.observe_latency('queue_wait', wait_seconds)

    def queue_drops(self, name, count=1):
        name, count = _label(name, QUEUES), _number(count)
        with self._lock:
            key = ('queue_drops', (name,))
            self._counters[key] = self._counters.get(key, 0) + count
        self.event('queue_drop', outcome='rejected', count=count)

    def adjust_queue_depth(self, name, delta):
        name = _label(name, QUEUES)
        if type(delta) is not int:
            raise ValueError('Queue depth delta must be an integer')
        with self._lock:
            key = ('queue_depth', name)
            self._gauges[key] = max(0, self._gauges.get(key, 0) + delta)

    def resources(self, *, cpu_seconds=None, rss_bytes=None, gpu_bytes=None, peak_rss_bytes=None):
        values = {'cpu_seconds': cpu_seconds, 'rss_bytes': rss_bytes, 'gpu_bytes': gpu_bytes,
                  'peak_rss_bytes': peak_rss_bytes}
        checked = {key: _number(value) for key, value in values.items() if value is not None}
        with self._lock:
            # Unknown GPU availability removes an earlier observation rather than
            # retaining a fabricated zero or presenting a stale sample as current.
            for key in values:
                if key not in checked:
                    self._gauges.pop(('resource_value', key), None)
            self._gauges.update({('resource_value', key): value for key, value in checked.items()})

    @contextmanager
    def span(self, stage, *, route='none'):
        stage, route = _label(stage, STAGES), _label(route, ROUTES)
        context = (self.tracer.start_as_current_span(
            'roma.' + stage, attributes={'roma.route': route},
            record_exception=False, set_status_on_exception=False,
        ) if self.tracer is not None else nullcontext())
        with context as span:
            outcome = 'ok'
            try:
                yield span
            except asyncio.CancelledError:
                outcome = 'cancelled'
                raise
            except TimeoutError:
                outcome = 'timeout'
                raise
            except BaseException:
                outcome = 'error'
                raise
            finally:
                if span is not None:
                    # Fixed attributes only; exception strings may contain caller data.
                    span.set_attribute('roma.outcome', outcome)

    @contextmanager
    def time(self, stage, *, route='none'):
        _label(stage, STAGES)
        _label(route, ROUTES)
        started, outcome = self.clock(), 'ok'
        try:
            with self.span(stage, route=route):
                yield
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except TimeoutError:
            outcome = 'timeout'
            raise
        except BaseException:
            outcome = 'error'
            raise
        finally:
            self.observe_latency(stage, max(0, self.clock() - started),
                                 outcome=outcome, route=route)

    def render(self):
        lines = ['# HELP roma_latency_seconds Observed operation latency in seconds.',
                 '# TYPE roma_latency_seconds histogram']
        with self._lock:
            for (stage, outcome, route), (buckets, count, total) in sorted(self._histograms.items()):
                labels = f'stage="{stage}",outcome="{outcome}",route="{route}"'
                for bound, value in zip(BUCKETS, buckets, strict=True):
                    lines.append(f'roma_latency_seconds_bucket{{{labels},le="{bound:g}"}} {value}')
                lines.extend([
                    f'roma_latency_seconds_bucket{{{labels},le="+Inf"}} {count}',
                    f'roma_latency_seconds_count{{{labels}}} {count}',
                    f'roma_latency_seconds_sum{{{labels}}} {total:g}',
                ])
            lines.extend([
                '# HELP roma_events_total Observed operational events by reviewed event and outcome.',
                '# TYPE roma_events_total counter',
                '# HELP roma_queue_drops_total Dropped queue items by reviewed queue name.',
                '# TYPE roma_queue_drops_total counter',
                '# HELP roma_queue_depth Queued items across active process-local channels.',
                '# TYPE roma_queue_depth gauge',
                '# HELP roma_resource_value Observed native process resources in the labelled unit.',
                '# TYPE roma_resource_value gauge',
            ])
            for (metric, labels), value in sorted(self._counters.items()):
                if metric == 'events':
                    dimensions = f'event="{labels[0]}",outcome="{labels[1]}"'
                else:
                    dimensions = f'queue="{labels[0]}"'
                lines.append(f'roma_{metric}_total{{{dimensions}}} {value:g}')
            for (metric, label), value in sorted(self._gauges.items()):
                dimension = 'queue' if metric == 'queue_depth' else 'resource'
                lines.append(f'roma_{metric}{{{dimension}="{label}"}} {value:g}')
        return '\n'.join(lines) + '\n'


_telemetry = Telemetry()


def get_telemetry():
    return _telemetry


def set_telemetry(telemetry):
    global _telemetry
    if not isinstance(telemetry, Telemetry):
        raise TypeError('Telemetry instance required')
    previous, _telemetry = _telemetry, telemetry
    return previous


def configure_tracing(*, enabled=False, processor=None):
    """Return a private provider, without globally configuring SDKs or exporters.

    A caller may supply an explicitly approved SpanProcessor. Merely setting OTEL
    environment variables never initiates network export from this module.
    """
    if not enabled:
        return None
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError as exc:
        raise RuntimeError('Install the observability extra to enable tracing') from exc
    provider = TracerProvider(resource=Resource({'service.name': 'roma'}))
    if processor is not None:
        provider.add_span_processor(processor)
    return provider


class RecentSpanProcessor:
    """Bounded local trace metadata only; never an exporter or payload store."""

    def __init__(self, capacity=256):
        if type(capacity) is not int or not 1 <= capacity <= 1024:
            raise ValueError('Trace history capacity must be between 1 and 1024')
        self._spans = deque(maxlen=capacity)
        self._lock = Lock()

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        if span.name not in {'roma.' + stage for stage in STAGES}:
            return
        attributes = span.attributes or {}
        route = attributes.get('roma.route', 'none')
        outcome = attributes.get('roma.outcome', 'ok')
        if route not in ROUTES or outcome not in OUTCOMES:
            return
        row = {
            'name': span.name,
            'trace_id': f'{span.context.trace_id:032x}',
            'span_id': f'{span.context.span_id:016x}',
            'parent_span_id': f'{span.parent.span_id:016x}' if span.parent else None,
            'start_time_ns': span.start_time,
            'end_time_ns': span.end_time,
            'route': route,
            'outcome': outcome,
        }
        with self._lock:
            self._spans.append(row)

    def snapshot(self):
        with self._lock:
            return tuple(dict(row) for row in self._spans)

    def shutdown(self):
        with self._lock:
            self._spans.clear()

    def force_flush(self, timeout_millis=30000):
        return True


@contextmanager
def tracing_runtime(telemetry, *, enabled=False, capacity=256):
    """Explicit private SDK lifecycle with fixed bounded local metadata storage.

    Leaves global OpenTelemetry state and exporter environment variables alone.
    Disabled startup neither imports the SDK nor changes the existing tracer.
    """
    if not enabled:
        yield None
        return
    # Inherit the installed SDK lifecycle hooks without making metrics import
    # the optional dependency when tracing is disabled.
    from opentelemetry.sdk.trace import SpanProcessor

    class LocalSpanProcessor(RecentSpanProcessor, SpanProcessor):
        pass

    processor = LocalSpanProcessor(capacity)
    provider = configure_tracing(enabled=True, processor=processor)
    previous = telemetry.tracer
    try:
        telemetry.tracer = provider.get_tracer('roma')
        yield processor
    finally:
        telemetry.tracer = previous
        provider.shutdown()


class TurnTelemetry:
    """One bounded instance per turn; caller owns its lifecycle, no IDs retained.

    Start at actual work initiation, finish at the first observed output. A repeated
    finish is ignored. Cancelling records durations for only still-pending stages.
    """
    def __init__(self, telemetry=None, *, route='none'):
        self.telemetry = telemetry or get_telemetry()
        self.route = _label(route, ROUTES)
        self._pending = {}
        self._finished = set()
        self._spans = {}

    def start(self, stage):
        stage = _label(stage, STAGES)
        if stage not in self._finished and stage not in self._pending:
            self._pending[stage] = self.telemetry.clock()
            if self.telemetry.tracer is not None:
                options = {}
                parent = self._spans.get('turn')
                if parent is not None and hasattr(parent, 'get_span_context'):
                    from opentelemetry.trace import set_span_in_context
                    # Explicit parentage avoids keeping a context active across
                    # Pipecat processor tasks or leaking it to another caller.
                    options['context'] = set_span_in_context(parent)
                self._spans[stage] = self.telemetry.tracer.start_span(
                    'roma.' + stage, attributes={'roma.route': self.route}, **options)

    def finish(self, stage, *, outcome='ok'):
        stage, outcome = _label(stage, STAGES), _label(outcome, OUTCOMES)
        started = self._pending.pop(stage, None)
        if started is not None:
            self.telemetry.observe_latency(stage, max(0, self.telemetry.clock() - started),
                                           outcome=outcome, route=self.route)
            self._finished.add(stage)
            span = self._spans.pop(stage, None)
            if span is not None:
                span.set_attribute('roma.outcome', outcome)
                span.end()

    def cancel(self):
        for stage in tuple(self._pending):
            self.finish(stage, outcome='cancelled')

    def error(self):
        for stage in tuple(self._pending):
            self.finish(stage, outcome='error')


async def monitor_event_loop(telemetry=None, *, interval=1., stop=None, clock=perf_counter):
    """Opt-in lifecycle coroutine. No background tasks are spawned on import."""
    telemetry = telemetry or get_telemetry()
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError('Positive monitoring interval required')
    while stop is None or not stop.is_set():
        started = clock()
        await asyncio.sleep(interval)
        telemetry.observe_latency('event_loop_stall', max(0, clock() - started - interval))


def observed(stage, *, event=None):
    """Measure an async operation without retaining its arguments or return value."""
    _label(stage, STAGES)
    if event is not None:
        _label(event, EVENTS)

    def decorate(function):
        @wraps(function)
        async def measure(*args, **kwargs):
            telemetry, outcome = get_telemetry(), 'ok'
            try:
                with telemetry.time(stage):
                    return await function(*args, **kwargs)
            except asyncio.CancelledError:
                outcome = 'cancelled'
                raise
            except TimeoutError:
                outcome = 'timeout'
                raise
            except BaseException:
                outcome = 'error'
                raise
            finally:
                if event is not None:
                    telemetry.event(event, outcome=outcome)
        return measure
    return decorate


def sample_process_resources(telemetry=None):
    """Native process CPU and high-water RSS only; GPU/current RSS stay unknown."""
    import resource
    import sys

    usage = resource.getrusage(resource.RUSAGE_SELF)
    scale = 1 if sys.platform == 'darwin' else 1024
    (telemetry or get_telemetry()).resources(
        cpu_seconds=usage.ru_utime + usage.ru_stime,
        peak_rss_bytes=usage.ru_maxrss * scale,
    )
