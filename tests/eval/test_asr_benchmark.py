import asyncio
import hashlib
import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import pytest
from roma.domain.audio.pcm import pack, unpack
from roma.eval.asr_benchmark import (
    LANGUAGES,
    build_manifest,
    case_audio,
    digest,
    edit_counts,
    export_failure_samples,
    failure_samples,
    perturb,
    read_json,
    read_wav,
    run_benchmark,
    score,
    summaries,
    validate_manifest,
)
from roma.providers.ai.contracts import ProviderUnavailable, STTResult


@pytest.fixture
def public_cache(tmp_path):
    folder = tmp_path / "public"
    folder.mkdir()
    # Deliberately non-silent signed audio exercises noise and codec transformations.
    pcm = pack([round(8000 * math.sin(i * 0.12)) for i in range(1600)])
    for language, config in LANGUAGES.items():
        path = folder / f"{language}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        metadata = {
            "dataset": "google/fleurs",
            "config": config,
            "split": "validation",
            "row": 0,
            "reference": f"hello {language}",
            "license": "CC-BY-4.0",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        path.with_suffix(".json").write_text(json.dumps(metadata))
    return folder


class FakeSTT:
    def __init__(self, fail_at=None, cancel_at=None):
        self.requests = []
        self.fail_at, self.cancel_at = fail_at, cancel_at

    async def transcribe(self, request):
        self.requests.append(request)
        if len(self.requests) == self.cancel_at:
            raise asyncio.CancelledError
        if len(self.requests) == self.fail_at:
            raise ProviderUnavailable("TOKEN secret /caller/private.wav remote raw error")
        return STTResult("hello " + request.language_hint)


def test_edit_counts_rates_empty_and_indic():
    assert edit_counts("a b c".split(), "a x c".split()) == {
        "edits": 1,
        "substitutions": 1,
        "deletions": 0,
        "insertions": 0,
    }
    assert score("one two", "one")["deletions"] == 1
    assert score("one", "one two three")["insertions"] == 2
    assert score("one", "one two three")["wer"] == 2
    assert score("a b c", "x b d e")["substitutions"] == 2
    assert score("a b c", "x b d e")["insertions"] == 1
    assert score("", "")["wer"] == 0
    assert score("", "inserted")["wer"] is None
    assert score("", "inserted")["insertions"] == 1
    assert score("one", "")["deletions"] == 1
    assert score("હિન્દી ગુજરાતી हिन्दी ૨૦૨૬", "હિન્દી ગુજરાતી हिन्दी 2026")["cer"] == 0
    assert score("SEO! Google Ads", "seo google ads")["wer"] == 0
    assert score("ab", "ac")["char_substitutions"] == 1


def test_manifest_determinism_integrity_and_stitched_labels(public_cache):
    manifest = build_manifest(public_cache, include_stitched_code_mix=True)
    assert digest(manifest) == digest(
        build_manifest(public_cache, include_stitched_code_mix=True)
    )
    assert len(manifest["cases"]) == 20
    assert {case["language"] for case in manifest["cases"]} == {
        "gu",
        "hi",
        "en",
        "gu-en",
        "hi-en",
    }
    assert manifest["native_code_mix_included"] is False
    sources = validate_manifest(manifest, public_cache)
    mixed = next(
        case
        for case in manifest["cases"]
        if case["language"] == "gu-en" and case["variant"] == "clean"
    )
    assert mixed["corpus_kind"] == "stitched_public_code_mix"
    assert len(case_audio(mixed, manifest, sources)) == 1600 * 4 + 8000
    manifest["sources"][0]["reference"] = "edited reference"
    with pytest.raises(ValueError, match="differs"):
        validate_manifest(manifest, public_cache)


def test_cache_rejects_tampered_audio_and_private_metadata(public_cache):
    metadata = public_cache / "gu.json"
    row = read_json(metadata)
    row["dataset"] = "private/callers"
    metadata.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="provenance"):
        build_manifest(public_cache)
    row["dataset"] = "google/fleurs"
    metadata.write_text(json.dumps(row))
    with (public_cache / "gu.wav").open("ab") as wav:
        wav.write(b"x")
    with pytest.raises(ValueError, match="identity"):
        build_manifest(public_cache)


