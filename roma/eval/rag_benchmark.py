"""Synthetic governed-retrieval regression evidence, separate from Claude review."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from roma.core.ai_contracts import EmbeddingIdentity, EmbeddingResult, LLMChunk
from roma.domain.conversation.stage import ConversationStage
from roma.domain.conversation.state import CallState
from roma.domain.knowledge import governance
from roma.domain.knowledge.ingestion import PIPELINE_VERSION, chunk_document
from roma.domain.knowledge.retrieval import Evidence, RetrievalFilters, cosine_similarity
from roma.domain.knowledge.routing import classify_turn
from roma.domain.safety.filter import screen
from roma.eval.project_readiness import load_matrix
from roma.services.knowledge_answer import PROMPT_VERSION, RULES, KnowledgeAnswerService
from roma.services.knowledge_retrieval import KnowledgeSearchService


def thresholds_from_matrix(matrix):
    """Share agreed numeric targets; fixture spans remain mechanical evidence only."""
    checks = {row["id"]: row for row in matrix["checks"]}
    quality = checks["rag.quality"]["targets"]
    return {
        "recall_at_5": quality["recall_at_5"]["value"],
        "mrr": quality["mrr"]["value"],
        "grounded_span_support": quality["groundedness"]["value"],
        "deferral_accuracy": quality["deferral_accuracy"]["value"],
        "safety_accuracy": quality["safety_rate"]["value"],
        "retrieval_p95_ms": checks["rag.latency"]["targets"]["retrieval_p95_ms"]["value"],
    }


THRESHOLDS = thresholds_from_matrix(load_matrix())


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def percentile(values, quantile):
    """Nearest-rank observed percentile; never invent a zero for missing samples."""
    if not values:
        return None
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Invalid latency samples")
    return sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)]


def summarize(rows):
    answerable = [row for row in rows if row["relevant"]]
    deferred = [row for row in rows if row["expected_deferral"]]
    failed_negative = [row for row in deferred if row["retrieved"]]
    searched_negative = [row for row in deferred if row["retrieval_ms"] is not None]
    searched_failed_negative = [row for row in searched_negative if row["retrieved"]]
    generated = [row for row in rows if not row["fallback"]]
    safety = [row for row in rows if row["safety_case"]]
    latencies = [row["retrieval_ms"] for row in rows if row["retrieval_ms"] is not None]

    def average(values):
        return sum(values) / len(values) if values else None

    return {
        "recall_at_5": average(
            [
                len(set(row["retrieved"][:5]) & set(row["relevant"]))
                / len(set(row["relevant"]))
                for row in answerable
            ]
        ),
        "mrr": average(
            [
                next(
                    (
                        1 / rank
                        for rank, key in enumerate(row["retrieved"], 1)
                        if key in row["relevant"]
                    ),
                    0,
                )
                for row in answerable
            ]
        ),
        "grounded_span_support": average([int(row["span_supported"]) for row in generated]),
        "deferral_accuracy": average([int(row["fallback"]) for row in deferred]),
        # The oracle selector can defer despite irrelevant admitted evidence.
        # Keep retrieval abstention separate from answer-release mechanics.
        "retrieval_abstention_accuracy": average(
            [int(not row["retrieved"]) for row in deferred]
        ),
        "negative_query_retrieval_failure_rate": average(
            [int(bool(row["retrieved"])) for row in deferred]
        ),
        "failed_negative_query_ids": [row["case_id"] for row in failed_negative],
        "search_executed_negative_query_failure_rate": average(
            [int(bool(row["retrieved"])) for row in searched_negative]
        ),
        "search_executed_failed_negative_query_ids": [
            row["case_id"] for row in searched_failed_negative
        ],
        "safety_accuracy": average([int(row["safe"] and row["fallback"]) for row in safety]),
        "route_accuracy": average([int(row["route_ok"]) for row in rows]),
        "retrieval_p50_ms": percentile(latencies, 0.50),
        "retrieval_p95_ms": percentile(latencies, 0.95),
        "retrieval_p99_ms": percentile(latencies, 0.99),
        "real_claude_groundedness": None,
        "counts": {
            "cases": len(rows),
            "answerable": len(answerable),
            "expected_deferrals": len(deferred),
            "failed_negative_queries": len(failed_negative),
            "search_executed_negative_queries": len(searched_negative),
            "search_executed_failed_negative_queries": len(searched_failed_negative),
            "generated": len(generated),
            "safety": len(safety),
            "retrieval_timed": len(latencies),
        },
    }


def chunk_key(source_key, version, ordinal, content_hash):
    return f"{source_key}:v{version}:{ordinal}:{content_hash}"


def load_golden(path):
    path = Path(path)
    if path.stat().st_size > 262144:
        raise ValueError("Golden manifest is too large")
    value = json.loads(path.read_text())
    validate_golden(value)
    return value


def validate_golden(value):
    if (
        value.get("schema") != "roma-rag-golden-v1"
        or value.get("data_classification") != "synthetic_test_only"
        or value.get("not_production_business_approval") is not True
        or not 4 <= len(value.get("sources", [])) <= 32
        or not 8 <= len(value.get("cases", [])) <= 200
    ):
        raise ValueError("Expected bounded synthetic RAG golden manifest")
    as_of = datetime.fromisoformat(value["as_of"])
    if as_of.utcoffset() is None:
        raise ValueError("Golden evaluation needs an aware clock")
    keys, source_names = {}, set()
    for source in value["sources"]:
        if (
            not source["source_key"].startswith("rag-golden/")
            or source["source_key"] in source_names
            or source["category"] not in {"course", "branch", "schedule", "policy"}
            or source["status"] not in {"active", "archived"}
        ):
            raise ValueError("Invalid synthetic source")
        source_names.add(source["source_key"])
        governance.validate_content(source["content"])
        for chunk in chunk_document(source["content"], language=source["language"]):
            keys[
                chunk_key(
                    source["source_key"], source["version"], chunk.ordinal, chunk.content_hash
                )
            ] = (source, chunk)
    ids = set()
    for case in value["cases"]:
        if (
            case["case_id"] in ids
            or case["split"] not in {"calibration", "heldout"}
            or case["input_language"] not in {"gu", "hi", "en", "hi-en"}
            or not 1 <= len(case["query"]) <= 2000
            or any(
                key not in keys or keys[key][0]["status"] != "active"
                for key in case["relevant_chunk_keys"]
            )
            or bool(case["relevant_chunk_keys"]) != (case["quote"] is not None)
            or any(
                case["quote"] not in keys[key][1].content for key in case["relevant_chunk_keys"]
            )
        ):
            raise ValueError("Invalid golden case identity or evidence")
        if case["expected_route"] not in {
            "knowledge",
            "booking",
            "state",
            "safety",
            "no_evidence",
            "deterministic",
        }:
            raise ValueError("Invalid expected golden route")
        ids.add(case["case_id"])
    grid = value["calibration_thresholds"]
    if (
        not grid
        or len(grid) > 12
        or len(grid) != len(set(grid))
        or any(not math.isfinite(v) or not -1 <= v <= 1 for v in grid)
    ):
        raise ValueError("Invalid fixed calibration grid")


class FixtureSelector:
    """Oracle fixture selection exercises release checks; it does not score Claude."""

    def __init__(self, quote):
        self.quote = quote

    async def generate(self, request):
        payload = json.loads(request.messages[-1].content)
        match = next(
            (item for item in payload["EVIDENCE"] if self.quote and self.quote in item["text"]),
            None,
        )
        yield LLMChunk(
            json.dumps(
                {
                    "chunk_id": match["chunk_id"] if match else None,
                    "quote": self.quote if match else None,
                }
            ),
            finish_reason="stop",
        )


class ObservedSearch:
    def __init__(self, search):
        self.search_service = search
        self.result, self.latency_ms = None, None

    async def search(self, *args, **kwargs):
        started = perf_counter()
        try:
            self.result = await self.search_service.search(*args, **kwargs)
            return self.result
        finally:
            self.latency_ms = (perf_counter() - started) * 1000

    async def revalidate(self, *args, **kwargs):
        return await self.search_service.revalidate(*args, **kwargs)

    async def record(self, *args, **kwargs):
        return await self.search_service.record(*args, **kwargs)


def material_fingerprint(golden, identity, *, threshold, k=5, claude_model="claude-haiku-5-5"):
    return digest(
        {
            "golden_sha256": digest(golden),
            "embedding": asdict(identity),
            "chunker": PIPELINE_VERSION,
            "k": k,
            "similarity_threshold": threshold,
            "acceptance_thresholds": THRESHOLDS,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": digest(RULES),
            "configured_claude_model": claude_model,
            "generation_evaluator": "deterministic_fixture_selector-v1",
            "output_policy": "code_mix",
        }
    )


async def evaluate_cases(golden, search, stable_keys, *, split=None):
    rows = []
    for case in golden["cases"]:
        if split is not None and case["split"] != split:
            continue
        decision = classify_turn(CallState(stage=ConversationStage.VALUE), case["query"])
        category = decision.topic if str(decision.route) == "knowledge" else case["category"]
        observed = ObservedSearch(search)
        answer = await KnowledgeAnswerService(observed, FixtureSelector(case["quote"])).answer(
            case["query"],
            RetrievalFilters(category=category, language="hi-en"),
            output_language="code_mix",
        )
        retrieved = (
            [
                stable_keys.get(item.chunk_id, "unknown-chunk")
                for item in observed.result.evidence
            ]
            if observed.result
            else []
        )
        selected = [stable_keys.get(item.chunk_id, "unknown-chunk") for item in answer.evidence]
        supported = (
            not answer.fallback
            and bool(answer.evidence)
            and answer.text == case["quote"]
            and all(answer.text in evidence.content for evidence in answer.evidence)
            and bool(set(selected) & set(case["relevant_chunk_keys"]))
        )
        expected_bypass = case["expected_route"] != "knowledge"
        actual_route = str(
            classify_turn(CallState(stage=ConversationStage.VALUE), case["query"]).route
        )
        rows.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "input_language": case["input_language"],
                "category": case["category"],
                "relevant": case["relevant_chunk_keys"],
                "retrieved": retrieved,
                "expected_deferral": not bool(case["relevant_chunk_keys"]),
                "fallback": answer.fallback,
                "span_supported": supported,
                "safe": screen(answer.text).allowed,
                "safety_case": case["safety_case"],
                "retrieval_ms": observed.latency_ms,
                "route_ok": actual_route == case["expected_route"]
                and expected_bypass == (answer.reason == "route_bypass")
                and (expected_bypass or category == case["category"]),
                "outcome": answer.reason,
            }
        )
    return rows


def report_for(golden, rows, identity, *, mode, threshold, calibration=None, started_at=None):
    summary = summarize([row for row in rows if row["split"] == "heldout"])
    checks = {
        key: summary[key] is not None
        and (summary[key] <= minimum if key == "retrieval_p95_ms" else summary[key] >= minimum)
        for key, minimum in THRESHOLDS.items()
    }
    return {
        "schema": "roma-rag-benchmark-v1",
        "mode": mode,
        "dataset_sha256": digest(golden),
        "material_fingerprint": material_fingerprint(golden, identity, threshold=threshold),
        "embedding_identity": asdict(identity),
        "similarity_threshold": threshold,
        "k": 5,
        "started_at": started_at or datetime.now(UTC).isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "complete": len(rows) == len(golden["cases"]),
        "generation_evaluator": "deterministic_fixture_selector",
        "generation_evidence": "synthetic oracle selector; exact-span mechanical support only",
        "embedding_inference_executed": mode == "local",
        "pgvector_executed": mode == "local",
        "latency_scope": "query normalization, embedding and exact SQL search; excludes generation/revalidation/audit"
        if mode == "local"
        else "software fixture timing only; no local model or SQL latency claim",
        "retrieval_diagnostic_scope": "Expected-deferral cases; search-executed means the observed search method ran, including rejected/unavailable outcomes, without implying completed embedding or SQL inference. Diagnostics do not change acceptance thresholds.",
        "retrieval_diagnostic_failures": [
            row["case_id"] for row in rows if row["expected_deferral"] and row["retrieved"]
        ],
        "calibration": calibration,
        "cases": rows,
        "summary": summary,
        "language_summaries": {
            language: summarize(
                [
                    row
                    for row in rows
                    if row["split"] == "heldout" and row["input_language"] == language
                ]
            )
            for language in ("gu", "hi", "en", "hi-en")
        },
        "thresholds": THRESHOLDS,
        "mechanical_checks": checks,
        "mechanical_checks_passed": all(checks.values()),
        "quality_gate_passed": False,
        "native_review_passed": False,
        "paid_claude_called": False,
        "failures": [
            row["case_id"]
            for row in rows
            if (row["relevant"] and not row["span_supported"])
            or (row["expected_deferral"] and (not row["fallback"] or row["retrieved"]))
            or not row["safe"]
            or not row["route_ok"]
        ],
        "limits": "Synthetic fixture corpus; deterministic oracle selector measures release mechanics, not Claude groundedness or semantic judgment. Local mode measures actual cache-only embeddings and exact pgvector retrieval. No native or production acceptance is implied.",
    }


class FixtureEmbedding:
    def __init__(self, golden):
        self.dimension = len(golden["sources"]) + 1
        self.identity = EmbeddingIdentity(
            "fixture-table",
            "1",
            self.dimension,
            "synthetic-unit-basis-v1",
            digest(golden["sources"]),
        )
        self.texts = {
            source["content"]: index for index, source in enumerate(golden["sources"])
        }
        by_source = {
            source["source_key"]: index for index, source in enumerate(golden["sources"])
        }
        for case in golden["cases"]:
            self.texts[case["query"]] = (
                by_source[case["relevant_chunk_keys"][0].split(":v")[0]]
                if case["relevant_chunk_keys"]
                else self.dimension - 1
            )

    async def embed(self, request):
        rows = []
        for text in request.texts:
            index = self.texts.get(text, self.dimension - 1)
            rows.append(tuple(float(i == index) for i in range(self.dimension)))
        return EmbeddingResult(tuple(rows), self.dimension, self.identity)


class FixtureRepository:
    def __init__(self, golden):
        self.now = datetime.fromisoformat(golden["as_of"])
        self.rows, self.stable_keys, self.audit = [], {}, []
        self.provider = FixtureEmbedding(golden)
        for source in golden["sources"]:
            actor = uuid5(NAMESPACE_URL, "rag-golden/actor")
            doc = SimpleNamespace(
                **source,
                id=uuid5(NAMESPACE_URL, source["source_key"]),
                title="TEST ONLY fixture",
                source_reference="synthetic/fixture-v1",
                owner_id=actor,
                course_id=None,
                branch_id=None,
                effective_from=self.now - timedelta(days=1),
                effective_until=None,
                approved_by=None,
                approved_at=None,
                approval_reference=None,
                approved_hash=None,
            )
            doc.status = "draft"
            doc.content_hash = governance.content_hash(doc)
            governance.approve(
                doc, actor_id=actor, reference="synthetic-fixture-review-v1", now=self.now
            )
            governance.activate(doc, now=self.now)
            if source["status"] == "archived":
                governance.archive(doc, now=self.now)
            for chunk in chunk_document(doc.content, language=doc.language):
                chunk_id = uuid5(
                    NAMESPACE_URL,
                    chunk_key(
                        source["source_key"],
                        source["version"],
                        chunk.ordinal,
                        chunk.content_hash,
                    ),
                )
                self.stable_keys[chunk_id] = chunk_key(
                    source["source_key"], source["version"], chunk.ordinal, chunk.content_hash
                )
                self.rows.append((doc, chunk, chunk_id))

    def eligible(self, doc, filters):
        return (
            doc.category == filters.category
            and doc.language in filters.languages
            and governance.eligible(
                doc,
                now=self.now,
                language=doc.language,
                course_id=filters.course_id,
                branch_id=filters.branch_id,
            )
        )

    async def search_chunks(self, *, query_vector, identity, filters, now, k, threshold):
        if identity != self.provider.identity:
            raise ValueError("Different fixture vector identity")
        result = []
        for doc, chunk, chunk_id in self.rows:
            if not self.eligible(doc, filters):
                continue
            vector = (
                await self.provider.embed(SimpleNamespace(texts=(chunk.content,)))
            ).vectors[0]
            score = cosine_similarity(query_vector, vector)
            if score >= threshold:
                result.append(
                    Evidence(
                        chunk_id,
                        doc.id,
                        chunk.content,
                        chunk.content_hash,
                        doc.version,
                        doc.language,
                        score,
                        chunk.source_section,
                    )
                )
        return tuple(sorted(result, key=lambda item: (-item.score, str(item.chunk_id)))[:k])

    async def revalidate_evidence(self, *, chunk_ids, identity, filters, now):
        return tuple(
            Evidence(
                chunk_id,
                doc.id,
                chunk.content,
                chunk.content_hash,
                doc.version,
                doc.language,
                0,
                chunk.source_section,
            )
            for doc, chunk, chunk_id in self.rows
            if chunk_id in chunk_ids and self.eligible(doc, filters)
        )

    async def record_retrieval(self, metadata, *, now):
        self.audit.append(metadata)


async def run_mock_benchmark(golden):
    validate_golden(golden)
    repo = FixtureRepository(golden)
    search = KnowledgeSearchService(
        repo, repo.provider, audit_repository=repo, now_fn=lambda: repo.now
    )
    rows = await evaluate_cases(golden, search, repo.stable_keys)
    return report_for(golden, rows, repo.provider.identity, mode="mock", threshold=0.8)


def guard_test_database_url(url, *, test_only):
    """Check explicit isolated target before any connection; never read .env."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        target = make_url(url)
        allowed = (
            test_only is True
            and target.drivername == "postgresql+asyncpg"
            and target.host in {"localhost", "127.0.0.1", "::1"}
            and re.fullmatch(r"roma_rag_benchmark_[a-z0-9_]{1,40}", target.database or "")
            and not target.query
        )
    except (ValueError, TypeError, ArgumentError):
        allowed = False
    if not allowed:
        raise ValueError(
            "A loopback TEST ONLY roma_rag_benchmark_ database and --test-only are required"
        )
    return url


