"""Offline four-call streaming/CPU heartbeat exercise. No audio capture or models."""

import asyncio
import hashlib
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.core.ai_contracts import LLMChunk  # noqa: E402
from roma.core.cpu_worker import CpuWorker  # noqa: E402
from roma.domain.conversation.stage import ConversationStage  # noqa: E402
from roma.domain.conversation.state import CallState  # noqa: E402
from roma.eval.tts import SyntheticPCMProvider  # noqa: E402
from roma.services.safety_service import SafetyService  # noqa: E402
from roma.services.speech_input import SpeechTranscript  # noqa: E402
from roma.services.streaming_text import StreamConfig  # noqa: E402
from roma.services.text_conversation_service import TextConversationService  # noqa: E402
from roma.services.tts_service import SpeechSynthesisService  # noqa: E402
from roma.services.voice_prototype import (  # noqa: E402
    InMemoryVoiceTurnStore,
    TimedLLMProvider,
    VoicePrototype,
)
from starlette.websockets import WebSocket  # noqa: E402


def slow_job(function, args):
    # Deliberate pure-Python CPU contention, not sleep in the event-loop thread.
    until = time.perf_counter() + 0.03
    while time.perf_counter() < until:
        pass
    return function(*args)


class SlowCpu(CpuWorker):
    async def run(self, function, *args):
        return await super().run(slow_job, function, args)


class ScriptedLLM:
    async def generate(self, request):
        yield LLMChunk("हम प्रैक्टिकल स्किल सीखते हैं।")
        await asyncio.sleep(0.2)
        yield LLMChunk("आपको किस विषय में रुचि है?", finish_reason="stop")


class SlowTTS(SyntheticPCMProvider):
    async def synthesize(self, request):
        await asyncio.sleep(0.02)
        return await super().synthesize(request)


async def run_lab():
    cpu = SlowCpu(workers=2, max_pending=8)
    writes = 0
    async def receive():
        return {"type": "websocket.connect"}
    async def send(message):
        nonlocal writes
        writes += 1
    socket = WebSocket({"type": "websocket", "path": "/synthetic", "headers": []}, receive, send)
    await socket.accept()
    running = True
    stalls = []
    async def heartbeat():
        deadline = time.perf_counter() + 0.005
        while running:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            stalls.append(max(0, (now - deadline) * 1000))
            deadline = now + 0.005
            await socket.send_json({"event": "synthetic_heartbeat"})
    heart = asyncio.create_task(heartbeat())
    async def one(index):
        class Speaker:
            first_write_at = None
            async def play(self, result):
                self.first_write_at = time.perf_counter()
                await socket.send_bytes(result.audio)
                await asyncio.sleep(0.005)
        state = CallState(call_sid=f"voice-lab-streaming-{index}")
        state.stage = ConversationStage.VALUE
        safety = SafetyService()
        conversation = TextConversationService(TimedLLMProvider(ScriptedLLM()), safety=safety, dialogue_language="voice_hi")
        synthesis = SpeechSynthesisService(SlowTTS(), safety, model_version="synthetic-pcm-v1")
        voice = VoicePrototype(state, conversation, synthesis, Speaker(), InMemoryVoiceTurnStore(),
                               streaming=StreamConfig(), cpu_worker=cpu)
        result = await voice.respond(SpeechTranscript("Please explain practical classes", "en", 500, "silence", 0,
                                                       speech_end_at=time.perf_counter()))
        return {"call": index, "status": result.status, "timings": result.timings,
                "queues": voice.queue_metrics, "fallbacks": voice.stream_fallbacks}
    try:
        results = await asyncio.gather(*(one(index) for index in range(4)))
    finally:
        running = False
        await heart
        await cpu.aclose()
        await socket.close()
    sources = [ROOT / name for name in (
        "roma/core/async_queue.py", "roma/core/cpu_worker.py", "roma/services/streaming_text.py",
        "roma/services/streaming_voice.py", "roma/services/voice_prototype.py",
        "roma/services/text_conversation_service.py", "roma/services/tts_service.py",
    )]
    return {"schema": "section22-23-offline-v1", "created_at": datetime.now(UTC).isoformat(),
            "synthetic_only": True, "microphone_used": False, "paid_api_calls": False,
            "model_downloads": False, "durable_database": False, "gate_passed": False,
            "advanced_epochs": False, "concurrent_calls": 4, "cpu_workers": 2,
            "cpu_delay_per_job_ms": 30, "scripted_tts_delay_ms": 20,
            "heartbeat_target_interval_ms": 5, "heartbeat_samples": len(stalls),
            "max_event_loop_stall_ms": max(stalls, default=0), "websocket_writes": writes,
            "config": asdict(StreamConfig()),
            "source_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in sources)).hexdigest(),
            "calls": results,
            "limits": "Fake WebSocket I/O, ASR, LLM and PCM only; TTFA is first software write, not acoustic/native capacity or an SLO."}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(asyncio.wait_for(run_lab(), 60)), ensure_ascii=True, indent=2))
