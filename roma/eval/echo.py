"""Deterministic real-DSP checks with generated samples only; no device or file audio.

These harmonic/noise probes measure numerical attenuation and preservation, not
speech intelligibility, native listening acceptance, or physical device latency.
"""

from time import perf_counter

from roma.providers.audio.echo import WebRTCEchoCanceller


def synthetic_probe(*, noise_suppression=False, far_kind="voiced"):
    import numpy as np

    if far_kind not in {"voiced", "noise"}:
        raise ValueError("Expected voiced or noise synthetic far-end")
    rate, seconds = 16000, 16
    size = rate * seconds
    t = np.arange(size) / rate
    rng = np.random.default_rng(301)
    if far_kind == "noise":
        far = (
            np.convolve(
                rng.normal(0, 1, size + 6), [0.2, 0.5, 0.7, 1.0, 0.7, 0.5, 0.2], mode="valid"
            )
            * 2700
            * (0.5 + 0.5 * np.sin(2 * np.pi * 1.9 * t) ** 2)
        )
    else:
        phase = (
            2
            * np.pi
            * np.cumsum(
                210 + 45 * np.sin(2 * np.pi * 0.3 * t) + 20 * np.sin(2 * np.pi * 1.1 * t)
            )
            / rate
        )
        far = (
            sum(np.sin(h * phase) / h for h in range(1, 14))
            * 3000
            * (0.1 + 0.9 * np.sin(2 * np.pi * 1.9 * t) ** 2)
        )
    phase = (
        2
        * np.pi
        * np.cumsum(160 + 35 * np.sin(2 * np.pi * 0.7 * t) + 15 * np.sin(2 * np.pi * 1.3 * t))
        / rate
    )
    near = (
        sum(np.sin(h * phase) / h for h in range(1, 14))
        * 3500
        * (0.2 + 0.8 * np.sin(2 * np.pi * 2.3 * t) ** 2)
    )
    # 0..6s echo only; 6..12s double talk; 12..16s near-end plus echo tail.
    near[: 6 * rate] = 0
    far[12 * rate :] = 0
    far = np.clip(far, -20000, 20000).astype("<i2")
    echo = np.zeros(size)
    echo[640:] = 0.55 * far[:-640]
    echo[960:] += 0.2 * far[:-960]
    mixed = np.clip(echo + near, -32768, 32767).astype("<i2")
    now = [0.0]
    aec = WebRTCEchoCanceller(
        stream_delay_ms=40, noise_suppression=noise_suppression, clock=lambda: now[0]
    )
    aec.preflight()
    output, processing_ms = [], []
    for start in range(0, size, 320):
        now[0] = start / rate
        began = perf_counter()
        aec.render(far[start : start + 320].tobytes(), rate, now[0])
        clean = aec.process(mixed[start : start + 320].tobytes(), now[0])
        processing_ms.append((perf_counter() - began) * 1000)
        output.append(np.frombuffer(clean, dtype="<i2").copy())
    output = np.concatenate(output).astype(float)

    def energy(samples):
        return float(np.mean(np.asarray(samples, dtype=float) ** 2))

    def erle(begin, end):
        section = slice(int(begin * rate), int(end * rate))
        return float(
            10 * np.log10(max(1e-9, energy(echo[section])) / max(1e-9, energy(output[section])))
        )

    def preservation(begin, end):
        begin, end = int(begin * rate), int(end * rate)
        correlations = [
            float(np.corrcoef(near[begin : end - delay], output[begin + delay : end])[0, 1])
            for delay in range(321)
        ]
        return {
            "energy_ratio": energy(output[begin:end]) / energy(near[begin:end]),
            "lag_aligned_correlation": max(correlations),
            "lag_samples": int(np.argmax(correlations)),
        }

    windows = [erle(i / 5, (i + 1) / 5) for i in range(1, 30)]
    # This threshold indicator includes nonlinear suppression and is not a
    # measurement of linear adaptive-filter convergence or native turn latency.
    sustained = next(
        (
            (index + 1) / 5
            for index in range(len(windows) - 2)
            if min(windows[index : index + 3]) >= 10
        ),
        None,
    )
    return {
        "far_kind": far_kind,
        "noise_suppression": noise_suppression,
        "cold_200_to_700ms_erle_db": erle(0.2, 0.7),
        "late_4_to_6s_erle_db": erle(4, 6),
        "late_4_to_6s_min_200ms_erle_db": min(windows[-10:]),
        "first_three_200ms_windows_ge_10db_at_s": sustained,
        "double_talk_8_to_12s": preservation(8, 12),
        "near_only_14_to_16s": preservation(14, 16),
        "render_and_process_frame_p95_ms": float(np.quantile(processing_ms, 0.95)),
        "reference_overflows": aec.reference.overflows,
        "capture_discontinuities": aec.discontinuities,
    }


def report():
    return {
        "schema": "roma.synthetic-echo.v1",
        "backend": "pywebrtc-audio==0.2.0",
        "seed": 301,
        "sample_rate": 16000,
        "frame_ms": 20,
        "echo_taps_ms_gain": [[40, 0.55], [60, 0.2]],
        "scope": "synthetic DSP only; no listening, physical latency or speech-quality gate",
        "probes": [
            synthetic_probe(noise_suppression=ns, far_kind=kind)
            for kind in ("voiced", "noise")
            for ns in (False, True)
        ],
    }