def select_calibrated_threshold(candidates, *, default=0.8):
    measured = []
    for threshold, rows in candidates:
        if not rows or any(row["split"] != "calibration" for row in rows):
            raise ValueError("Calibration cannot inspect heldout observations")
        relevant = [row for row in rows if row["relevant"]]
        absent = [row for row in rows if row["expected_deferral"]]
        recall = (
            sum(
                len(set(row["relevant"]) & set(row["retrieved"])) / len(set(row["relevant"]))
                for row in relevant
            )
            / len(relevant)
            if relevant
            else 0
        )
        abstention = sum(not row["retrieved"] for row in absent) / len(absent) if absent else 0
        measured.append(
            {
                "threshold": threshold,
                "recall_at_5": recall,
                "no_evidence_retrieval_abstention": abstention,
                "objective": (recall + abstention) / 2,
                "case_count": len(rows),
            }
        )
    if not measured:
        raise ValueError("Calibration requires fixed candidates")
    best = max(
        measured,
        key=lambda row: (row["objective"], -abs(row["threshold"] - default), row["threshold"]),
    )
    return {
        "method": "fixed_grid_mean_recall_and_no_evidence_abstention-v1",
        "split": "calibration",
        "selected_threshold": best["threshold"],
        "heldout_used_for_selection": False,
        "candidates": measured,
    }


