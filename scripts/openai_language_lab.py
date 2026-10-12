"""Separate multilingual lab: mock by default, paid/native review explicitly deferred."""

import argparse
import asyncio
import json
from pathlib import Path

from roma.core.openai_llm_settings import OpenAILLMSettings
from roma.core.private_files import open_private
from roma.eval.openai_language import evaluate_case, score_report
from roma.providers.ai.registry import build_llm_provider

ROOT = Path(__file__).resolve().parents[1]


async def run(args):
    cases = json.loads((ROOT / "benchmarks/llm/language_cases.json").read_text())
    policy = json.loads((ROOT / "benchmarks/llm/language_policy.json").read_text())
    if args.score:
        artifact = json.loads(await asyncio.to_thread(Path(args.score).read_text))
        real = artifact.get("provider") == "openai" and all(
            len(row.get("metrics", [])) == 1
            and row["metrics"][0].get("usage_known")
            and row["metrics"][0].get("ttft_ms") is not None
            and row.get("finish_reason") in {"stop", "length"}
            for row in artifact["results"]
        )
        result = score_report(artifact["results"], cases, policy, real_model=real)
        print(json.dumps(result, indent=2))
        if result["status"] != "pass":
            raise SystemExit(1)
        return
    if args.provider == "openai" and not args.allow_paid_api:
        raise SystemExit(
            "Paid OpenAI generation is deferred; use mocks while quota is unavailable"
        )
    cfg = OpenAILLMSettings(llm_provider=args.provider)
    results = []
    for case in cases:
        # Fresh budget/client per isolated synthetic case, not a claimed continuous call.
        provider = build_llm_provider(cfg)
        try:
            results.append(await evaluate_case(provider, case))
        finally:
            if hasattr(provider, "aclose"):
                await provider.aclose()
    artifact = {
        "provider": args.provider,
        "requested_model": cfg.openai_model if args.provider == "openai" else "mock",
        "policy": policy,
        "results": results,
        "native_review": "deferred",
        "voice_approval": "deferred",
    }
    with open_private(Path(args.output), "w") as fh:
        fh.write(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(results)} {args.provider} cases; native review deferred.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["mock", "openai"], default="mock")
    parser.add_argument("--allow-paid-api", action="store_true")
    parser.add_argument("--output", default="var/roma/openai-lab/review.json")
    parser.add_argument("--score")
    asyncio.run(run(parser.parse_args()))
