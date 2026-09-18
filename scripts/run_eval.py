"""Run the Step 7 replay harness (docs/10).

    uv run python scripts/run_eval.py                      # offline, ₹0, this is the default
    uv run python scripts/run_eval.py --script clean_lock  # one script by name
    uv run python scripts/run_eval.py -v                   # per-turn detail
    uv run python scripts/run_eval.py --live               # real model, METERED, costs money

Offline is the default and makes ZERO network calls — it validates the controller and the
filter, and is what belongs in CI. `--live` replays the same scripts through real
gpt-4o-mini to check the model's own output shape, metered against
`Settings.openai_budget_inr` (₹100).

Before any `--live` run, lower the OpenAI dashboard hard spend limit to ₹100. docs/02 is
explicit that the cap is enforced by OpenAI, not by discipline; the meter in `roma.eval.cost`
is a second belt that reports and halts, not the thing standing between you and the bill.

Exit code is 0 only when every check passed and nothing halted — this is meant to be run by
CI, so "halted early on budget" must never look like success.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roma.eval import script as script_mod
from roma.eval.checks import check_canaries
from roma.eval.cost import Meter
from roma.eval.runner import run_script

_log = logging.getLogger("roma.eval")

DEFAULT_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "evals" / "scripts"


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Roma Step-7 replay harness")
    p.add_argument(
        "--scripts-dir",
        default=str(DEFAULT_SCRIPT_DIR),
        help="directory of *.jsonl conversation fixtures",
    )
    p.add_argument(
        "--script",
        action="append",
        default=None,
        metavar="NAME",
        help="run only this script (by name or filename stem); repeatable",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="replay through the REAL model. Costs money. Metered against OPENAI_BUDGET_INR",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="per-turn detail")
    return p.parse_args(argv)


def _print_result(result, verbose: bool) -> None:
    mark = "PASS" if result.ok else "FAIL"
    print(f"\n{mark}  {result.name}  ({len(result.turns)} turns, outcome={result.outcome})")
    if verbose:
        for t in result.turns:
            flag = "  " if not t.expect_phase or t.phase == t.expect_phase else "->"
            print(f"   {flag} {t.index:>2}. [{t.phase:<12}] lead: {t.lead}")
            if t.roma:
                print(f"          roma ({len(t.roma.split())}w): {t.roma}")
    if result.halted:
        print(f"   HALTED: {result.halted}")
    for f in result.findings:
        print(f"   {f}")


async def _main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    try:
        scripts = script_mod.load_dir(args.scripts_dir)
    except script_mod.ScriptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.script:
        wanted = set(args.script)
        scripts = [s for s in scripts if s.name in wanted or (s.path and s.path.stem in wanted)]
        if not scripts:
            print(f"error: no script matched {sorted(wanted)}", file=sys.stderr)
            return 2

    meter = None
    client = None
    if args.live:
        from openai import AsyncOpenAI
        from roma.core.config import get_settings

        settings = get_settings()
        meter = Meter(budget_inr=settings.openai_budget_inr)
        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        print(
            f"LIVE run — budget ₹{meter.budget_inr:.2f}. This spends real money.\n"
            "Confirm the OpenAI dashboard hard limit is set before trusting this meter."
        )

    canary_findings = check_canaries()
    print(f"filter canaries: {'PASS' if not canary_findings else 'FAIL'}")
    for f in canary_findings:
        print(f"   {f}")

    results = []
    for s in scripts:
        results.append(await run_script(s, live=args.live, client=client, meter=meter))
        _print_result(results[-1], args.verbose)

    failed = [r for r in results if not r.ok]
    total_findings = sum(len(r.findings) for r in results) + len(canary_findings)
    print(
        f"\n{len(results) - len(failed)}/{len(results)} scripts passed, "
        f"{total_findings} finding(s)"
    )
    if meter is not None:
        print(
            f"spent ₹{meter.spent_inr:.2f} of ₹{meter.budget_inr:.2f} over {meter.calls} "
            f"OpenAI call(s) — verify against the dashboard"
        )
        if meter.calls == 0:
            print(
                "   WARNING: 0 calls metered. --live does not meter generation yet "
                "(no `generate` is passed to run_script), so the ₹0.00 above measures "
                "nothing. The OpenAI dashboard is the only number to trust here."
            )
    return 0 if not failed and not canary_findings else 1


def main(argv=None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
