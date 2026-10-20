"""Run the Step 7 replay harness (docs/10).

    uv run python scripts/run_eval.py                      # offline, ₹0, this is the default
    uv run python scripts/run_eval.py --script clean_lock  # one script by name
    uv run python scripts/run_eval.py -v                   # per-turn detail
    uv run python scripts/run_eval.py --live               # real model, METERED, costs money

Offline is the default and makes ZERO network calls. Explicit `--live` selects the
configured Claude provider, generates real replies, and meters extraction plus wording.
The local meter supplements the Anthropic account spend limit.

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
from roma.eval.cost import BudgetExceeded, Meter
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
        help="replay through Claude. Costs money. Metered against ANTHROPIC_BUDGET_INR",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="per-turn detail")
    return p.parse_args(argv)


def _print_result(result, verbose: bool) -> None:
    mark = "PASS" if result.ok else "FAIL"
    print(f"\n{mark}  {result.name}  ({len(result.turns)} turns, outcome={result.outcome})")
    if verbose:
        for t in result.turns:
            flag = "  " if not t.expect_stage or t.stage == t.expect_stage else "->"
            print(f"   {flag} {t.index:>2}. [{t.stage:<12}] lead: {t.lead}")
            if t.roma:
                print(f"          roma ({len(t.roma.split())}w): {t.roma}")
    if result.halted:
        print(f"   HALTED: {result.halted}")
    for f in result.findings:
        print(f"   {f}")


class _EvalProvider:
    """Count every extraction/wording attempt, including unknown-usage reservations."""

    def __init__(self, provider, meter):
        self.provider = provider
        self.meter = meter
        self.failed = False

    @property
    def max_output_tokens(self):
        return self.provider.max_output_tokens

    async def generate(self, request):
        from contextlib import aclosing

        self.meter.check_before_call()
        if self.failed:
            raise RuntimeError("Claude evaluation provider is unavailable")
        before = getattr(self.provider, "spent_cents", 0)
        final = None
        try:
            async with aclosing(self.provider.generate(request)) as stream:
                async for chunk in stream:
                    if chunk.metrics is not None:
                        final = chunk.metrics
                        self.failed |= final.error_code is not None
                    yield chunk
        except Exception:
            self.failed = True
            raise
        finally:
            charged = (getattr(self.provider, "spent_cents", before) - before) / 100
            if final is not None:
                charged = final.charged_inr
                self.meter.calls += final.attempts
                self.meter.input_tokens += final.input_tokens or 0
                self.meter.output_tokens += final.output_tokens or 0
            elif charged:
                self.meter.calls += 1
                self.failed = True
            self.meter.spent_inr += charged

    async def aclose(self):
        await self.provider.aclose()


def _live_generator(script, client):
    from contextlib import aclosing

    from roma.domain.conversation.prompts import assemble_system_prompt, stage_max_tokens
    from roma.providers.ai.contracts import LLMMessage, LLMRequest

    history = []
    index = 0

    async def generate(state, meter):
        nonlocal index
        history.append(LLMMessage("user", script.turns[index].lead))
        index += 1
        request = LLMRequest(
            messages=(
                LLMMessage(
                    "system", assemble_system_prompt(state.as_prompt_vars(), state.stage)
                ),
                *history,
            ),
            max_tokens=min(
                stage_max_tokens(state.stage),
                getattr(client.provider, "max_output_tokens", 256),
            ),
        )
        parts = []
        async with aclosing(client.generate(request)) as stream:
            async for chunk in stream:
                parts.append(chunk.text)
        if meter.remaining_inr <= 0:
            raise BudgetExceeded(meter)
        text = "".join(parts)
        history.append(LLMMessage("assistant", text))
        return text

    return generate


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
    settings = None
    if args.live:
        from roma.core.config import get_settings

        settings = get_settings()
        if settings.llm_provider != "claude":
            print("error: --live requires LLM_PROVIDER=claude", file=sys.stderr)
            return 2
        meter = Meter(budget_inr=settings.anthropic_budget_inr)
        print(
            f"LIVE Claude run — budget ₹{meter.budget_inr:.2f}. This spends real money.\n"
            "Use an Anthropic account spend limit in addition to this local meter."
        )

    canary_findings = check_canaries()
    print(f"filter canaries: {'PASS' if not canary_findings else 'FAIL'}")
    for f in canary_findings:
        print(f"   {f}")

    results = []
    provider_failed = False
    for s in scripts:
        client = None
        generate = None
        if args.live:
            from roma.providers.ai.registry import build_llm_provider

            if meter.remaining_inr <= 0:
                provider_failed = True
                print("HALTED: Claude run budget exhausted")
                break
            call_settings = settings.model_copy(
                update={
                    "anthropic_call_spend_cap_inr": min(
                        settings.anthropic_call_spend_cap_inr, meter.remaining_inr
                    )
                }
            )
            client = _EvalProvider(build_llm_provider(call_settings), meter)
            generate = _live_generator(s, client)
        try:
            results.append(
                await run_script(
                    s, live=args.live, client=client, meter=meter, generate=generate
                )
            )
        except Exception:  # noqa: BLE001 -- report incomplete without exposing SDK payloads.
            print("HALTED: Claude generation/extraction failed; run incomplete")
            return 1
        finally:
            if client is not None:
                await client.aclose()
        if client is not None and client.failed:
            provider_failed = True
            results[-1].halted = "Claude generation/extraction failed; run incomplete"
        _print_result(results[-1], args.verbose)
        if results[-1].halted:
            break

    failed = [r for r in results if not r.ok]
    total_findings = sum(len(r.findings) for r in results) + len(canary_findings)
    print(
        f"\n{len(results) - len(failed)}/{len(results)} scripts passed, "
        f"{total_findings} finding(s)"
    )
    if meter is not None:
        print(
            f"spent ₹{meter.spent_inr:.2f} of ₹{meter.budget_inr:.2f} over {meter.calls} "
            f"Claude attempt(s) — verify against the Anthropic account"
        )
    return 0 if not failed and not canary_findings and not provider_failed else 1


def main(argv=None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
