"""Run each pinned model in a separate process; text-only, no audio or promotion."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from roma.core.local_llm_config import load_local_llm_settings
from roma.core.private_files import open_private
from roma.eval.local_comparison import summarize_candidate

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles",
        nargs="+",
        default=["configs/local-llm-mac.json", "configs/local-llm-mac-4b.json"],
    )
    parser.add_argument("--output-dir", default="var/roma/llm-lab/compact-comparison")
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="read existing complete artifacts; never reuse stale prompts",
    )
    args = parser.parse_args()
    cases = json.loads((ROOT / "benchmarks/llm/language_cases.json").read_text())
    destination = Path(args.output_dir)
    if not destination.is_absolute():
        destination = ROOT / destination
    summaries = []
    for index, profile in enumerate(args.profiles):
        target = destination / f"candidate-{index + 1}.json"
        settings = load_local_llm_settings(ROOT / profile)
        if not args.summarize_only:
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/local_llm_language_eval.py"),
                    "--profile",
                    str(ROOT / profile),
                    "--output",
                    str(target),
                ],
                cwd=ROOT,
                check=True,
            )
        artifact = json.loads(target.read_text())
        if (
            artifact.get("model") != settings.local_llm_model
            or artifact.get("revision") != settings.local_llm_revision
        ):
            raise ValueError("Artifact does not match the requested pinned profile")
        summaries.append(summarize_candidate(artifact, cases))
    report = {
        "scope": "text-only; separate processes; no native grades, audio workload or production selection",
        "candidates": summaries,
    }
    with open_private(destination / "summary.json", "w") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
