"""Run the Dramatiq consumer: one process and four threads by default.

Extra arguments go to Dramatiq, e.g. --processes 2 --threads 8.
Only --allow-paid-summary opts into capped Claude jobs; the default uses metadata.
"""

import sys
from pathlib import Path

from dramatiq.cli import main as dramatiq_main
from dramatiq.cli import make_argument_parser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    options = list(sys.argv[1:] if argv is None else argv)
    allow_paid_summary = "--allow-paid-summary" in options
    options = [value for value in options if value != "--allow-paid-summary"]
    factory = (
        "configure_paid_summary_broker" if allow_paid_summary else "configure_worker_broker"
    )
    args = make_argument_parser().parse_args(
        [
            f"roma.workers.dramatiq_worker:{factory}",
            "--processes",
            "1",
            "--threads",
            "4",
            *options,
        ]
    )
    return dramatiq_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