@pytest.mark.parametrize("variant", ["noise", "telephone", "noise_telephone"])
def test_perturbations_are_bounded_reproducible_and_not_clean(variant):
    pcm = pack([round(12000 * math.sin(i * 0.2)) for i in range(1601)])
    first = perturb(pcm, variant, seed=7)
    assert first == perturb(pcm, variant, seed=7)
    assert first != pcm
    assert len(first) == len(pcm)
    assert all(-32768 <= value <= 32767 for value in unpack(first))
    if variant == "noise":
        noise = [b - a for a, b in zip(unpack(pcm), unpack(first), strict=True)]
        actual_snr = 10 * math.log10(
            sum(x * x for x in unpack(pcm)) / sum(x * x for x in noise)
        )
        assert actual_snr == pytest.approx(15, abs=0.02)
        assert first != perturb(pcm, variant, seed=8)


@pytest.mark.parametrize(
    "kwargs",
    [{"variants": ("unknown",)}, {"snr_db": float("nan")}, {"snr_db": -1}, {"seed": -1}],
)
def test_invalid_perturbation_config_rejected(public_cache, kwargs):
    with pytest.raises(ValueError):
        build_manifest(public_cache, **kwargs)


def test_weighted_summary_excludes_failed_requests():
    rows = []
    for ref, hyp, ms in [("a", "x", 1000), ("b c d", "b c d", 3000)]:
        rows.append(
            {
                "language": "hi",
                "variant": "clean",
                "corpus_kind": "public_read_speech",
                "status": "ok",
                "metrics": score(ref, hyp),
                "audio_seconds": ms / 1000,
                "request_ms": ms,
            }
        )
    rows.append({**rows[0], "status": "error", "metrics": None})
    summary = summaries(rows)[0]
    assert summary["wer"] == 0.25  # weighted corpus score, not .5 mean utterance WER
    assert summary["substitutions"] == 1
    assert summary["request_rtf"] == 1
    assert summary["inference_rtf"] is None
    assert (summary["succeeded"], summary["failed"]) == (2, 1)
    assert summaries([rows[-1]])[0]["wer"] is None


def test_runner_failure_checkpoint_resume_and_replay(public_cache, tmp_path):
    manifest = build_manifest(public_cache, variants=("clean",))
    path = tmp_path / "report.json"
    provider = FakeSTT(fail_at=2)
    ticks = iter([0, 1, 2, 4, 5, 8])
    report = asyncio.run(
        run_benchmark(
            manifest,
            public_cache,
            provider,
            provider_name="fake",
            report_path=path,
            clock=lambda: next(ticks),
        )
    )
    assert report["complete"]
    assert len(provider.requests) == 3
    assert all(
        request.metadata["data_classification"] == "public_benchmark"
        for request in provider.requests
    )
    failed = failure_samples(read_json(path))
    assert len(failed) == 1
    assert failed[0]["metrics"] is None
    assert failed[0]["hypothesis"] is None
    assert failed[0]["request_rtf"] == 20
    assert "TOKEN" not in path.read_text()
    assert failed[0]["error_code"] == "asr-request-failed"
    resumed = FakeSTT()
    asyncio.run(
        run_benchmark(
            manifest, public_cache, resumed, provider_name="fake", report_path=path, resume=True
        )
    )
    assert not resumed.requests
    retried = FakeSTT()
    report = asyncio.run(
        run_benchmark(
            manifest,
            public_cache,
            retried,
            provider_name="fake",
            report_path=path,
            resume=True,
            retry_errors=True,
        )
    )
    assert len(retried.requests) == 1
    assert not failure_samples(report)
    replay = FakeSTT()
    asyncio.run(
        run_benchmark(
            manifest,
            public_cache,
            replay,
            provider_name="fake",
            report_path=path,
            resume=True,
            case_ids={report["cases"][0]["case_id"]},
        )
    )
    assert len(replay.requests) == 1
    with pytest.raises(ValueError, match="identity"):
        asyncio.run(
            run_benchmark(
                manifest,
                public_cache,
                replay,
                provider_name="other",
                report_path=path,
                resume=True,
            )
        )


