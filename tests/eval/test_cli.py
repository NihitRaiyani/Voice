"""`scripts/run_eval.py` — the exit code, mostly.

This is meant to be run by CI, where a zero exit is read as "the checks passed". So the
tests here are about the exit code being honest: green only when every check passed, and
non-zero when anything failed, including a filter canary.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _cli():
    """Load `scripts/run_eval.py` as a module. It is a script, not a package member, so
    there is no import path to it — the same reason `scripts/verify_media.py` is invoked
    by path rather than imported."""
    spec = importlib.util.spec_from_file_location("run_eval", _ROOT / "scripts" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_offline_run_over_the_shipped_corpus_exits_zero(capsys):
    assert _cli().main([]) == 0
    out = capsys.readouterr().out
    assert "filter canaries: PASS" in out
    assert "scripts passed" in out


def test_a_failing_expectation_exits_nonzero(tmp_path, capsys):
    """A script whose declared stage is wrong must turn the run red, not be shrugged off."""
    d = tmp_path / "scripts"
    d.mkdir()
    (d / "wrong.jsonl").write_text(
        '{"name": "wrong"}\n{"lead": "haan", "expect_stage": "close"}\n',
        encoding="utf-8",
    )
    assert _cli().main(["--scripts-dir", str(d)]) == 1
    assert "[stage-hits]" in capsys.readouterr().out


def test_a_broken_filter_exits_nonzero_even_with_a_perfect_corpus(monkeypatch, capsys):
    """The canaries run unconditionally and gate the exit code on their own. Restore the
    tokenizer defect and the run must go red even though every script still passes — a
    filter that fails open is not something a green corpus is allowed to hide."""
    broken = re.compile(r"[₹%]|\w+")
    monkeypatch.setattr("roma.domain.safety.filter._tokens", lambda text: broken.findall(text))
    assert _cli().main([]) == 1
    out = capsys.readouterr().out
    assert "filter canaries: FAIL" in out
    assert "LEAKED" in out


def test_selecting_one_script_by_name(capsys):
    assert _cli().main(["--script", "clean_lock"]) == 0
    out = capsys.readouterr().out
    assert "clean_lock" in out
    assert "objection_detour" not in out


def test_an_unknown_script_name_is_an_error(capsys):
    assert _cli().main(["--script", "does_not_exist"]) == 2
    assert "no script matched" in capsys.readouterr().err


def test_a_bad_scripts_dir_is_an_error(tmp_path, capsys):
    assert _cli().main(["--scripts-dir", str(tmp_path / "nope")]) == 2
    assert "error:" in capsys.readouterr().err


def test_verbose_prints_per_turn_detail(capsys):
    assert _cli().main(["--script", "clean_lock", "-v"]) == 0
    out = capsys.readouterr().out
    assert "roma (" in out and "[discover" in out


@pytest.mark.parametrize("flag", ["--live"])
def test_live_is_opt_in(flag):
    """Nothing here runs it — the point is that the default has no `--live` in it, so no
    invocation of this harness spends money unless someone typed the flag."""
    mod = _cli()
    args = mod._parse_args([])
    assert args.live is False
    assert mod._parse_args([flag]).live is True


def test_live_provider_meters_extraction_and_wording_with_fake_only():
    import asyncio

    from roma.core.ai_contracts import LLMMessage, LLMRequest
    from roma.eval.cost import Meter
    from tests.realtime.test_provider_llm import FakeProvider

    async def run():
        cli = _cli()
        meter = Meter(1)
        provider = cli._EvalProvider(FakeProvider(), meter)
        for purpose in ("extraction", "wording"):
            async for _ in provider.generate(LLMRequest(
                (LLMMessage("user", "fixture"),), metadata={"purpose": purpose}
            )):
                pass
        assert meter.calls == 2
        assert meter.spent_inr == pytest.approx(0.02)
        await provider.aclose()
        assert provider.provider.client_closed
    asyncio.run(run())


def test_live_provider_stops_after_quota_failure_without_more_calls():
    import asyncio
    from dataclasses import replace

    from roma.core.ai_contracts import LLMChunk, LLMMessage, LLMRequest
    from roma.eval.cost import Meter
    from tests.realtime.test_provider_llm import METRICS, FakeProvider

    class Unavailable(FakeProvider):
        async def generate(self, request):
            self.requests.append(request)
            yield LLMChunk("safe fallback", "provider_unavailable", replace(
                METRICS, error_code="provider_unavailable"
            ))

    async def run():
        provider = _cli()._EvalProvider(Unavailable(), Meter(1))
        request = LLMRequest((LLMMessage("user", "fixture"),))
        async for _ in provider.generate(request):
            pass
        with pytest.raises(RuntimeError):
            async for _ in provider.generate(request):
                pass
        assert len(provider.provider.requests) == 1
    asyncio.run(run())


def test_live_generator_uses_actual_provider_output_and_history():
    import asyncio

    from roma.domain.conversation.state import CallState
    from roma.eval.cost import Meter
    from tests.realtime.test_provider_llm import FakeProvider

    async def run():
        cli = _cli()
        script = next(s for s in cli.script_mod.load_dir(cli.DEFAULT_SCRIPT_DIR) if s.name == "clean_lock")
        meter = Meter(1)
        provider = cli._EvalProvider(FakeProvider(), meter)
        generate = cli._live_generator(script, provider)
        state = CallState(call_sid="eval-synthetic")
        assert await generate(state, meter) == "Namaste. Kaise hain?"
        assert await generate(state, meter) == "Namaste. Kaise hain?"
        requests = provider.provider.requests
        assert requests[0].messages[-1].content == script.turns[0].lead
        assert requests[1].messages[-2].role == "assistant"
        assert requests[1].messages[-1].content == script.turns[1].lead
        assert requests[1].max_tokens <= provider.provider.max_output_tokens
    asyncio.run(run())
