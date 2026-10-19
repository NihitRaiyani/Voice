"""Explicit local-language composition, never a failure-driven/cloud fallback."""

from roma.providers.ai.contracts import STTRequest, STTResult


class LocalLanguageSTT:
    def __init__(self, indic, whisper):
        self.indic, self.whisper = indic, whisper

    async def transcribe(self, request: STTRequest) -> STTResult:
        # IndicConformer does not support English. Smoothing is controller-owned;
        # select the English baseline only for the saved explicit English route.
        provider = (
            self.whisper if request.language_hint in {"en", "en-IN", "en-US"} else self.indic
        )
        return await provider.transcribe(request)
