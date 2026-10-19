import asyncio
from pathlib import Path

import pytest
from roma.eval.benchmark_evidence import evidence_from_report
from roma.eval.tts import SyntheticPCMProvider, report
from roma.services.safety_service import InMemorySafetyEventStore, SafetyService
from roma.services.tts_service import SpeechSynthesisService


def test_offline_tts_lab_all_three_languages_and_lengths():
    async def run():
        result = await report(
            SpeechSynthesisService(
                SyntheticPCMProvider(),
                SafetyService(InMemorySafetyEventStore()),
                model_version="synthetic-pcm-v1",
            )
        )
        assert (
            result["synthetic_only"]
            and not result["native_review_passed"]
            and not result["network_calls"]
        )
        assert result["cache"] == {"hits": 9, "misses": 9, "stores": 9, "coalesced": 0}
        assert len(result["cases"]) == 18
        assert {row["language"] for row in result["cases"]} == {"hi", "gu", "en"}
        assert {row["length"] for row in result["cases"]} == {"short", "medium", "long"}
        assert all(
            row["audit_ok"] and row["audio_ms"] > 0 and row["rtf"] >= 0
            for row in result["cases"]
        )
        for miss, hit in zip(result["cases"][::2], result["cases"][1::2], strict=True):
            assert len(miss["audio_sha256"]) == 64
            assert miss["audio_sha256"] == hit["audio_sha256"]
            assert miss["sample_rate_hz"] == 8000
            assert miss["voice"] == "female"

    asyncio.run(run())


def test_failed_provider_preserves_all_case_error_evidence():
    class Failed:
        async def synthesize(self, request):
            raise RuntimeError("Synthetic sensitive detail must not enter the report")

    async def run():
        result = await report(
            SpeechSynthesisService(Failed(), SafetyService(), model_version="failure-test-v1"),
            languages=("en",),
        )
        assert result["errors"] == result["expected_cases"] == len(result["cases"]) == 6
        assert all(
            row["status"] == "error" and row["error_code"] == "RuntimeError"
            for row in result["cases"]
        )
        assert "sensitive detail" not in str(result)

    asyncio.run(run())


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
def test_partial_failure_keeps_resolved_model_error_rate(failure):
    class Partial(SyntheticPCMProvider):
        calls = 0

        async def cache_identity(self, request):
            assert request.voice == "female" and request.language == "en"
            return "indic-tts:" + "a" * 64

        async def synthesize(self, request):
            self.calls += 1
            if self.calls == 1:
                raise failure("sensitive failure detail")
            return await super().synthesize(request)

    async def run():
        result = await report(
            SpeechSynthesisService(
                Partial(), SafetyService(), model_version="manifest-validated"
            ),
            provider="indic_tts",
            languages=("en",),
        )
        assert result["errors"] == 1
        first, second = result["cases"][:2]
        assert first["model_version"] == second["model_version"] == "indic-tts:" + "a" * 64
        assert first["model_identity_resolved"] is second["model_identity_resolved"] is True
        evidence = evidence_from_report(
            result,
            run_key="partial-test",
            report_path=Path("var/roma/test.json"),
            report_sha256="b" * 64,
        )
        rates = [row for row in evidence.metrics if row.metric == "error_rate.short"]
        assert len(evidence.models) == len(rates) == 1
        assert rates[0].value == 0.5 and rates[0].sample_count == 2
        assert "sensitive failure detail" not in str(result)

    asyncio.run(run())


def test_identity_failure_records_unresolved_setup_evidence():
    class Missing(SyntheticPCMProvider):
        async def cache_identity(self, request):
            raise RuntimeError("private missing manifest path")

        async def synthesize(self, request):
            pytest.fail("An unresolved model must not be synthesized")

    async def run():
        result = await report(
            SpeechSynthesisService(
                Missing(), SafetyService(), model_version="manifest-validated"
            ),
            provider="indic_tts",
            languages=("gu",),
        )
        assert result["errors"] == 6
        assert all(
            row["model_identity_resolved"] is False
            and row["model_version"] == "unresolved-local-artifacts"
            for row in result["cases"]
        )
        with pytest.raises(ValueError, match="unresolved model provenance"):
            evidence_from_report(
                result,
                run_key="unresolved-test",
                report_path=Path("var/roma/test.json"),
                report_sha256="b" * 64,
            )
        assert "private missing manifest path" not in str(result)

    asyncio.run(run())
