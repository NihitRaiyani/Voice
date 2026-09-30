"""Run the Dramatiq consumer: one process and four threads by default.

Extra arguments go to Dramatiq, e.g. --processes 2 --threads 8.
"""

import sys
from pathlib import Path

from dramatiq.cli import main as dramatiq_main
from dramatiq.cli import make_argument_parser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    args = make_argument_parser().parse_args(
        [
            "roma.workers.dramatiq_worker:configure_worker_broker",
            "--processes",
            "1",
            "--threads",
            "4",
            *(sys.argv[1:] if argv is None else argv),
        ]
    )
    return dramatiq_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
