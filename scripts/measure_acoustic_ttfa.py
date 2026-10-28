"""Analyze one approved existing stereo PCM16 loopback WAV; never records audio.

Channels must isolate caller reference and speaker response. Supply a silent noise
window before the reference window, which must enclose the whole isolated stimulus
with silent guards. RMS thresholds use full-scale units (0..1); they are calibration
inputs, not approved SLOs. All window boundaries align to the selected frame size.
Output contains scalar energy-boundary estimates only. A physical-loopback label
is a declaration, not verified hardware, exact audibility, speech or gate evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roma.eval.acoustic import (
    PROVENANCE,
    AcousticAnalysisError,
    AcousticConfig,
    analyze_wav,
)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise AcousticAnalysisError('invalid_arguments')


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--approved-input', action='store_true',
                        help='Declare this existing WAV approved for local offline analysis.')
    parser.add_argument('--provenance', choices=sorted(PROVENANCE), required=True)
    parser.add_argument('--reference-channel', type=int, choices=(0, 1), required=True)
    parser.add_argument('--speaker-channel', type=int, choices=(0, 1), required=True)
    parser.add_argument('--reference-window-ms', type=float, nargs=2, required=True,
                        metavar=('START', 'END'))
    parser.add_argument('--noise-window-ms', type=float, nargs=2, required=True,
                        metavar=('START', 'END'))
    parser.add_argument('--signal-rms', type=float, required=True)
    parser.add_argument('--max-noise-rms', type=float, required=True)
    parser.add_argument('--frame-ms', type=int, choices=(5, 10, 20), default=10)
    parser.add_argument('--min-signal-ms', type=int, default=30)
    parser.add_argument('--guard-ms', type=int, default=20)
    try:
        args = parser.parse_args(argv)
        config = AcousticConfig(
            reference_channel=args.reference_channel, speaker_channel=args.speaker_channel,
            reference_window_ms=tuple(args.reference_window_ms),
            noise_window_ms=tuple(args.noise_window_ms), signal_rms=args.signal_rms,
            max_noise_rms=args.max_noise_rms, frame_ms=args.frame_ms,
            min_signal_ms=args.min_signal_ms, guard_ms=args.guard_ms,
        )
        report = analyze_wav(args.input, config, provenance=args.provenance,
                             approved_input=args.approved_input)
    except AcousticAnalysisError as error:
        print(json.dumps({'error': str(error), 'acceptance_gate_passed': False}), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
