"""Explicit loopback-only speech clients; no retry, redirect, proxy or fallback."""

from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import ValidationError

from roma.core.ai_contracts import (
    ProviderUnavailable,
    STTRequest,
    STTResult,
    TTSRequest,
    TTSResult,
)
from roma.core.serving_settings import SpeechServingSettings
from roma.providers.reliability import remaining_timeout
from roma.serving.protocol import (
    STTBody,
    STTResponse,
    TTSBody,
    TTSIdentityResponse,
    TTSResponse,
    stt_payload,
    tts_payload,
)


class _SpeechHTTP:
    def __init__(
        self, settings: SpeechServingSettings, *, client: httpx.AsyncClient | None = None
    ):
        self.settings = settings
        self._key = settings.require_key()
        self._base_url = SpeechServingSettings.loopback_url(settings.speech_service_url)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=settings.speech_service_max_inflight,
                max_keepalive_connections=settings.speech_service_max_inflight,
            ),
            timeout=settings.speech_service_timeout_secs,
        )
        self._active = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ready(self) -> bool:
        """Probe authenticated warm readiness; unavailable/invalid/overloaded is false."""
        try:
            response = await self._request("GET", "/ready")
            return response.get("ready") is True
        except ProviderUnavailable:
            return False

    async def _post(self, path: str, payload: dict) -> dict:
        return await self._request("POST", path, payload)

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if self._active >= self.settings.speech_service_max_inflight:
            raise ProviderUnavailable("Local speech client is busy")
        encoded = (
            json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
        )
        if len(encoded) > self.settings.speech_service_max_body_bytes:
            raise ValueError("Speech payload exceeds byte bound")
        timeout = remaining_timeout(self.settings.speech_service_timeout_secs)
        if timeout <= 0:
            raise ProviderUnavailable("Local speech request timed out")
        self._active += 1
        try:
            async with asyncio.timeout(timeout):
                async with self._client.stream(
                    method,
                    self._base_url + path,
                    content=encoded,
                    headers={
                        "Authorization": "Bearer " + self._key,
                        "Content-Type": "application/json",
                    },
                    timeout=timeout,
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise ProviderUnavailable("Local speech service unavailable")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self.settings.speech_service_max_body_bytes:
                            raise ProviderUnavailable(
                                "Local speech response exceeds byte bound"
                            )
                        body.extend(chunk)
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("Invalid speech response")
                    return data
        except (TimeoutError, httpx.HTTPError, ValueError):
            raise ProviderUnavailable("Local speech request failed") from None
        finally:
            self._active -= 1


class HTTPSTTProvider(_SpeechHTTP):
    async def transcribe(self, request: STTRequest) -> STTResult:
        body = stt_payload(request)
        STTBody.model_validate(body).contract(self.settings)
        data = await self._post("/v1/stt", body)
        try:
            result = STTResponse.model_validate(data).contract()
            if result.turn_epoch != request.turn_epoch:
                raise ValueError("Stale speech response")
            return result
        except (ValueError, ValidationError):
            raise ProviderUnavailable("Invalid local STT response") from None


class HTTPTTSProvider(_SpeechHTTP):
    async def cache_identity(self, request: TTSRequest) -> str:
        body = tts_payload(request)
        TTSBody.model_validate(body).contract(self.settings)
        data = await self._post("/v1/tts/identity", body)
        try:
            return TTSIdentityResponse.model_validate(data).identity
        except (ValueError, ValidationError):
            raise ProviderUnavailable("Invalid local TTS identity") from None

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        body = tts_payload(request)
        TTSBody.model_validate(body).contract(self.settings)
        data = await self._post("/v1/tts", body)
        try:
            result = TTSResponse.model_validate(data).contract(
                self.settings.speech_service_max_audio_secs
            )
            if result.turn_epoch != request.turn_epoch or result.format != request.format:
                raise ValueError("Invalid speech response identity")
            return result
        except (ValueError, ValidationError):
            raise ProviderUnavailable("Invalid local TTS response") from None
