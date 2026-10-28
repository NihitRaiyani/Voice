"""Bounded offline loopback energy timing; never proves acoustic or native acceptance.

One isolated caller stimulus and one response must use separate stereo channels.
Threshold crossings estimate audible boundaries at frame resolution; they do not
identify speech, prove channel isolation, or distinguish a delayed echo from a reply.
A physical-loopback label is an operator declaration, not verified hardware evidence.
"""
from __future__ import annotations

import math
import os
import stat
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

MAX_DURATION_SECONDS = 60
MAX_FILE_BYTES = 60 * 48000 * 4 + 65536
PROVENANCE = frozenset({'synthetic', 'virtual_loopback', 'physical_loopback', 'unverified'})


class AcousticAnalysisError(ValueError):
    """A fixed safe code; never a filename, waveform or underlying exception."""


@dataclass(frozen=True)
class AcousticConfig:
    reference_channel: int
    speaker_channel: int
    reference_window_ms: tuple[float, float]
    noise_window_ms: tuple[float, float]
    signal_rms: float
    max_noise_rms: float
    frame_ms: int = 10
    min_signal_ms: int = 30
    guard_ms: int = 20

    def validate(self):
        if type(self.reference_channel) is not int or type(self.speaker_channel) is not int \
                or {self.reference_channel, self.speaker_channel} != {0, 1}:
            raise AcousticAnalysisError('invalid_channel_layout')
        if type(self.frame_ms) is not int or self.frame_ms not in (5, 10, 20) \
                or type(self.min_signal_ms) is not int or type(self.guard_ms) is not int \
                or not self.frame_ms <= self.min_signal_ms <= 1000 \
                or not self.frame_ms <= self.guard_ms <= 1000:
            raise AcousticAnalysisError('invalid_detection_bounds')
        for value in (self.signal_rms, self.max_noise_rms):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise AcousticAnalysisError('invalid_noise_thresholds')
        if not 0 < self.max_noise_rms < self.signal_rms < 1:
            raise AcousticAnalysisError('invalid_noise_thresholds')
        for window in (self.reference_window_ms, self.noise_window_ms):
            if not isinstance(window, (tuple, list)) or len(window) != 2 \
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in window) \
                    or not 0 <= window[0] < window[1] <= MAX_DURATION_SECONDS * 1000 \
                    or any(v % self.frame_ms for v in window):
                raise AcousticAnalysisError('invalid_analysis_window')
        if self.noise_window_ms[1] > self.reference_window_ms[0] \
                or self.noise_window_ms[1] - self.noise_window_ms[0] < self.guard_ms:
            raise AcousticAnalysisError('invalid_noise_window')


def _read_pcm(path):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise AcousticAnalysisError('invalid_input_file')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream, wave.open(stream, 'rb') as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            if wav.getnchannels() != 2 or wav.getsampwidth() != 2 \
                    or wav.getcomptype() != 'NONE' or rate not in (8000, 16000, 24000, 32000, 44100, 48000):
                raise AcousticAnalysisError('unsupported_wav_format')
            if not 0 < frames <= rate * MAX_DURATION_SECONDS:
                raise AcousticAnalysisError('invalid_recording_duration')
            raw = wav.readframes(frames + 1)
            if len(raw) != frames * 4:
                raise AcousticAnalysisError('truncated_wav')
        pcm = array('h', raw)
        if sys.byteorder != 'little':
            pcm.byteswap()
        return pcm, rate, frames
    except AcousticAnalysisError:
        raise
    except (OSError, EOFError, wave.Error, ValueError):
        raise AcousticAnalysisError('input_unavailable_or_invalid') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _signal_bounds(energies, threshold, minimum_frames):
    active = [index for index, value in enumerate(energies) if value >= threshold]
    if not active:
        raise AcousticAnalysisError('missing_signal')
    runs = []
    start = previous = active[0]
    for index in active[1:]:
        if index != previous + 1:
            runs.append((start, previous + 1))
            start = index
        previous = index
    runs.append((start, previous + 1))
    sustained = [(start, end) for start, end in runs if end - start >= minimum_frames]
    if not sustained or sustained[0][0] != active[0] or sustained[-1][1] != active[-1] + 1:
        raise AcousticAnalysisError('ambiguous_signal_transient')
    return sustained[0][0], sustained[-1][1]


