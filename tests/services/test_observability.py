import asyncio

import pytest
from roma.core.telemetry import Telemetry


def test_latency_histogram_has_percentile_buckets_and_rejects_unreviewed_labels():
    telemetry = Telemetry()
    telemetry.observe_latency('tts_first', 0.125, route='knowledge')
    telemetry.observe_latency('tts_first', 0.8, route='knowledge')
    body = telemetry.render()
    assert 'roma_latency_seconds_bucket{stage="tts_first",outcome="ok",route="knowledge",le="0.25"} 1' in body
    assert 'roma_latency_seconds_count{stage="tts_first",outcome="ok",route="knowledge"} 2' in body
    for stage, route in [('caller@example.com', 'knowledge'), ('rag', '+919999999999')]:
        with pytest.raises(ValueError):
            telemetry.observe_latency(stage, .1, route=route)
    assert 'caller@example.com' not in telemetry.render()
    assert '+919999999999' not in telemetry.render()


def test_timer_records_actual_duration_and_failure_without_exception_payload():
    telemetry = Telemetry(clock=iter([1., 1.5, 2., 2.1, 3., 3.2]).__next__)
    with telemetry.time('rag'):
        pass
    with pytest.raises(RuntimeError), telemetry.time('rag'):
        raise RuntimeError('private caller@example.com')
    with pytest.raises(asyncio.CancelledError), telemetry.time('rag'):
        raise asyncio.CancelledError('secret call id')
    rendered = telemetry.render()
    assert 'outcome="ok",route="none"} 0.5' in rendered
    assert 'outcome="error",route="none"} 1' in rendered
    assert 'outcome="cancelled",route="none"} 1' in rendered
    assert 'private' not in rendered and 'secret' not in rendered


def test_tracing_disabled_never_imports_sdk_or_configures_exporters(monkeypatch):
    import builtins

    from roma.core.telemetry import configure_tracing
    original = builtins.__import__

    def guard(name, *args, **kwargs):
        assert not name.startswith('opentelemetry')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guard)
    assert configure_tracing() is None


def test_turn_observer_records_first_audio_once_and_cancellation():
    from roma.core.telemetry import TurnTelemetry
    now = [10.]
    telemetry = Telemetry(clock=lambda: now[0])
    turn = TurnTelemetry(telemetry)
    turn.start('turn')
    turn.start('tts_first')
    now[0] = 10.3
    turn.finish('tts_first')
    turn.finish('tts_first')
    now[0] = 10.5
    turn.cancel()
    rendered = telemetry.render()
    assert 'roma_latency_seconds_count{stage="tts_first",outcome="ok",route="none"} 1' in rendered
    assert 'roma_latency_seconds_count{stage="turn",outcome="cancelled",route="none"} 1' in rendered


def test_operational_metrics_are_observations_and_missing_gpu_is_absent():
    telemetry = Telemetry()
    telemetry.event('plc', count=3)
    telemetry.queue('audio_in', depth=4, wait_seconds=.04, dropped=2)
    telemetry.resources(cpu_seconds=1.5, rss_bytes=4096, gpu_bytes=None)
    body = telemetry.render()
    assert 'roma_events_total{event="plc",outcome="ok"} 3' in body
    assert 'roma_queue_depth{queue="audio_in"} 4' in body
    assert 'roma_queue_drops_total{queue="audio_in"} 2' in body
    assert 'roma_resource_value{resource="rss_bytes"} 4096' in body
    assert 'gpu_bytes' not in body
    with pytest.raises(ValueError):
        telemetry.event('call-id-private')
    with pytest.raises(ValueError):
        telemetry.queue('caller@example.com', depth=0)


class MemorySpan:
    def __init__(self, name, attributes):
        self.name, self.attributes = name, dict(attributes)
        self.ends = 0

    def set_attribute(self, name, value):
        self.attributes[name] = value

    def end(self):
        self.ends += 1


class MemoryTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, *, attributes):
        span = MemorySpan(name, attributes)
        self.spans.append(span)
        return span

    def start_as_current_span(self, name, *, attributes, record_exception,
                              set_status_on_exception):
        from contextlib import contextmanager
        assert record_exception is False and set_status_on_exception is False
        span = self.start_span(name, attributes=attributes)

        @contextmanager
        def active():
            try:
                yield span
            finally:
                span.end()
        return active()


def test_optional_spans_end_once_and_retain_only_reviewed_outcome_and_route():
    from roma.core.telemetry import TurnTelemetry
    tracer = MemoryTracer()
    telemetry = Telemetry(tracer=tracer)
    turn = TurnTelemetry(telemetry, route='knowledge')
    turn.start('turn')
    turn.start('tts_first')
    turn.start('tts_first')
    turn.finish('tts_first')
    turn.finish('tts_first')
    turn.cancel()
    turn.cancel()
    with pytest.raises(TimeoutError), telemetry.time('rag'):
        raise TimeoutError('private@example.invalid')
    with pytest.raises(asyncio.CancelledError), telemetry.time('safety'):
        raise asyncio.CancelledError('private-call-id')
    assert [span.name for span in tracer.spans] == [
        'roma.turn', 'roma.tts_first', 'roma.rag', 'roma.safety']
    assert [span.attributes['roma.outcome'] for span in tracer.spans] == [
        'cancelled', 'ok', 'timeout', 'cancelled']
    assert all(span.ends == 1 for span in tracer.spans)
    assert all(set(span.attributes) == {'roma.route', 'roma.outcome'} for span in tracer.spans)
    assert 'private' not in repr([span.attributes for span in tracer.spans])


def test_event_loop_monitor_records_actual_scheduling_delay_and_stops(monkeypatch):
    from roma.core.telemetry import monitor_event_loop
    async def run():
        now, stop = [1.], asyncio.Event()
        telemetry = Telemetry()
        async def scheduled_sleep(interval):
            assert interval == .5
            now[0] += .625
            stop.set()
        monkeypatch.setattr(asyncio, 'sleep', scheduled_sleep)
        await monitor_event_loop(telemetry, interval=.5, stop=stop, clock=lambda: now[0])
        body = telemetry.render()
        assert 'roma_latency_seconds_sum{stage="event_loop_stall",outcome="ok",route="none"} 0.125' in body
        assert 'roma_latency_seconds_count{stage="event_loop_stall",outcome="ok",route="none"} 1' in body
        with pytest.raises(ValueError):
            await monitor_event_loop(telemetry, interval=0)
    asyncio.run(run())


def test_native_resource_sampler_reports_units_and_does_not_fabricate_current_or_gpu(monkeypatch):
    import resource
    import sys
    from types import SimpleNamespace

    from roma.core.telemetry import sample_process_resources
    telemetry = Telemetry()
    telemetry.resources(gpu_bytes=100)
    monkeypatch.setattr(resource, 'getrusage', lambda _: SimpleNamespace(
        ru_utime=1.25, ru_stime=.5, ru_maxrss=4096))
    sample_process_resources(telemetry)
    body = telemetry.render()
    scale = 1 if sys.platform == 'darwin' else 1024
    assert 'roma_resource_value{resource="cpu_seconds"} 1.75' in body
    assert f'roma_resource_value{{resource="peak_rss_bytes"}} {4096 * scale:g}' in body
    assert 'resource="rss_bytes"' not in body and 'gpu_bytes' not in body


@pytest.mark.parametrize('invalid', [-1, float('nan'), float('inf'), True, '1'])
def test_invalid_observations_never_enter_the_registry(invalid):
    telemetry = Telemetry()
    with pytest.raises(ValueError):
        telemetry.observe_latency('turn', invalid)
    with pytest.raises(ValueError):
        telemetry.event('turn', count=invalid)
    with pytest.raises(ValueError):
        telemetry.resources(cpu_seconds=invalid)
    assert 'stage="turn"' not in telemetry.render()
    assert 'event="turn"' not in telemetry.render()


