"""Lazy local Silero probabilities, without framework endpoint decisions.

Uses the ONNX weights already shipped inside the installed Pipecat package.
No Hub/torch downloader, network fallback or automatic weight installation.
One instance belongs to one contiguous stream and must be reset across gaps.
"""

from collections.abc import Callable
from typing import Any

from roma.domain.audio.endpointing import validate_frame, validate_probability


class SileroFrameProbability:
    sample_rate = 16000
    frame_samples = 512
    frame_bytes = 1024

    def __init__(self, *, model_factory: Callable[[], Any] | None = None) -> None:
        self._factory = model_factory or self._bundled_model
        self._model: Any = None

    @staticmethod
    def _bundled_model() -> Any:
        from importlib.resources import files

        from pipecat.audio.vad.silero import SileroOnnxModel

        weights = files("pipecat.audio.vad.data").joinpath("silero_vad.onnx")
        if not weights.is_file():
            raise FileNotFoundError("Installed Pipecat Silero ONNX weights are unavailable")
        return SileroOnnxModel(str(weights), force_onnx_cpu=True)

    def probability(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width: int = 2,
    ) -> float:
        validate_frame(
            pcm, sample_rate=sample_rate, channels=channels, sample_width=sample_width
        )
        import numpy as np

        if self._model is None:
            self._model = self._factory()
        # Explicit byte order: the domain's PCM is signed little-endian.
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        probability = float(self._model(samples, self.sample_rate)[0].item())
        validate_probability(probability)
        return probability

    def reset(self) -> None:
        """Reset recurrent history if initialized; never loads a model to reset it."""
        if self._model is not None:
            self._model.reset_states()
