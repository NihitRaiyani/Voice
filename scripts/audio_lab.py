"""Section 14 pure audio lab; no credentials, downloads, provider calls or DB access."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roma.eval.audio import report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jitter-ms", type=int, choices=(40, 50, 60), default=60)
    args = parser.parse_args()
    print(json.dumps(report(delay_ms=args.jitter_ms), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