def test_enabled_sdk_provider_does_not_replace_global_provider_or_add_exporters():
    pytest.importorskip('opentelemetry.sdk.trace')
    from opentelemetry import trace
    from roma.core.telemetry import configure_tracing
    global_provider = trace.get_tracer_provider()
    provider = configure_tracing(enabled=True)
    try:
        assert provider is not global_provider
        assert trace.get_tracer_provider() is global_provider
        assert not provider._active_span_processor._span_processors
    finally:
        provider.shutdown()


def test_sdk_frame_stage_spans_share_turn_trace_without_global_or_exception_data():
    pytest.importorskip('opentelemetry.sdk.trace')
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from roma.core.telemetry import TurnTelemetry, configure_tracing
    exporter = InMemorySpanExporter()
    global_provider = trace.get_tracer_provider()
    provider = configure_tracing(enabled=True, processor=SimpleSpanProcessor(exporter))
    try:
        telemetry = Telemetry(tracer=provider.get_tracer('roma.test'))
        turn = TurnTelemetry(telemetry)
        turn.start('turn')
        turn.start('stt')
        turn.finish('stt')
        turn.cancel()
        with pytest.raises(RuntimeError), telemetry.time('safety'):
            raise RuntimeError('caller@example.invalid')
        spans = exporter.get_finished_spans()
        stt, whole, safety = spans
        assert stt.name == 'roma.stt' and whole.name == 'roma.turn'
        assert stt.context.trace_id == whole.context.trace_id
        assert stt.parent.span_id == whole.context.span_id
        assert whole.attributes['roma.outcome'] == 'cancelled'
        assert safety.attributes['roma.outcome'] == 'error'
        assert not safety.events
        assert 'caller@example.invalid' not in repr(spans)
        assert trace.get_tracer_provider() is global_provider
    finally:
        provider.shutdown()


@pytest.mark.parametrize(('metric', 'description'), [
    ('roma_events_total', 'Observed operational events by reviewed event and outcome.'),
    ('roma_queue_drops_total', 'Dropped queue items by reviewed queue name.'),
    ('roma_queue_depth', 'Queued items across active process-local channels.'),
    ('roma_resource_value', 'Observed native process resources in the labelled unit.'),
])
def test_every_operational_metric_family_has_fixed_prometheus_help(metric, description):
    telemetry = Telemetry()
    telemetry.event('turn')
    telemetry.queue('audio', depth=1, dropped=1)
    telemetry.resources(cpu_seconds=1)
    assert f'# HELP {metric} {description}\n' in telemetry.render()


def test_explicit_private_trace_runtime_has_bounded_safe_history_and_restores_tracer(monkeypatch):
    pytest.importorskip('opentelemetry.sdk.trace')
    from opentelemetry import trace
    from roma.core.telemetry import tracing_runtime
    telemetry = Telemetry()
    global_provider = trace.get_tracer_provider()
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'https://must-not-be-contacted.invalid')
    with tracing_runtime(telemetry, enabled=False) as runtime:
        assert runtime is None and telemetry.tracer is None
    with tracing_runtime(telemetry, enabled=True, capacity=2) as runtime:
        assert telemetry.tracer is not None
        for _ in range(4):
            with telemetry.time('safety'):
                pass
        rows = runtime.snapshot()
        assert len(rows) == 2
        assert all(row['name'] == 'roma.safety' for row in rows)
        assert all(set(row) == {'name', 'trace_id', 'span_id', 'parent_span_id',
                                'start_time_ns', 'end_time_ns', 'route', 'outcome'} for row in rows)
        assert all(row['outcome'] == 'ok' for row in rows)
        assert trace.get_tracer_provider() is global_provider
    assert telemetry.tracer is None and runtime.snapshot() == ()
