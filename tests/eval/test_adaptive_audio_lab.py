import asyncio

from roma.eval.adaptive_audio import report


def test_fixed_seed_loss_jitter_replays_bit_identically_through_real_control():
    first = asyncio.run(report())
    second = asyncio.run(report())
    assert first == second
    assert first['synthetic_only'] and not first['native_quality_measured']
    assert first['impairment'] == {'seed': 3550, 'jitter_radius_ms': 50, 'loss_percent': 5,
                                   'source_frames': 500, 'dropped_frames': 25}
    assert first['adaptive']['pending_packets'] == 0
    assert 40 <= first['adaptive']['max_target_delay_ms'] <= 120
    assert first['adaptive']['max_depth'] <= 16
    assert first['adaptive']['concealed_frames'] > 0
    assert first['adaptive']['concealed_frames'] == first['adaptive']['concealed_frames_excluded_from_endpoint']
    assert first['fixed_baseline']['delay_ms'] == 60
    assert first['controller']['scripted_stt_calls'] >= 1
    assert first['controller']['stop_signal_honoured']
    assert first['controller']['booking_effects'] == 0
