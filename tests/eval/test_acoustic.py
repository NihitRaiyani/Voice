"""Synthetic calibration signals never establish physical/native acceptance."""
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest


def fixture_wav(path, *, reference=(300, 600), response=(900, 1200), rate=16000,
                duration_ms=1600, bleed=False, noise=0, clipped=False, quiet_tail=False, swap=False):
    samples = []
    for index in range(rate * duration_ms // 1000):
        time_ms = index * 1000 / rate
        left = int(6000 * math.sin(2 * math.pi * 600 * index / rate)) \
            if reference and reference[0] <= time_ms < reference[1] else 0
        right = int(5000 * math.sin(2 * math.pi * 1000 * index / rate)) \
            if response and response[0] <= time_ms < response[1] else 0
        if quiet_tail and 600 <= time_ms < 650:
            left = int(700 * math.sin(2 * math.pi * 600 * index / rate))
        if bleed and left:
            right += left // 2
        if noise:
            right += noise if index % 2 else -noise
        if clipped and index == rate:
            right = 32767
        samples.extend((right, left) if swap else (left, right))
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
    return path


def configuration(**changes):
    from roma.eval.acoustic import AcousticConfig

    defaults = dict(reference_channel=0, speaker_channel=1,
                    reference_window_ms=(200, 700), noise_window_ms=(0, 100),
                    signal_rms=0.04, max_noise_rms=0.005)
    return AcousticConfig(**(defaults | changes))


def test_known_delay_reports_scalar_energy_estimate_without_physical_claim(tmp_path):
    from roma.eval.acoustic import analyze_wav

    path = fixture_wav(tmp_path / 'synthetic.wav')
    result = analyze_wav(path, configuration(), provenance='synthetic', approved_input=True)
    assert result['caller_offset_ms'] == 600
    assert result['speaker_onset_ms'] == 900
    assert result['ttfa_ms'] == 300
    assert result['resolution_ms'] == 10
    assert result['provenance'] == 'synthetic'
    assert result['physical_setup_verified'] is False
    assert result['acceptance_gate_passed'] is False
    assert result['measurement'] == 'thresholded_energy_onset_estimate'
    assert all(not isinstance(value, (list, dict)) for value in result.values())
    assert str(path) not in json.dumps(result)


@pytest.mark.parametrize('case,code', [
    ({'bleed': True}, 'speaker_overlap_or_bleed'),
    ({'response': (500, 800)}, 'speaker_overlap_or_bleed'),
    ({'noise': 500}, 'noise_floor_exceeded'),
    ({'clipped': True}, 'clipped_input'),
    ({'reference': None}, 'missing_signal'),
    ({'response': None}, 'missing_signal'),
    ({'response': (900, 910)}, 'ambiguous_signal_transient'),
    ({'reference': (200, 600)}, 'reference_window_cuts_signal'),
    ({'reference': (100, 600)}, 'reference_channel_not_isolated'),
    ({'response': (610, 1200)}, 'insufficient_separation'),
])
def test_rejects_contamination_ambiguity_and_missing_signals(tmp_path, case, code):
    from roma.eval.acoustic import AcousticAnalysisError, analyze_wav

    path = fixture_wav(tmp_path / 'private_source.wav', **case)
    with pytest.raises(AcousticAnalysisError, match=f'^{code}$'):
        analyze_wav(path, configuration(), provenance='unverified', approved_input=True)


@pytest.mark.parametrize('provenance', ['synthetic', 'virtual_loopback', 'physical_loopback', 'unverified'])
def test_provenance_declarations_cannot_verify_physical_setup_or_pass_gate(tmp_path, provenance):
    from roma.eval.acoustic import analyze_wav

    result = analyze_wav(fixture_wav(tmp_path / 'input.wav'), configuration(),
                         provenance=provenance, approved_input=True)
    assert result['provenance'] == provenance
    assert result['physical_setup_verified'] is False
    assert result['acceptance_gate_passed'] is False
    assert not any('budget' in field or 'p95' in field for field in result)


def test_cli_is_explicit_offline_and_does_not_echo_paths_or_secrets(tmp_path, monkeypatch):
    path = fixture_wav(tmp_path / 'private-phone-and-token.wav')
    script = Path(__file__).resolve().parents[2] / 'scripts/measure_acoustic_ttfa.py'
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'private-value-do-not-print')
    args = [sys.executable, str(script), '--input', str(path), '--approved-input',
            '--provenance', 'synthetic', '--reference-channel', '0', '--speaker-channel', '1',
            '--reference-window-ms', '200', '700', '--noise-window-ms', '0', '100',
            '--signal-rms', '0.04', '--max-noise-rms', '0.005']
    completed = subprocess.run(args, cwd=tmp_path, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['ttfa_ms'] == 300
    assert completed.stderr == ''
    for forbidden in (str(path), 'private-value-do-not-print', 'Traceback'):
        assert forbidden not in completed.stdout + completed.stderr
    denied = subprocess.run([arg for arg in args if arg != '--approved-input'],
                            cwd=tmp_path, text=True, capture_output=True)
    assert denied.returncode == 2
    assert json.loads(denied.stderr)['error'] == 'approved_input_required'
    malformed = subprocess.run([*args, '--unexpected-private-value'],
                              cwd=tmp_path, text=True, capture_output=True)
    assert malformed.returncode == 2
    assert json.loads(malformed.stderr)['error'] == 'invalid_arguments'
    assert 'unexpected-private-value' not in malformed.stderr
    path.write_bytes(b'not a wav')
    invalid = subprocess.run(args, cwd=tmp_path, text=True, capture_output=True)
    assert invalid.returncode == 2
    assert json.loads(invalid.stderr)['error'] == 'input_unavailable_or_invalid'
    assert str(path) not in invalid.stdout + invalid.stderr
    assert 'Traceback' not in invalid.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


@pytest.mark.parametrize('changes,code', [
    ({'reference_channel': True}, 'invalid_channel_layout'),
    ({'speaker_channel': 0}, 'invalid_channel_layout'),
    ({'reference_channel': 2}, 'invalid_channel_layout'),
    ({'signal_rms': float('nan')}, 'invalid_noise_thresholds'),
    ({'max_noise_rms': 0.1}, 'invalid_noise_thresholds'),
    ({'max_noise_rms': 0}, 'invalid_noise_thresholds'),
    ({'frame_ms': 0}, 'invalid_detection_bounds'),
    ({'min_signal_ms': 0}, 'invalid_detection_bounds'),
    ({'guard_ms': 1001}, 'invalid_detection_bounds'),
    ({'noise_window_ms': (200, 300)}, 'invalid_noise_window'),
    ({'reference_window_ms': (201, 700)}, 'invalid_analysis_window'),
    ({'reference_window_ms': (700, 200)}, 'invalid_analysis_window'),
    ({'reference_window_ms': (200, float('inf'))}, 'invalid_analysis_window'),
])
def test_invalid_calibration_bounds_fail_before_input_access(tmp_path, changes, code):
    from roma.eval.acoustic import AcousticAnalysisError, analyze_wav

    with pytest.raises(AcousticAnalysisError, match=f'^{code}$'):
        analyze_wav(tmp_path / 'not_read.wav', configuration(**changes),
                    provenance='synthetic', approved_input=True)


@pytest.mark.parametrize('bad_provenance', ['not_a_setup', None, []])
def test_invalid_provenance_rejected_without_reading_or_echoing_input(tmp_path, bad_provenance):
    from roma.eval.acoustic import AcousticAnalysisError, analyze_wav

    with pytest.raises(AcousticAnalysisError, match=r'^invalid_provenance$'):
        analyze_wav(tmp_path / 'never_read.wav', configuration(),
                    provenance=bad_provenance, approved_input=True)


@pytest.mark.parametrize('case', ['mono', 'truncated', 'oversized', 'too_long', 'symlink', 'directory'])
def test_rejects_unsupported_or_unbounded_inputs(tmp_path, case):
    from roma.eval.acoustic import MAX_FILE_BYTES, AcousticAnalysisError, analyze_wav

    path = fixture_wav(tmp_path / 'private_input.wav')
    if case == 'mono':
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b'\0' * 32000)
    elif case == 'truncated':
        path.write_bytes(path.read_bytes()[:-1])
    elif case == 'oversized':
        with path.open('wb') as stream:
            stream.truncate(MAX_FILE_BYTES + 1)
    elif case == 'too_long':
        raw = bytearray(path.read_bytes())
        struct.pack_into('<I', raw, 40, 16000 * 61 * 4)
        path.write_bytes(raw)
    elif case == 'symlink':
        link = tmp_path / 'link.wav'
        link.symlink_to(path)
        path = link
    elif case == 'directory':
        path = tmp_path
    with pytest.raises(AcousticAnalysisError) as caught:
        analyze_wav(path, configuration(), provenance='synthetic', approved_input=True)
    assert str(path) not in str(caught.value)


