"""Separate multilingual output lab; never changes live Roma language policy."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from roma.domain.conversation.state import CallState
from roma.providers.ai.contracts import LLMProvider, LLMRequest
from roma.services.local_llm_prompts import local_dialogue_request
from roma.services.text_conversation_service import filter_response


def lab_request(case: dict[str, Any]) -> LLMRequest:
    return local_dialogue_request(
        CallState.from_dict(case["state"]),
        case["caller"],
        case["task"],
        language=case["language"],
        max_tokens=256,
    )


async def evaluate_case(provider: LLMProvider, case: dict[str, Any]) -> dict[str, Any]:
    request = lab_request(case)
    raw, metrics = [], []
    finish_reason = None
    async for chunk in provider.generate(request):
        raw.append(chunk.text)
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason
        if chunk.metrics:
            metrics.append(asdict(chunk.metrics))
    output = "".join(raw)
    final, safety = filter_response(output, CallState.from_dict(case["state"]))
    return {
        "id": case["id"],
        "language": case["language"],
        "stage": case["stage"],
        "prompt": [asdict(m) for m in request.messages],
        "generation": {
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "prompt_version": request.metadata.get("prompt_version"),
            "sampling_policy": request.metadata.get("sampling_policy"),
        },
        "raw_output": output,
        "finish_reason": finish_reason,
        "final_response": final,
        "safety": safety,
        "metrics": metrics,
        "reviewer": "",
        "correctness": None,
        "naturalness": None,
        "unsafe_final": None,
    }


def score_report(
    rows: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    real_model: bool,
) -> dict[str, Any]:
    expected = {c["id"]: c for c in cases}
    if len(rows) != len(expected) or {r.get("id") for r in rows} != set(expected):
        return {
            "status": "incomplete",
            "reason": "Every scripted case needs exactly one review",
        }
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("language") != expected[row["id"]]["language"]:
            return {"status": "invalid", "reason": "Case language does not match corpus"}
        expected_prompt = [asdict(m) for m in lab_request(expected[row["id"]]).messages]
        request = lab_request(expected[row["id"]])
        expected_generation = {
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "prompt_version": request.metadata.get("prompt_version"),
            "sampling_policy": request.metadata.get("sampling_policy"),
        }
        if row.get("prompt") != expected_prompt or row.get("generation") != expected_generation:
            return {
                "status": "stale-prompts",
                "reason": "Regenerate changed corpus/prompt cases before scoring",
            }
        if (
            not isinstance(row.get("reviewer"), str)
            or not row["reviewer"].strip()
            or any(
                type(row.get(key)) is not int or not 1 <= row[key] <= 5
                for key in ("correctness", "naturalness")
            )
            or type(row.get("unsafe_final")) is not bool
        ):
            return {
                "status": "pending-native-review",
                "reason": "Named native reviewer, 1–5 scores and safety judgment required",
            }
        groups.setdefault(row["language"], []).append(row)
    summaries = {}
    passed = real_model
    for language in policy["languages"]:
        group = groups.get(language, [])
        if not group:
            return {"status": "incomplete", "reason": "Missing language group"}
        correctness = sum(r["correctness"] for r in group) / len(group)
        naturalness = sum(r["naturalness"] for r in group) / len(group)
        low = sum(
            min(r["correctness"], r["naturalness"]) < policy["individual_score_min"]
            for r in group
        )
        unsafe = sum(r["unsafe_final"] for r in group)
        ok = (
            correctness >= policy["mean_correctness_min"]
            and naturalness >= policy["mean_naturalness_min"]
            and low == 0
            and unsafe <= policy["unsafe_final_replies_max"]
        )
        passed = passed and ok
        summaries[language] = {
            "correctness": correctness,
            "naturalness": naturalness,
            "below_minimum": low,
            "unsafe_final": unsafe,
            "passed": ok,
        }
    return {
        "status": "pass" if passed else "fail",
        "real_model": real_model,
        "languages": summaries,
        "decision": "candidate eligible for next review"
        if passed
        else "tighten prompts or evaluate another open model; rerun and record decision",
    }
