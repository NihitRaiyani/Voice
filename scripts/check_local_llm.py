"""Opt-in trained model smoke; synthetic data, no carrier or database calls."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from roma.core.local_llm_config import load_local_llm_settings
from roma.core.private_files import open_private
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import LLMMessage, LLMRequest
from roma.providers.ai.registry import build_llm_provider
from roma.services.local_llm_prompts import local_dialogue_request
from roma.services.text_conversation_service import TextConversationService, filter_response


async def run(args: argparse.Namespace) -> None:
    settings = load_local_llm_settings(args.profile)
    if settings.llm_provider == "mock":
        raise SystemExit("This opt-in smoke requires a trained local provider profile")
    provider = build_llm_provider(settings)
    request = LLMRequest(
        (
            LLMMessage(
                "system",
                "You are Roma. Ask the caller's city in Hindi-base Hinglish in at most 15 words. Return only the question, no reasoning.",
            ),
            LLMMessage("user", "Mera naam Amit hai."),
        ),
        temperature=0,
        max_tokens=64,
    )
    chunks = [chunk async for chunk in provider.generate(request)]
    metrics = [asdict(c.metrics) for c in chunks if c.metrics]
    if not metrics or not metrics[-1]["generated_tokens"]:
        raise SystemExit("No trained generation metrics/tokens returned")
    raw = "".join(c.text for c in chunks)
    final, safety = filter_response(raw, CallState(stage="discover"))
    cache_checks = []
    if settings.llm_provider == "qwen3_mlx":
        cache_request = local_dialogue_request(
            CallState(stage="value"),
            "What practical skills will I learn?",
            "Explain one approved practical course benefit.",
            max_tokens=64,
            temperature=0,
        )
        # Same token sequence and greedy task, with and without cache. A mismatch
        # fails this opt-in mechanics check; it is not a native-quality score.
        for enabled in (False, True, True):
            settings.local_llm_prefix_cache = enabled
            generated = [c async for c in provider.generate(cache_request)]
            cache_checks.append(
                {
                    "enabled": enabled,
                    "raw": "".join(c.text for c in generated),
                    "metrics": [asdict(c.metrics) for c in generated if c.metrics],
                }
            )
        if (
            len({item["raw"] for item in cache_checks}) != 1
            or not cache_checks[-1]["metrics"][-1]["prefix_cache_hit"]
        ):
            raise SystemExit("Cached/uncached trained decoding check failed")
    service = TextConversationService(provider, temperature=0)
    state = CallState(call_sid="synthetic-trained-smoke", stage="discover")
    trace = await service.turn(
        state, "Amit bol raha hoon.", now=datetime(2026, 10, 7, 10, tzinfo=IST)
    )
    artifact = {
        "provider": settings.llm_provider,
        "model": settings.local_llm_model,
        "raw": raw,
        "final": final,
        "safety": safety,
        "metrics": metrics,
        "turn": asdict(trace),
        "cache_checks": cache_checks,
    }
    with open_private(Path(args.output), "w") as handle:
        handle.write(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "provider": settings.llm_provider,
                "final": final,
                "metrics": metrics,
                "extracted_name": state.lead_name,
                "stage": state.stage,
                "turn_final": trace.final_response,
                "provider_error": trace.provider_error,
                "booking_committed": trace.booking_committed,
                "artifact": args.output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if trace.provider_error or not trace.metrics or state.lead_name != "Amit":
        raise SystemExit(
            "Trained generation ran; controller/extraction smoke failed, inspect the private artifact"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="configs/local-llm-mac.json")
    parser.add_argument("--output", default="var/roma/llm-lab/mac-smoke.json")
    asyncio.run(run(parser.parse_args()))
