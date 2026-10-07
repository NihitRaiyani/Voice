"""Download only model/tokenizer data; HF credentials stay in the child environment."""

import argparse
import os
import shutil
import subprocess

from roma.core.local_llm_config import load_local_llm_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="configs/local-llm-mac.json")
    parser.add_argument(
        "--fast", action="store_true", help="enable supported Xet high-performance transfers"
    )
    args = parser.parse_args()
    settings = load_local_llm_settings(args.profile)
    executable = shutil.which("hf")
    if executable is None:
        raise SystemExit("Install the locked runtime first: make local-llm-mac-sync (Apple) or make local-llm-sync (Transformers)")
    env = os.environ.copy()
    token = settings.hf_token.get_secret_value()
    if token:
        env["HF_TOKEN"] = token
    env["HF_DEBUG"] = "0"
    if args.fast:
        env["HF_XET_HIGH_PERFORMANCE"] = "1"
    subprocess.run(
        [
            executable,
            "download",
            settings.local_llm_model,
            "--revision",
            settings.local_llm_revision,
            "--include",
            "*.json",
            "*.safetensors",
            "*.jinja",
            "merges.txt",
            "--max-workers",
            "4",
        ],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
