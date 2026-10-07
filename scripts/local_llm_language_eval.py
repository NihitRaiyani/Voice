"""Generate local lab outputs, then score the completed native-review artifact."""

import argparse
import asyncio
import json
from pathlib import Path

from roma.core.local_llm_config import load_local_llm_settings
from roma.core.private_files import open_private
from roma.eval.local_language import evaluate_case, score_report
from roma.providers.ai.contracts import ProviderUnavailable
from roma.providers.ai.registry import build_llm_provider

ROOT = Path(__file__).resolve().parents[1]


async def run(args: argparse.Namespace) -> None:
    cases = json.loads((ROOT / "benchmarks/llm/language_cases.json").read_text())
    policy = json.loads((ROOT / "benchmarks/llm/language_policy.json").read_text())
    if args.score:
        artifact = json.loads(await asyncio.to_thread(Path(args.score).read_text))
        report = score_report(
            artifact["results"],
            cases,
            policy,
            real_model=artifact.get("provider") in {"qwen3_transformers", "qwen3_mlx"},
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["status"] != "pass":
            raise SystemExit(1)
        return
    settings = load_local_llm_settings(args.profile, provider=args.provider)
    provider = build_llm_provider(settings)
    results = []
    try:
        for case in cases:
            print(f"Generating {case['id']} ({len(results) + 1}/{len(cases)})", flush=True)
            results.append(await evaluate_case(provider, case))
    except (ProviderUnavailable, ValueError) as exc:
        print(f"Lab stopped: {exc}")
        raise SystemExit(1) from None
    artifact = {
        "provider": settings.llm_provider,
        "model": settings.local_llm_model,
        "revision": settings.local_llm_revision,
        "policy": policy,
        "results": results,
    }
    with open_private(Path(args.output), "w") as handle:
        handle.write(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(results)} outputs to {args.output}; native scores pending.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "qwen3_transformers", "qwen3_mlx"))
    parser.add_argument("--profile", help="non-secret local model profile JSON")
    parser.add_argument("--output", default="var/roma/llm-lab/review.json")
    parser.add_argument(
        "--score", help="review JSON with named native reviewers and completed scores"
    )
    asyncio.run(run(parser.parse_args()))
