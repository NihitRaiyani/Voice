"""Phase 5A synthetic jitter/replay lab; never reads private settings or loads models."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.eval.adaptive_audio import report  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=3550)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(report(seed=args.seed)), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
