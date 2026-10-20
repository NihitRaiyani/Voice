"""Synthetic text console. Default mocks; paid API needs explicit --allow-paid-api."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

from roma.core.claude_llm_settings import ClaudeLLMSettings
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.state import CallState
from roma.providers.ai.registry import build_llm_provider
from roma.services.text_conversation_service import TextConversationService


async def run(args):
    if args.provider == "claude" and not args.allow_paid_api:
        raise SystemExit(
            "Claude requests require --allow-paid-api; use mocks for offline checks"
        )
    cfg = ClaudeLLMSettings(llm_provider=args.provider)
    provider = build_llm_provider(cfg)
    service = TextConversationService(provider, temperature=0.7)
    state = CallState(call_sid="synthetic-" + uuid4().hex, stage="open")
    print(
        "Synthetic text-only console. No carrier calls or appointment commits. Type /quit to exit."
    )
    try:
        while True:
            try:
                text = await asyncio.to_thread(input, "Caller> ")
            except EOFError:
                break
            if text.strip() == "/quit":
                break
            trace = await service.turn(state, text, now=datetime.now(IST))
            print(json.dumps(asdict(trace), ensure_ascii=False, indent=2))
    finally:
        if hasattr(provider, "aclose"):
            await provider.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["mock", "claude"], default="mock")
    parser.add_argument("--allow-paid-api", action="store_true")
    asyncio.run(run(parser.parse_args()))
