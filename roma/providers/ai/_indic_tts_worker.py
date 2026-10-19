"""Isolated direct inference process. Invoked only by the explicitly selected local adapter."""

import importlib.metadata
import json
import math
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


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
    tokenizer.text_to_ids(text)
    if any(
        unicodedata.category(char).startswith(("L", "M", "N"))
        for char in tokenizer.not_found_characters
    ):
        raise ValueError("Model vocabulary does not support the requested text")


def main() -> None:
    payload = json.loads(sys.stdin.buffer.read(65537))
    if (
        importlib.metadata.version(payload["runtime_distribution"])
        != payload["runtime_version"]
    ):
        raise RuntimeError("Installed TTS version differs from the manifest")
    # Heavy optional imports stay in the isolated runtime, never in Roma's app process.
    import numpy as np
    from scipy.signal import resample_poly
    from TTS.utils.synthesizer import Synthesizer

    with _runtime_paths(payload["paths"]) as paths:
        synth = Synthesizer(
            **paths,
            tts_languages_file=None,
            encoder_checkpoint="",
            encoder_config="",
            use_cuda=False,
        )
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
    pcm = (np.clip(pcm, -1, 1) * 32767).round().astype("<i2")
    Path(payload["output"]).write_bytes(pcm.tobytes())


if __name__ == "__main__":
    main()
