import math

import pytest
from roma.domain.audio.pcm import AudioFormatError, pack
from roma.providers.ai.silero import SileroFrameProbability

np = pytest.importorskip("numpy")


class Model:
    def __init__(self, result=0.75):
        self.result = result
        self.calls = []
        self.resets = 0

    def __call__(self, samples, sample_rate):
        self.calls.append((samples.copy(), sample_rate))
        return np.array([[self.result]], dtype=np.float32)

    def reset_states(self):
        self.resets += 1


def test_lazy_model_and_reset_with_signed_pcm_scaling_at_exact_16khz():
    model = Model()
    loads = []

    def factory():
        loads.append(True)
        return model

    adapter = SileroFrameProbability(model_factory=factory)
    adapter.reset()
    assert not loads
    pcm = pack([-32768, -1, 0, 32767] * 128)
    assert adapter.probability(pcm) == 0.75
    assert adapter.probability(pcm) == 0.75
    assert loads == [True]
    samples, rate = model.calls[0]
    assert rate == 16000 and samples.dtype == np.float32 and samples.shape == (512,)
    assert samples[:4].tolist() == [-1.0, -1 / 32768, 0.0, 32767 / 32768]
    adapter.reset()
    assert model.resets == 1


@pytest.mark.parametrize(
    "metadata", [{"sample_rate": 8000}, {"channels": 2}, {"sample_width": 4}]
)
def test_invalid_format_does_not_initialize_or_infer(metadata):
    loads = []
    adapter = SileroFrameProbability(model_factory=lambda: loads.append(True))
    with pytest.raises(AudioFormatError):
        adapter.probability(bytes(1024), **metadata)
    with pytest.raises(AudioFormatError):
        adapter.probability(bytes(640))
    assert not loads


@pytest.mark.parametrize("probability", [math.nan, math.inf, -0.1, 1.1])
def test_invalid_model_probability_is_not_silently_converted_to_silence(probability):
    adapter = SileroFrameProbability(model_factory=lambda: Model(probability))
    with pytest.raises(AudioFormatError):
        adapter.probability(bytes(1024))


def test_inference_failure_propagates_for_application_failure_policy():
    def unavailable():
        raise RuntimeError("local model unavailable")

    adapter = SileroFrameProbability(model_factory=unavailable)
    with pytest.raises(RuntimeError, match="local model unavailable"):
        adapter.probability(bytes(1024))


def test_installed_bundled_silero_silence_smoke_without_downloads():
    from importlib.resources import files

    pytest.importorskip("onnxruntime")
    pytest.importorskip("pipecat.audio.vad.silero")
    assert files("pipecat.audio.vad.data").joinpath("silero_vad.onnx").is_file()
    adapter = SileroFrameProbability()
    probability = adapter.probability(bytes(1024))
    assert 0 <= probability <= 1
    assert probability < 0.35
    adapter.reset()
    assert adapter.probability(bytes(1024)) == probability
