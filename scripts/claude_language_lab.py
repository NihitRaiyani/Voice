"""Separate multilingual lab: mock by default, paid/native review explicitly deferred."""

import argparse
import asyncio
import json
from pathlib import Path

from roma.core.claude_llm_settings import ClaudeLLMSettings
from roma.core.private_files import open_private
from roma.eval.openai_language import evaluate_case, score_report
from roma.providers.ai.registry import build_llm_provider

ROOT = Path(__file__).resolve().parents[1]


async def run(args):
    cases = json.loads((ROOT / "benchmarks/llm/language_cases.json").read_text())
    policy = json.loads((ROOT / "benchmarks/llm/language_policy.json").read_text())
    if args.score:
        artifact = json.loads(await asyncio.to_thread(Path(args.score).read_text))
        real = artifact.get("provider") == "claude" and all(
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
    if args.provider == "claude" and not args.allow_paid_api:
        raise SystemExit(
            "Claude generation requires --allow-paid-api; use mocks for offline checks"
        )
    cfg = ClaudeLLMSettings(llm_provider=args.provider)
    results = []
    charged = 0.0
    for case in cases:
        # Fresh budget/client per isolated synthetic case, not a claimed continuous call.
        if args.provider == "claude" and 2 - charged < 0.01:
            break
        case_cfg = (
            cfg.model_copy(
                update={
                    "anthropic_call_spend_cap_inr": min(
                        cfg.anthropic_call_spend_cap_inr, 2 - charged
                    )
                }
            )
            if args.provider == "claude"
            else cfg
        )
        provider = build_llm_provider(case_cfg)
        try:
            result = await evaluate_case(provider, case)
            results.append(result)
            charged += sum(row.get("charged_inr", 0) for row in result.get("metrics", []))
            if args.provider == "claude" and charged >= 2:
                break  # Explicit lab ceiling; no automatic larger-model or OpenAI fallback.
        finally:
            if hasattr(provider, "aclose"):
                await provider.aclose()
    artifact = {
        "provider": args.provider,
        "effort": "low" if args.provider == "claude" else None,
        "thinking": "disabled" if args.provider == "claude" else None,
        "temperature_sent": False,
        "charged_inr": charged,
        "expected_cases": len(cases),
        "complete": len(results) == len(cases),
        "requested_model": cfg.anthropic_model if args.provider == "claude" else "mock",
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
    parser.add_argument("--provider", choices=["mock", "claude"], default="mock")
    parser.add_argument("--allow-paid-api", action="store_true")
    parser.add_argument("--output", default="var/roma/claude-lab/review.json")
    parser.add_argument("--score")
    asyncio.run(run(parser.parse_args()))
