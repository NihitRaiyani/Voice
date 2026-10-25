"""Isolated direct inference process. Invoked only by the explicitly selected local adapter."""

import importlib.metadata
import json
import math
import os
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # Script execution uses the isolated interpreter, without importing Roma.
    from ._indic_tts_protocol import (
        MAX_FRAME_BYTES,
        RESIDENT_PROFILE,
        decode_frame,
        encode_frame,
    )
except ImportError:
    # Script-mode fallback imports the same verified sibling module.
    from _indic_tts_protocol import (  # type: ignore[no-redef]
        MAX_FRAME_BYTES,
        RESIDENT_PROFILE,
        decode_frame,
        encode_frame,
    )


@contextmanager
def _runtime_paths(paths: dict[str, str]) -> Iterator[dict[str, str]]:
    # Coqui 0.22 initializes SpeakerManager from the config, ignoring the
    # constructor's tts_speakers_file override. Release configs contain legacy
    # relative paths. Rebind only these references in an ephemeral copy; never
    # rewrite the manifest-verified artifact or change its cache identity.
    config = json.loads(Path(paths["tts_config_path"]).read_text())
    config["speakers_file"] = paths["tts_speakers_file"]
    if isinstance(config.get("model_args"), dict):
        config["model_args"]["speakers_file"] = paths["tts_speakers_file"]
    with tempfile.TemporaryDirectory(prefix="roma-tts-config-") as directory:
        runtime_config = Path(directory) / "config.json"
        runtime_config.write_text(json.dumps(config))
        yield {**paths, "tts_config_path": str(runtime_config)}


def _validate_vocabulary(tokenizer, text: str) -> None:
    # The installed tokenizer applies the model's cleaner and records characters
    # its encoder would silently discard. Losing lexical content can turn an
    # approved safety replacement into unrelated speech, so fail before synthesis.
    tokenizer.not_found_characters = []
    tokenizer.text_to_ids(text)
    if any(
        unicodedata.category(char).startswith(("L", "M", "N"))
        for char in tokenizer.not_found_characters
    ):
        raise ValueError("Model vocabulary does not support the requested text")


def _load_synth(payload):
    if (
        importlib.metadata.version(payload["runtime_distribution"])
        != payload["runtime_version"]
    ):
        raise RuntimeError("Installed TTS version differs from the manifest")
    # Heavy optional imports stay in the isolated runtime, never in Roma's app process.
    from TTS.utils.synthesizer import Synthesizer

    with _runtime_paths(payload["paths"]) as paths:
        synth = Synthesizer(
            **paths,
            tts_languages_file=None,
            encoder_checkpoint="",
            encoder_config="",
            use_cuda=False,
        )
    return synth


def _synthesize(synth, payload, *, resident=False):
    import numpy as np
    from scipy.signal import resample_poly

    _validate_vocabulary(synth.tts_model.tokenizer, payload["text"])
    wav = np.asarray(
        synth.tts(payload["text"], speaker_name=payload["voice"], style_wav=""),
        dtype=np.float32,
    )
    rate = int(synth.output_sample_rate)
    target = payload["sample_rate"]
    if wav.ndim != 1 or not 0 < len(wav) <= rate * payload["max_audio_secs"]:
        raise ValueError("Empty, multichannel or oversized model audio")
    if not np.isfinite(wav).all() or not 8000 <= rate <= 96000:
        raise ValueError("Invalid model output")
    divisor = math.gcd(rate, target)
    pcm = resample_poly(wav, target // divisor, rate // divisor)
    if resident:
        _edge_fade(pcm, target)
    if not np.isfinite(pcm).all():
        raise ValueError("Invalid resampled audio")
    pcm = (np.clip(pcm, -1, 1) * 32767).round().astype("<i2")
    Path(payload["output"]).write_bytes(pcm.tobytes())


def _edge_fade(samples, sample_rate):
    """Deterministic 15 ms raised-cosine edges; length/sample rate are unchanged."""
    frames = min(round(sample_rate * 0.015), len(samples) // 2)
    for i in range(frames):
        gain = 0.5 * (1 - math.cos(math.pi * i / max(1, frames - 1)))
        samples[i] *= gain
        samples[-1 - i] *= gain


def serve(input_stream, output_stream):
    synth = None
    voice = None
    previous_id = 0
    while True:
        frame = input_stream.readline(MAX_FRAME_BYTES + 1)
        if not frame:
            return
        # Malformed/oversized input ends the process without exposing its content.
        payload = decode_frame(frame)
        request_id = payload["id"]
        if request_id <= previous_id:
            raise ValueError("Non-monotonic Indic-TTS request ID")
        previous_id = request_id
        if payload.get("op") == "load" and synth is None:
            if set(payload) != {
                "id",
                "turn_epoch",
                "op",
                "paths",
                "runtime_distribution",
                "runtime_version",
                "voice",
                "profile",
            }:
                raise ValueError("Invalid Indic-TTS load shape")
            if (
                payload["turn_epoch"] is not None
                or payload["profile"] != RESIDENT_PROFILE
                or payload["voice"] not in {"female", "male"}
            ):
                raise ValueError("Invalid Indic-TTS profile")
            synth = _load_synth(payload)
            voice = payload["voice"]
            status = "ready"
        elif payload.get("op") == "synthesize" and synth is not None:
            if (
                set(payload)
                != {"id", "turn_epoch", "op", "text", "sample_rate", "max_audio_secs", "output"}
                or type(payload["text"]) is not str
                or not payload["text"].strip()
                or len(payload["text"].encode()) > 8192
                or type(payload["sample_rate"]) is not int
                or payload["sample_rate"] not in {8000, 16000}
                or type(payload["max_audio_secs"]) not in {int, float}
                or not 0 < payload["max_audio_secs"] <= 60
                or type(payload["output"]) is not str
                or not Path(payload["output"]).is_absolute()
            ):
                raise ValueError("Invalid Indic-TTS synthesis shape")
            _synthesize(synth, {**payload, "voice": voice}, resident=True)
            status = "ok"
        else:
            raise ValueError("Invalid Indic-TTS operation")
        output_stream.write(
            encode_frame(
                {"id": request_id, "status": status, "turn_epoch": payload["turn_epoch"]}
            )
        )
        output_stream.flush()


def main() -> None:
    if "--resident" in sys.argv:
        # Reserve a protocol FD, then suppress Python/native-library stdout/stderr.
        # Model libraries sometimes print progress during construction/inference.
        with os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0) as protocol:
            with open(os.devnull, "wb") as null:
                os.dup2(null.fileno(), sys.stdout.fileno())
                os.dup2(null.fileno(), sys.stderr.fileno())
                serve(sys.stdin.buffer, protocol)
        return
    payload = json.loads(sys.stdin.buffer.read(MAX_FRAME_BYTES + 1))
    synth = _load_synth(payload)
    _synthesize(synth, payload)


if __name__ == "__main__":
    main()
