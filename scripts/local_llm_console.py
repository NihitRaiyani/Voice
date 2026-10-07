"""Level 2 console: python scripts/local_llm_console.py --provider mock."""

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime

from roma.core.local_llm_config import load_local_llm_settings
from roma.domain.appointments.timeresolve import IST
from roma.domain.conversation.prompts import opening_line
from roma.domain.conversation.state import CallState
from roma.domain.safety import safe_output
from roma.providers.ai.registry import build_llm_provider
from roma.services.text_conversation_service import TextConversationService


async def run(args: argparse.Namespace) -> None:
    settings = load_local_llm_settings(args.profile, provider=args.provider)
    service = TextConversationService(
        build_llm_provider(settings),
        max_tokens=settings.local_llm_max_new_tokens,
        temperature=settings.local_llm_temperature,
    )
    state = CallState(call_sid="local-text-lab")
    started = time.monotonic()
    print("TEXT LAB: synthetic callers only; no appointment commits. /quit exits.")
    print(safe_output(opening_line(None)))
    while True:
        try:
            text = await asyncio.to_thread(input, "Caller> ")
        except EOFError:
            break
        if text.strip() == "/quit":
            break
        if not text.strip():
            continue
        trace = await service.turn(
            state, text, now=datetime.now(IST), elapsed_secs=time.monotonic() - started
        )
        print(json.dumps(asdict(trace), ensure_ascii=False, indent=2))
        if state.lead_wants_out or state.elapsed_secs >= 300:
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "qwen3_transformers", "qwen3_mlx"))
    parser.add_argument("--profile", help="non-secret local model profile JSON")
    asyncio.run(run(parser.parse_args()))