def test_cancellation_leaves_success_checkpoint_and_resume_skips_it(public_cache, tmp_path):
    manifest = build_manifest(public_cache, variants=("clean",))
    path = tmp_path / "report.json"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_benchmark(
                manifest,
                public_cache,
                FakeSTT(cancel_at=2),
                provider_name="fake",
                report_path=path,
            )
        )
    assert len(read_json(path)["cases"]) == 1
    assert not read_json(path)["complete"]
    resumed = FakeSTT()
    report = asyncio.run(
        run_benchmark(
            manifest, public_cache, resumed, provider_name="fake", report_path=path, resume=True
        )
    )
    assert len(resumed.requests) == 2
    assert report["complete"]


def test_language_selection_and_failure_audio_export(public_cache, tmp_path):
    manifest = build_manifest(public_cache, include_stitched_code_mix=True, variants=("clean",))
    path = tmp_path / "report.json"
    report = asyncio.run(
        run_benchmark(
            manifest,
            public_cache,
            FakeSTT(fail_at=1),
            provider_name="fake",
            report_path=path,
            languages={"gu", "gu-en"},
        )
    )
    assert report["expected_cases"] == 2
    assert report["complete"]
    assert report["languages"] == ["gu", "gu-en"]
    files = export_failure_samples(report, manifest, public_cache, tmp_path / "failures")
    assert len(files) == 2  # one request failure and one synthetic code-mix high-WER result
    saved = read_json(files[0].with_suffix(".json"))
    assert saved["data_classification"] == "public_benchmark"
    assert hashlib.sha256(read_wav(files[0])).hexdigest() == saved["case"]["audio_sha256"]
    report["cases"][0]["audio_sha256"] = "bad"
    with pytest.raises(ValueError, match="identity"):
        export_failure_samples(report, manifest, public_cache, tmp_path / "other")