@pytest.mark.parametrize('rate,delay', [(8000, 50), (16000, 300), (44100, 500), (48000, 700)])
def test_known_delay_across_supported_sample_rates(tmp_path, rate, delay):
    from roma.eval.acoustic import analyze_wav

    path = fixture_wav(tmp_path / 'calibration.wav', rate=rate,
                       response=(600 + delay, 900 + delay), duration_ms=1900)
    result = analyze_wav(path, configuration(), provenance='virtual_loopback', approved_input=True)
    assert result['ttfa_ms'] == delay
    assert result['sample_rate_hz'] == rate


def test_quiet_reference_tail_is_ambiguous_instead_of_an_early_caller_offset(tmp_path):
    from roma.eval.acoustic import AcousticAnalysisError, analyze_wav

    path = fixture_wav(tmp_path / 'quiet_tail.wav', quiet_tail=True)
    with pytest.raises(AcousticAnalysisError, match=r'^ambiguous_reference_boundary$'):
        analyze_wav(path, configuration(), provenance='synthetic', approved_input=True)


def test_declared_reversed_channel_layout_is_used(tmp_path):
    from roma.eval.acoustic import analyze_wav

    path = fixture_wav(tmp_path / 'reversed.wav', swap=True)
    result = analyze_wav(path, configuration(reference_channel=1, speaker_channel=0),
                         provenance='synthetic', approved_input=True)
    assert result['ttfa_ms'] == 300
    assert result['reference_channel'] == 1
    assert result['speaker_channel'] == 0