async def seed_local_fixtures(golden, factory, provider):
    from sqlalchemy import func, select, text

    from roma.core.migration_settings import current_schema_head
    from roma.domain.knowledge.ingestion import pipeline_identity
    from roma.repositories.postgres.knowledge import KnowledgeRepository
    from roma.repositories.postgres.knowledge_ingestion import KnowledgeIngestionRepository
    from roma.repositories.postgres.models import User
    from roma.repositories.postgres.models.knowledge import KnowledgeChunk, KnowledgeDocument
    from roma.schemas.knowledge import UploadKnowledgeRequest
    from roma.services.knowledge_ingestion import KnowledgeIngestionService

    async with factory() as session:
        if (
            await session.scalar(text("SELECT version_num FROM alembic_version"))
        ) != current_schema_head():
            raise ValueError("TEST ONLY benchmark requires the current migrated schema")
        if await session.scalar(select(func.count()).select_from(KnowledgeDocument)):
            raise ValueError("TEST ONLY benchmark requires a fresh empty knowledge database")
    actor, now = (
        uuid5(NAMESPACE_URL, "rag-golden/actor"),
        datetime.fromisoformat(golden["as_of"]),
    )
    async with factory() as session, session.begin():
        session.add(
            User(
                id=actor,
                email="rag-fixture@example.invalid",
                display_name="TEST ONLY benchmark",
                password_hash="disabled-fixture-account",
                is_active=False,
            )
        )
    governance_repo, jobs = KnowledgeRepository(factory), KnowledgeIngestionRepository(factory)
    ingestion = KnowledgeIngestionService(
        jobs, provider, identity=provider.identity, now_fn=lambda: now
    )
    for source in golden["sources"]:
        request = UploadKnowledgeRequest(
            source_key=source["source_key"],
            source_reference="synthetic/fixture-v1",
            title="TEST ONLY fixture",
            version=source["version"],
            category=source["category"],
            language=source["language"],
            owner_id=actor,
            effective_from=now - timedelta(days=1),
            content=source["content"],
        )
        doc = await governance_repo.create(request, actor_id=actor, ingestion_required=True)
        await governance_repo.transition(
            doc.id, "approve", actor_id=actor, now=now, reference=golden["approval_reference"]
        )
        job = await jobs.enqueue_ingestion(
            doc.id,
            doc.content_hash,
            pipeline_identity(provider.identity),
            identity=provider.identity,
            now=now,
        )
        if await ingestion.process(job.id) != "succeeded":
            raise ValueError("TEST ONLY fixture ingestion did not complete")
        await governance_repo.transition(
            doc.id, "activate", actor_id=actor, expected_version=doc.version, now=now
        )
        if source["status"] == "archived":
            await governance_repo.transition(
                doc.id, "archive", actor_id=actor, expected_version=doc.version, now=now
            )
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    KnowledgeChunk.id,
                    KnowledgeDocument.source_key,
                    KnowledgeDocument.version,
                    KnowledgeChunk.ordinal,
                    KnowledgeChunk.content_hash,
                ).join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            )
        ).all()
    return {
        row.id: chunk_key(row.source_key, row.version, row.ordinal, row.content_hash)
        for row in rows
    }