def analyze_wav(path: Path, config: AcousticConfig, *, provenance: str, approved_input=False):
    """Return scalar estimates only; input authorization and provenance are explicit."""
    if approved_input is not True:
        raise AcousticAnalysisError('approved_input_required')
    if not isinstance(provenance, str) or provenance not in PROVENANCE:
        raise AcousticAnalysisError('invalid_provenance')
    config.validate()
    pcm, rate, sample_frames = _read_pcm(path)
    if rate * config.frame_ms % 1000:
        raise AcousticAnalysisError('unsupported_frame_alignment')
    duration_ms = sample_frames * 1000 / rate
    if config.reference_window_ms[1] > duration_ms:
        raise AcousticAnalysisError('analysis_window_outside_recording')
    frame_samples = rate * config.frame_ms // 1000
    energies: list[list[float]] = [[], []]
    for start in range(0, len(pcm), frame_samples * 2):
        frame = pcm[start:start + frame_samples * 2]
        if any(abs(value) >= 32760 for value in frame):
            raise AcousticAnalysisError('clipped_input')
        for channel in (0, 1):
            values = frame[channel::2]
            energies[channel].append(math.sqrt(sum(v * v for v in values) / len(values)) / 32768)
    reference = energies[config.reference_channel]
    speaker = energies[config.speaker_channel]
    noise_start, noise_end = (int(v / config.frame_ms) for v in config.noise_window_ms)
    if max(reference[noise_start:noise_end] + speaker[noise_start:noise_end]) > config.max_noise_rms:
        raise AcousticAnalysisError('noise_floor_exceeded')
    ref_start, ref_end = (int(v / config.frame_ms) for v in config.reference_window_ms)
    if any(value > config.max_noise_rms for value in reference[:ref_start] + reference[ref_end:]):
        raise AcousticAnalysisError('reference_channel_not_isolated')
    minimum_frames = math.ceil(config.min_signal_ms / config.frame_ms)
    caller_start, caller_end = _signal_bounds(reference[ref_start:ref_end], config.signal_rms, minimum_frames)
    caller_start, caller_end = caller_start + ref_start, caller_end + ref_start
    guard_frames = math.ceil(config.guard_ms / config.frame_ms)
    if caller_start - ref_start < guard_frames or ref_end - caller_end < guard_frames:
        raise AcousticAnalysisError('reference_window_cuts_signal')
    if any(value > config.max_noise_rms
           for value in reference[ref_start:caller_start] + reference[caller_end:ref_end]):
        raise AcousticAnalysisError('ambiguous_reference_boundary')
    if any(value > config.max_noise_rms for value in speaker[:caller_end]):
        raise AcousticAnalysisError('speaker_overlap_or_bleed')
    speaker_start, _ = _signal_bounds(speaker, config.signal_rms, minimum_frames)
    if any(value > config.max_noise_rms for value in speaker[caller_end:speaker_start]):
        raise AcousticAnalysisError('ambiguous_speaker_onset')
    if speaker_start - caller_end < guard_frames:
        raise AcousticAnalysisError('insufficient_separation')
    return {
        'schema_version': 1,
        'measurement': 'thresholded_energy_onset_estimate',
        'provenance': provenance,
        'physical_setup_verified': False,
        'acceptance_gate_passed': False,
        'caller_offset_ms': caller_end * config.frame_ms,
        'speaker_onset_ms': speaker_start * config.frame_ms,
        'ttfa_ms': (speaker_start - caller_end) * config.frame_ms,
        'resolution_ms': config.frame_ms,
        'sample_rate_hz': rate,
        'duration_ms': duration_ms,
        'signal_rms_threshold': config.signal_rms,
        'max_noise_rms': config.max_noise_rms,
        'min_signal_ms': config.min_signal_ms,
        'guard_ms': config.guard_ms,
        'reference_channel': config.reference_channel,
        'speaker_channel': config.speaker_channel,
        'reference_window_start_ms': config.reference_window_ms[0],
        'reference_window_end_ms': config.reference_window_ms[1],
        'noise_window_start_ms': config.noise_window_ms[0],
        'noise_window_end_ms': config.noise_window_ms[1],
    }
