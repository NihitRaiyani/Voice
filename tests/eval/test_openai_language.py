import asyncio
import json
from pathlib import Path

from roma.eval.openai_language import evaluate_case, score_report
from roma.providers.ai.mocks import MockLLMProvider

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "benchmarks/llm/language_cases.json").read_text())
POLICY = json.loads((ROOT / "benchmarks/llm/language_policy.json").read_text())


def rows():
    async def run():
        return [await evaluate_case(MockLLMProvider(), c) for c in CASES]

    return asyncio.run(run())


def test_corpus_covers_all_stages_and_languages_and_native_reviews_are_never_filled():
    assert len(CASES) == 40 and {c["language"] for c in CASES} == set(POLICY["languages"])
    for language in POLICY["languages"]:
        assert {c["stage"] for c in CASES if c["language"] == language} == {
            "open",
            "discover",
            "value",
            "structure",
            "pivot",
            "objection",
            "close",
        }
    result = rows()
    assert all(r["correctness"] is None and r["reviewer"] == "" for r in result)
    assert (
        score_report(result, CASES, POLICY, real_model=False)["status"]
        == "pending-native-review"
    )


def test_mock_outputs_cannot_pass_even_with_synthetic_perfect_review_fixtures():
    result = rows()
    for row in result:
        row.update(reviewer="synthetic-test", correctness=5, naturalness=5, unsafe_final=False)
    assert score_report(result, CASES, POLICY, real_model=False)["status"] == "fail"
    result[0]["prompt"][0]["content"] += " stale"
    assert score_report(result, CASES, POLICY, real_model=True)["status"] == "stale-prompts"


def test_duplicate_case_and_language_mismatch_fail_closed():
    result = rows()
    result[-1] = result[0]
    assert score_report(result, CASES, POLICY, real_model=False)["status"] == "incomplete"
    result = rows()
    result[0]["language"] = "en"
    assert score_report(result, CASES, POLICY, real_model=False)["status"] == "invalid"