def test_cli_no_network_by_default_and_offline_failure_read(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/asr_benchmark.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "run",
            "--manifest",
            str(tmp_path / "missing.json"),
            "--report",
            str(tmp_path / "report.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "No raw provider" in result.stderr
    assert not (tmp_path / "report.json").exists()
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"schema": "roma-asr-benchmark-v1", "cases": []}))
    result = subprocess.run(
        [sys.executable, str(script), "failures", "--report", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["cases"] == []


def test_resume_rejects_edited_saved_success(public_cache, tmp_path):
    manifest = build_manifest(public_cache, variants=("clean",))
    path = tmp_path / "report.json"
    report = asyncio.run(
        run_benchmark(manifest, public_cache, FakeSTT(), provider_name="fake", report_path=path)
    )
    assert report["completed_at"] >= report["recorded_at"]
    report["cases"][0]["metrics"]["wer"] = 1000
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="identity/score"):
        asyncio.run(
            run_benchmark(
                manifest,
                public_cache,
                FakeSTT(),
                provider_name="fake",
                report_path=path,
                resume=True,
            )
        )
    assert read_json(path)["cases"][0]["metrics"]["wer"] == 1000  # no clobber


def test_cli_direct_provider_selection_and_replay_are_offline_with_injected_adapters(
    public_cache, tmp_path, monkeypatch, capsys
):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts/asr_benchmark.py"
    spec = importlib.util.spec_from_file_location("benchmark_cli_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = FakeSTT(fail_at=1)
    monkeypatch.setattr(module, "GradioIndicSTT", lambda **kwargs: provider)
    whisper_options = []

    def whisper_adapter(**kwargs):
        whisper_options.append(kwargs)
        return provider

    monkeypatch.setattr(module, "GradioWhisperSTT", whisper_adapter)
    monkeypatch.setattr(
        module,
        "SpeechSettings",
        lambda **kwargs: type(
            "Settings",
            (),
            {
                "hf_indic_space_url": "https://indic.hf.space",
                "hf_whisper_space_url": "https://whisper.hf.space",
                "hf_whisper_api_name": "predict",
                "hf_whisper_space_authenticated": False,
                "hf_public_timeout_secs": 30,
                "stt_max_audio_secs": 30,
                "indic_decoder": "ctc",
            },
        )(),
    )
    manifest = build_manifest(public_cache, variants=("clean",))
    manifest_path, report_path = tmp_path / "manifest.json", tmp_path / "report.json"
    manifest_path.write_text(json.dumps(manifest))
    common = [
        "--allow-network",
        "--manifest",
        str(manifest_path),
        "--audio-dir",
        str(public_cache),
        "--report",
        str(report_path),
        "--provider",
        "hf_indic",
    ]
    with pytest.raises(ValueError, match="does not support English"):
        asyncio.run(module.run(module.parser().parse_args(["run", *common])))
    assert not provider.requests
    assert (
        asyncio.run(
            module.run(module.parser().parse_args(["run", *common, "--languages", "gu", "hi"]))
        )
        == 1
    )
    assert len(provider.requests) == 2
    assert asyncio.run(module.run(module.parser().parse_args(["replay", *common]))) == 0
    assert len(provider.requests) == 3
    assert read_json(report_path)["provider_details"]["remote_model_revision"] is None
    assert (
        asyncio.run(
            module.run(
                module.parser().parse_args(
                    ["run", *common[:-2], "--provider", "hf_whisper_space"]
                )
            )
        )
        == 0
    )
    assert len(provider.requests) == 6
    assert read_json(report_path)["provider"] == "hf_whisper_space"
    assert whisper_options == [
        {
            "endpoint_url": "https://whisper.hf.space",
            "api_name": "predict",
            "token": None,
            "timeout_secs": 30,
            "max_audio_secs": 30,
        }
    ]
    assert read_json(report_path)["provider_details"]["whisper_api_name"] == "predict"
    capsys.readouterr()


def test_known_hosted_quota_failure_has_fixed_benchmark_code(public_cache, tmp_path):
    from roma.providers.ai.hf_asr import HFASRQuotaExceeded

    class QuotaSTT:
        async def transcribe(self, request):
            raise HFASRQuotaExceeded()

    report = asyncio.run(
        run_benchmark(
            build_manifest(public_cache, variants=("clean",)),
            public_cache,
            QuotaSTT(),
            provider_name="quota",
            report_path=tmp_path / "quota.json",
        )
    )
    assert not report["complete"]
    assert len(report["cases"]) == 1
    assert report["stopped_reason"] == "asr-hosted-quota-exhausted"
    assert {row["error_code"] for row in report["cases"]} == {"asr-hosted-quota-exhausted"}
    assert all(row["metrics"] is None for row in report["cases"])


def test_local_cli_requires_offline_artifacts_and_never_constructs_hosted_adapter(
    public_cache, tmp_path, monkeypatch, capsys
):
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "scripts/asr_benchmark.py"
    spec = importlib.util.spec_from_file_location("local_benchmark_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = FakeSTT()
    constructed = []
    monkeypatch.setattr(
        module, "local_base_details", lambda folder: {"model_revision": "a" * 40}
    )
    monkeypatch.setattr(module, "WhisperSTT", lambda **kw: constructed.append(kw) or provider)
    for name in ("HFHostedSTT", "GradioWhisperSTT", "GradioIndicSTT"):
        monkeypatch.setattr(
            module, name, lambda **kw: pytest.fail("hosted inference attempted")
        )
    manifest_path, report = tmp_path / "manifest.json", tmp_path / "local.json"
    manifest_path.write_text(json.dumps(build_manifest(public_cache, variants=("clean",))))
    common = [
        "run",
        "--provider",
        "whisper_local",
        "--manifest",
        str(manifest_path),
        "--audio-dir",
        str(public_cache),
        "--report",
        str(report),
    ]
    with pytest.raises(ValueError, match="local-model-dir"):
        asyncio.run(module.run(module.parser().parse_args(common)))
    local = [*common, "--local-model-dir", str(tmp_path)]
    with pytest.raises(ValueError, match="forbids"):
        asyncio.run(module.run(module.parser().parse_args([*local, "--allow-network"])))
    assert not constructed
    assert asyncio.run(module.run(module.parser().parse_args(local))) == 0
    result = read_json(report)
    assert result["provider"] == "whisper_local" and result["network_calls"] is False
    assert len(provider.requests) == 3
    assert constructed == [{"model_id": str(tmp_path.resolve()), "timeout_secs": 60}]
    with pytest.raises(ValueError, match="identity differs"):
        asyncio.run(
            run_benchmark(
                read_json(manifest_path),
                public_cache,
                provider,
                provider_name="hf_whisper_space",
                report_path=report,
                resume=True,
            )
        )
    capsys.readouterr()


def test_local_indic_cli_runs_all_cases_offline_and_marks_english_unsupported(
    public_cache, tmp_path, monkeypatch, capsys
):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts/asr_benchmark.py"
    spec = importlib.util.spec_from_file_location("indic_benchmark_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = FakeSTT()
    constructed = []
    monkeypatch.setattr(
        module, "local_indic_details",
        lambda config: {"model_revision": "a" * 40, "network_calls": False},
        raising=False,
    )
    monkeypatch.setattr(
        module, "IndicConformerSTT",
        lambda **kw: constructed.append(kw) or provider, raising=False,
    )
    for name in ("HFHostedSTT", "GradioWhisperSTT", "GradioIndicSTT", "WhisperSTT"):
        monkeypatch.setattr(module, name, lambda **kw: pytest.fail("another model attempted"))
    monkeypatch.setattr(
        module, "SpeechSettings",
        lambda **kw: type("Settings", (), {
            "indic_model_id": "ai4bharat/indic-conformer-600m-multilingual",
            "indic_model_revision": "a" * 40,
            "indic_trust_remote_code": True,
            "indic_decoder": "ctc",
            "stt_max_audio_secs": 30,
        })(),
    )
    manifest = build_manifest(
        public_cache, include_stitched_code_mix=True,
        variants=("clean", "noise", "telephone"),
    )
    manifest_path, report_path = tmp_path / "manifest.json", tmp_path / "indic.json"
    manifest_path.write_text(json.dumps(manifest))
    args = [
        "run", "--provider", "indic_local", "--manifest", str(manifest_path),
        "--audio-dir", str(public_cache), "--report", str(report_path),
    ]
    with pytest.raises(ValueError, match="forbids"):
        asyncio.run(module.run(module.parser().parse_args([*args, "--allow-network"])))
    assert not constructed
    assert asyncio.run(module.run(module.parser().parse_args(args))) == 1
    report = read_json(report_path)
    assert report["complete"] and len(report["cases"]) == 15
    assert report["network_calls"] is False
    assert len(provider.requests) == 12
    assert all(r.language_hint != "en" for r in provider.requests)
    english = [r for r in report["cases"] if r["language"] == "en"]
    assert len(english) == 3
    assert all(r["error_code"] == "asr-language-unsupported" and r["metrics"] is None for r in english)
    assert all(r["status"] == "ok" for r in report["cases"] if r["language"] != "en")
    assert constructed[0]["revision"] == "a" * 40
    assert constructed[0]["timeout_secs"] == 60
    assert constructed[0]["trust_remote_code"] is True
    capsys.readouterr()


def test_adaptive_local_cli_composes_pinned_models_and_preserves_mixed_hint(
    public_cache, tmp_path, monkeypatch, capsys
):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts/asr_benchmark.py"
    spec = importlib.util.spec_from_file_location("adaptive_benchmark_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = FakeSTT()
    constructed = []
    monkeypatch.setattr(
        module,
        "local_indic_details",
        lambda config: {"model_revision": "a" * 40, "network_calls": False},
    )
    monkeypatch.setattr(
        module,
        "local_base_details",
        lambda folder: {"model_revision": "b" * 40, "network_calls": False},
    )
    monkeypatch.setattr(
        module,
        "IndicConformerSTT",
        lambda **kwargs: constructed.append(("indic", kwargs)) or object(),
    )
    monkeypatch.setattr(
        module,
        "WhisperSTT",
        lambda **kwargs: constructed.append(("whisper", kwargs)) or object(),
    )
    monkeypatch.setattr(
        module,
        "LocalLanguageSTT",
        lambda indic, whisper: provider,
        raising=False,
    )
    for name in ("HFHostedSTT", "GradioWhisperSTT", "GradioIndicSTT"):
        monkeypatch.setattr(module, name, lambda **kw: pytest.fail("hosted inference attempted"))
    monkeypatch.setattr(
        module,
        "SpeechSettings",
        lambda **kw: type("Settings", (), {
            "indic_model_id": "ai4bharat/indic-conformer-600m-multilingual",
            "indic_model_revision": "a" * 40,
            "indic_trust_remote_code": True,
            "indic_decoder": "ctc",
            "stt_max_audio_secs": 30,
        })(),
    )
    manifest = build_manifest(
        public_cache, include_stitched_code_mix=True, variants=("clean",)
    )
    manifest_path, report_path = tmp_path / "manifest.json", tmp_path / "adaptive.json"
    manifest_path.write_text(json.dumps(manifest))
    args = [
        "run", "--provider", "adaptive_local", "--manifest", str(manifest_path),
        "--audio-dir", str(public_cache), "--report", str(report_path),
    ]
    with pytest.raises(ValueError, match="local-model-dir"):
        asyncio.run(module.run(module.parser().parse_args(args)))
    args += ["--local-model-dir", str(tmp_path)]
    with pytest.raises(ValueError, match="forbids"):
        asyncio.run(module.run(module.parser().parse_args([*args, "--allow-network"])))
    assert asyncio.run(module.run(module.parser().parse_args(args))) == 0
    report = read_json(report_path)
    assert report["provider"] == "adaptive_local" and report["network_calls"] is False
    assert {request.language_hint for request in provider.requests} == {
        "gu", "hi", "en", "gu-en", "hi-en"
    }
    assert report["provider_details"]["router_version"] == "phrase-span-v5"
    assert [kind for kind, _ in constructed] == ["indic", "whisper"]
    capsys.readouterr()


def test_local_comparison_pairs_audio_and_keeps_cold_and_unsupported_separate(
    public_cache, tmp_path
):
    from roma.eval.asr_comparison import compare_local_reports

    manifest = build_manifest(public_cache, variants=("clean", "noise"))
    base = asyncio.run(run_benchmark(
        manifest, public_cache, FakeSTT(), provider_name="whisper_local",
        report_path=tmp_path / "base.json",
    ))
    indic = asyncio.run(run_benchmark(
        manifest, public_cache, FakeSTT(), provider_name="indic_local",
        report_path=tmp_path / "indic.json",
    ))
    result = compare_local_reports(indic, base)
    assert result["expected_cases"] == 6
    assert result["shared_success_cases"] == 4
    assert result["shared_warm_cases"] == 3
    assert result["cold_case_ids"] == {
        "indic": "gu-0000:clean", "whisper": "en-0000:clean"
    }
    english = next(r for r in result["by_language"] if r["language"] == "en")
    assert english["indic"]["wer"] is None
    assert english["whisper"]["wer"] == 0
    assert english["shared_success_cases"] == 0
    assert result["shared_success"]["indic"]["wer"] == 0
    base["cases"][0]["audio_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="identity"):
        compare_local_reports(indic, base)


def test_local_comparison_rejects_mismatched_or_tampered_reports(public_cache, tmp_path):
    from copy import deepcopy

    from roma.eval.asr_comparison import compare_local_reports

    manifest = build_manifest(public_cache, variants=("clean",))
    base = asyncio.run(run_benchmark(
        manifest, public_cache, FakeSTT(), provider_name="whisper_local",
        report_path=tmp_path / "base.json",
    ))
    indic = asyncio.run(run_benchmark(
        manifest, public_cache, FakeSTT(), provider_name="indic_local",
        report_path=tmp_path / "indic.json",
    ))
    for change in (
        lambda r: r.update(manifest_sha256="f" * 64),
        lambda r: r.update(network_calls=True),
        lambda r: r.update(complete=False),
        lambda r: r["cases"].append(r["cases"][0]),
        lambda r: r["cases"][0].update(request_ms=float("nan")),
        lambda r: r["cases"][0]["metrics"].update(wer=123),
    ):
        bad = deepcopy(base)
        change(bad)
        with pytest.raises(ValueError):
            compare_local_reports(indic, bad)
