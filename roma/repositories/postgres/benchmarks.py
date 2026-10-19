"""Persist completed, non-PII lab evidence in one short transaction.

Finish inference and write failure artifacts before calling ``save``. The run key
is immutable: retries return the original ID, while changed evidence conflicts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from roma.domain.persistence import PersistenceConflict
from roma.repositories.postgres.models import BenchmarkResult, BenchmarkRun, ModelRegistry

ModelKey = tuple[str, str, str, str]
_STORE_KEY = "roma_benchmark_evidence"
_PRIVATE_KEYS = {
    "transcript",
    "transcripts",
    "hypothesis",
    "hypotheses",
    "reference",
    "reference_text",
    "phone",
    "phone_number",
    "authorization",
    "token",
    "access_token",
    "api_key",
    "secret",
    "password",
    "audio",
    "audio_bytes",
}


@dataclass(frozen=True)
class ModelEvidence:
    provider: str
    name: str
    version: str
    task: str = "stt"
    artifact_uri: str | None = None
    checksum_sha256: str | None = None
    license_name: str | None = None

    @property
    def key(self) -> ModelKey:
        return self.provider, self.name, self.version, self.task


@dataclass(frozen=True)
class AggregateMetric:
    model_key: ModelKey
    language: str
    metric: str
    value: Decimal | float | int
    unit: str
    sample_count: int
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureArtifact:
    """Identity of a separately retained public/synthetic failure report."""

    path: str
    sha256: str


@dataclass(frozen=True)
class BenchmarkEvidence:
    run_key: str
    dataset_name: str
    dataset_version: str
    dataset_sha256: str
    status: str
    sample_count: int
    started_at: datetime
    ended_at: datetime
    models: tuple[ModelEvidence, ...]
    metrics: tuple[AggregateMetric, ...]
    config: dict[str, object] = field(default_factory=dict)
    environment: dict[str, object] = field(default_factory=dict)
    failure_artifacts: tuple[FailureArtifact, ...] = ()


@dataclass(frozen=True)
class BenchmarkSaveResult:
    run_id: UUID
    created: bool


def _label(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Invalid {name}")


def _sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Invalid {name}")


def _location(value: str) -> None:
    _label(value, "artifact location", 4096)
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "Artifact locations must not contain credentials, queries or fragments"
        )


def _json_object(value: dict[str, object]) -> dict[str, object]:
    def check(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str) or key.lower() in _PRIVATE_KEYS:
                    raise ValueError("Benchmark metadata contains a private or non-string key")
                check(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                check(nested)

    if not isinstance(value, dict):
        raise ValueError("Benchmark metadata must be a JSON object")
    check(value)
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise ValueError("Benchmark metadata must contain finite JSON values") from error


def _number(value: Decimal | float | int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, float, int)):
        raise ValueError("Metric must be a finite numeric value")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) >= Decimal("1000000000000"):
            raise ValueError("Metric exceeds finite Numeric(20, 8) range")
        rounded = number.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
        if abs(rounded) >= Decimal("1000000000000"):
            raise ValueError("Rounded metric exceeds Numeric(20, 8) range")
        return rounded
    except InvalidOperation as error:
        raise ValueError("Invalid numeric metric") from error


def _prepare(evidence: BenchmarkEvidence) -> tuple[dict, list[dict], list[dict]]:
    """Validate and detach mutable input before acquiring a database session."""
    for value, name, limit in (
        (evidence.run_key, "run key", 128),
        (evidence.dataset_name, "dataset name", 255),
        (evidence.dataset_version, "dataset version", 128),
    ):
        _label(value, name, limit)
    _sha256(evidence.dataset_sha256, "dataset checksum")
    if evidence.status not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("Only completed benchmark runs can be saved")
    if type(evidence.sample_count) is not int or evidence.sample_count < 0:
        raise ValueError("Invalid benchmark sample count")
    if evidence.status == "succeeded" and evidence.sample_count == 0:
        raise ValueError("Successful benchmark requires samples")
    for timestamp in (evidence.started_at, evidence.ended_at):
        if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
            raise ValueError("Benchmark timestamps must be timezone-aware")
    if evidence.ended_at < evidence.started_at:
        raise ValueError("Benchmark end precedes start")
    if not evidence.models:
        raise ValueError("Benchmark requires model provenance")
    models = []
    identities = set()
    for model in evidence.models:
        for value, name, limit in (
            (model.provider, "model provider", 128),
            (model.name, "model name", 255),
            (model.version, "model version", 128),
        ):
            _label(value, name, limit)
        if model.task not in {"stt", "llm", "tts", "embedding", "reranker", "vad"}:
            raise ValueError("Invalid model task")
        if model.key in identities:
            raise ValueError("Duplicate benchmark model identity")
        identities.add(model.key)
        if model.checksum_sha256 is not None:
            _sha256(model.checksum_sha256, "model checksum")
        if model.artifact_uri is not None:
            _location(model.artifact_uri)
        if model.license_name is not None:
            _label(model.license_name, "model license", 255)
        models.append(
            dict(
                provider=model.provider,
                name=model.name,
                version=model.version,
                task=model.task,
                artifact_uri=model.artifact_uri,
                checksum_sha256=model.checksum_sha256,
                license_name=model.license_name,
            )
        )
    metrics = []
    measurements = set()
    for metric in evidence.metrics:
        if metric.model_key not in identities:
            raise ValueError("Metric refers to an undeclared model")
        for value, name, limit in (
            (metric.language, "metric language", 32),
            (metric.metric, "metric name", 64),
            (metric.unit, "metric unit", 32),
        ):
            _label(value, name, limit)
        identity = metric.model_key, metric.language, metric.metric
        if identity in measurements:
            raise ValueError("Duplicate metric identity; encode scope in the metric name")
        measurements.add(identity)
        if (
            type(metric.sample_count) is not int
            or not 0 < metric.sample_count <= evidence.sample_count
        ):
            raise ValueError("Metric sample count must fit the benchmark sample count")
        numeric_value = _number(metric.value)
        metrics.append(
            dict(
                model_key=metric.model_key,
                language=metric.language,
                metric=metric.metric,
                value=numeric_value,
                unit=metric.unit,
                sample_count=metric.sample_count,
                metadata_=_json_object(metric.metadata),
            )
        )
    if evidence.status == "succeeded" and not metrics:
        raise ValueError("Successful benchmark requires aggregate measurements")
    artifacts = []
    for artifact in evidence.failure_artifacts:
        _location(artifact.path)
        _sha256(artifact.sha256, "failure artifact checksum")
        artifacts.append(dict(path=artifact.path, sha256=artifact.sha256))
    if evidence.status == "failed" and not artifacts:
        raise ValueError("Failed benchmark requires a failure artifact identity")
    artifacts.sort(key=lambda row: (row["path"], row["sha256"]))
    models.sort(
        key=lambda row: tuple(row[key] for key in ("provider", "name", "version", "task"))
    )
    config = _json_object(evidence.config)
    environment = _json_object(evidence.environment)
    if _STORE_KEY in config:
        raise ValueError("Reserved benchmark configuration key")
    run = dict(
        run_key=evidence.run_key,
        dataset_name=evidence.dataset_name,
        dataset_version=evidence.dataset_version,
        dataset_sha256=evidence.dataset_sha256,
        status=evidence.status,
        sample_count=evidence.sample_count,
        started_at=evidence.started_at,
        ended_at=evidence.ended_at,
        config=config,
        environment=environment,
    )
    # Ordering is irrelevant; original precision remains part of immutable evidence.
    canonical = dict(
        run,
        started_at=evidence.started_at.isoformat(),
        ended_at=evidence.ended_at.isoformat(),
        models=sorted(
            models,
            key=lambda row: tuple(row[k] for k in ("provider", "name", "version", "task")),
        ),
        metrics=sorted(
            [
                dict(row, value=str(metric.value))
                for row, metric in zip(metrics, evidence.metrics, strict=True)
            ],
            key=lambda row: (row["model_key"], row["language"], row["metric"]),
        ),
        failure_artifacts=sorted(artifacts, key=lambda row: (row["path"], row["sha256"])),
    )
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    config[_STORE_KEY] = dict(schema_version=1, sha256=digest, failure_artifacts=artifacts)
    return run, models, metrics


class PostgresBenchmarkStore:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, evidence: BenchmarkEvidence) -> BenchmarkSaveResult:
        run_values, models, metrics = _prepare(evidence)
        proposed_id = uuid4()
        async with self._session_factory() as session, session.begin():
            inserted_id = await session.scalar(
                insert(BenchmarkRun)
                .values(id=proposed_id, **run_values)
                .on_conflict_do_nothing(index_elements=[BenchmarkRun.run_key])
                .returning(BenchmarkRun.id)
            )
            if inserted_id is None:
                existing = (
                    await session.execute(
                        select(BenchmarkRun.id, BenchmarkRun.config).where(
                            BenchmarkRun.run_key == evidence.run_key
                        )
                    )
                ).one()
                if existing.config.get(_STORE_KEY) != run_values["config"][_STORE_KEY]:
                    raise PersistenceConflict("Benchmark run key has different evidence")
                return BenchmarkSaveResult(existing.id, created=False)
            model_ids = {}
            for values in models:
                await session.execute(
                    insert(ModelRegistry)
                    .values(id=uuid4(), **values)
                    .on_conflict_do_nothing(
                        index_elements=[
                            ModelRegistry.provider,
                            ModelRegistry.name,
                            ModelRegistry.version,
                            ModelRegistry.task,
                        ]
                    )
                )
                model = await session.scalar(
                    select(ModelRegistry).where(
                        ModelRegistry.provider == values["provider"],
                        ModelRegistry.name == values["name"],
                        ModelRegistry.version == values["version"],
                        ModelRegistry.task == values["task"],
                    )
                )
                if model is None or any(
                    getattr(model, key) != value for key, value in values.items()
                ):
                    raise PersistenceConflict(
                        "Benchmark model identity has different provenance"
                    )
                model_ids[(model.provider, model.name, model.version, model.task)] = model.id
            for values in metrics:
                values = dict(values)
                model_id = model_ids[values.pop("model_key")]
                session.add(
                    BenchmarkResult(id=uuid4(), run_id=inserted_id, model_id=model_id, **values)
                )
        return BenchmarkSaveResult(inserted_id, created=True)
