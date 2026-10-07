import json
from dataclasses import asdict
from pathlib import Path

from roma.eval.local_language import lab_request, score_report

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "benchmarks/llm/language_cases.json").read_text())
POLICY = json.loads((ROOT / "benchmarks/llm/language_policy.json").read_text())


def reviews():
    return [
        {
            "id": c["id"],
            "language": c["language"],
            "prompt": [asdict(m) for m in lab_request(c).messages],
            "generation": {
                "max_tokens": 256,
                "temperature": 0.7,
                "prompt_version": "compact-v1",
                "sampling_policy": "qwen-nonthinking-p08-k20",
            },
            "reviewer": "Native reviewer",
            "correctness": 4,
            "naturalness": 4,
            "unsafe_final": False,
        }
        for c in CASES
    ]


def test_corpus_covers_all_stages_in_each_language_and_lab_removes_only_live_language_policy():
    assert len(CASES) == 40
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
    prompt = lab_request(CASES[0]).messages[0].content
    assert "Reply only in natural Gujarati script" in prompt
    assert "ALWAYS ANSWER IN HINDI" not in prompt
    assert "NO PROMISES" in prompt
    assert "MONEY" in prompt


def test_missing_native_review_is_pending_and_mock_can_never_pass():
    rows = reviews()
    rows[0]["reviewer"] = ""
    assert (
        score_report(rows, CASES, POLICY, real_model=True)["status"] == "pending-native-review"
    )
    assert score_report(reviews(), CASES, POLICY, real_model=False)["status"] == "fail"


def test_thresholds_are_per_language_and_enforce_low_scores_and_unsafe_final():
    rows = reviews()
    assert score_report(rows, CASES, POLICY, real_model=True)["status"] == "pass"
    rows[0]["naturalness"] = 2
    assert score_report(rows, CASES, POLICY, real_model=True)["status"] == "fail"
    rows = reviews()
    rows[0]["unsafe_final"] = True
    assert score_report(rows, CASES, POLICY, real_model=True)["status"] == "fail"


def test_missing_duplicate_or_mislabeled_cases_cannot_pass():
    rows = reviews()
    assert score_report(rows[:-1], CASES, POLICY, real_model=True)["status"] == "incomplete"
    rows[-1] = rows[0]
    assert score_report(rows, CASES, POLICY, real_model=True)["status"] == "incomplete"
    rows = reviews()
    rows[0]["language"] = "en"
    assert score_report(rows, CASES, POLICY, real_model=True)["status"] == "invalid"


def test_changed_prompt_or_corpus_cannot_reuse_old_native_scores():
    rows = reviews()
    rows[0]["prompt"][0]["content"] += " stale instruction"
    assert score_report(rows, CASES, POLICY, real_model=True)["status"] == "stale-prompts"


def test_discovery_cases_keep_city_unknown_in_state_and_caller_text():
    for case in CASES:
        if case["id"].endswith("-02"):
            assert not case["state"].get("city")
            assert "2025" in case["caller"]
            assert not any(city in case["caller"] for city in ("Vadodara", "वडोदरा", "વડોદરા"))