async def run_local_benchmark(golden, factory, provider, *, calibrate=True, timeout_secs=2.0):
    from roma.repositories.postgres.knowledge import KnowledgeRepository
    from roma.repositories.postgres.knowledge_search import KnowledgeSearchRepository

    validate_golden(golden)
    started_at = datetime.now(UTC).isoformat()
    keys = await seed_local_fixtures(golden, factory, provider)
    now = datetime.fromisoformat(golden["as_of"])
    repository, audit = KnowledgeSearchRepository(factory), KnowledgeRepository(factory)

    def search_for(threshold):
        return KnowledgeSearchService(
            repository,
            provider,
            audit_repository=audit,
            k=5,
            threshold=threshold,
            timeout_secs=timeout_secs,
            now_fn=lambda: now,
        )

    selected, calibration = golden["default_similarity_threshold"], None
    if calibrate:
        candidates = []
        for threshold in golden["calibration_thresholds"]:
            candidates.append(
                (
                    threshold,
                    await evaluate_cases(
                        golden, search_for(threshold), keys, split="calibration"
                    ),
                )
            )
        calibration = select_calibrated_threshold(candidates, default=selected)
        selected = calibration["selected_threshold"]
    rows = await evaluate_cases(golden, search_for(selected), keys)
    report = report_for(
        golden,
        rows,
        provider.identity,
        mode="local",
        threshold=selected,
        calibration=calibration,
        started_at=started_at,
    )
    report["model_warmup"] = (
        "artifact verification/model load occurs during fixture ingestion before retrieval timing"
    )
    return report


def benchmark_evidence(report, report_path):
    """Only actual aggregate values/provenance enter generic SQL evidence tables."""
    from roma.repositories.postgres.benchmarks import (
        AggregateMetric,
        BenchmarkEvidence,
        FailureArtifact,
        ModelEvidence,
    )

    path = Path(report_path).resolve()
    with path.open("rb") as stream:
        report_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    identity = report["embedding_identity"]
    model = ModelEvidence(
        "local_e5" if report["mode"] == "local" else "fixture_table",
        identity["model"],
        identity["revision"],
        task="embedding",
        checksum_sha256=identity["artifacts_sha256"],
        license_name="MIT" if report["mode"] == "local" else "Synthetic fixture",
    )
    metrics = []
    for language, summary in {"all": report["summary"], **report["language_summaries"]}.items():
        for key, denominator in (
            ("recall_at_5", "answerable"),
            ("mrr", "answerable"),
            ("grounded_span_support", "generated"),
            ("deferral_accuracy", "expected_deferrals"),
            ("retrieval_abstention_accuracy", "expected_deferrals"),
            ("negative_query_retrieval_failure_rate", "expected_deferrals"),
            ("search_executed_negative_query_failure_rate", "search_executed_negative_queries"),
            ("safety_accuracy", "safety"),
            ("route_accuracy", "cases"),
            ("retrieval_p50_ms", "retrieval_timed"),
            ("retrieval_p95_ms", "retrieval_timed"),
            ("retrieval_p99_ms", "retrieval_timed"),
        ):
            if summary.get(key) is not None:
                metrics.append(
                    AggregateMetric(
                        model.key,
                        language,
                        key,
                        summary[key],
                        "ms" if key.endswith("_ms") else "ratio",
                        summary["counts"][denominator],
                        {
                            "split": "heldout",
                            "generation_evaluator": report["generation_evaluator"],
                            "quality_gate_passed": False,
                        },
                    )
                )
    return BenchmarkEvidence(
        run_key="rag-"
        + digest(
            {"fingerprint": report["material_fingerprint"], "started_at": report["started_at"]}
        )[:56],
        dataset_name="roma-synthetic-rag-golden",
        dataset_version="v1",
        dataset_sha256=report["dataset_sha256"],
        status="succeeded" if report["complete"] else "failed",
        sample_count=len(report["cases"]),
        started_at=datetime.fromisoformat(report["started_at"]),
        ended_at=datetime.fromisoformat(report["completed_at"]),
        models=(model,),
        metrics=tuple(metrics),
        config={
            "material_fingerprint": report["material_fingerprint"],
            "similarity_threshold": report["similarity_threshold"],
            "generation_evaluator": report["generation_evaluator"],
            "real_claude_evaluated": False,
            "quality_gate_passed": False,
        },
        failure_artifacts=(FailureArtifact(str(path), report_sha),),
    )
