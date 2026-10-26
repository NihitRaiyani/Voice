"""Pinned cache-only E5 embeddings. No downloads, remote code or cloud fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from roma.core.ai_contracts import (
    EmbeddingIdentity,
    EmbeddingRequest,
    EmbeddingResult,
    ProviderUnavailable,
)

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
PREPROCESSING = "e5-prefix-mean-l2-v1"
Encoder = Callable[[tuple[str, ...]], Sequence[Sequence[float]]]
RuntimeFactory = Callable[[Path, int], Encoder]


def validate_embedding_result(
    result: EmbeddingResult, *, count: int, identity: EmbeddingIdentity
) -> EmbeddingResult:
    if (
        result.identity != identity
        or result.dimension != identity.dimension
        or len(result.vectors) != count
    ):
        raise ProviderUnavailable("embedding-output-identity-invalid")
    for vector in result.vectors:
        if len(vector) != identity.dimension or any(
            not math.isfinite(value) for value in vector
        ):
            raise ProviderUnavailable("embedding-output-shape-invalid")
        if not math.isclose(
            sum(value * value for value in vector), 1.0, rel_tol=1e-4, abs_tol=1e-4
        ):
            raise ProviderUnavailable("embedding-output-norm-invalid")
    return result


def _runtime(root: Path, threads: int) -> Encoder:
    # Imports and loading occur only after manifest/artifact verification.
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(threads)
    tokenizer = AutoTokenizer.from_pretrained(
        str(root), local_files_only=True, trust_remote_code=False
    )
    model = (
        AutoModel.from_pretrained(
            str(root), local_files_only=True, trust_remote_code=False, use_safetensors=True
        )
        .to("cpu")
        .eval()
    )

    def encode(texts: tuple[str, ...]) -> Sequence[Sequence[float]]:
        batch = tokenizer(list(texts), padding=True, truncation=False, return_tensors="pt")
        if batch["input_ids"].shape[1] > 512:
            raise ValueError("embedding-token-bound-exceeded")
        with torch.inference_mode():
            hidden = model(**batch).last_hidden_state
            mask = batch["attention_mask"][..., None].bool()
            pooled = hidden.masked_fill(~mask, 0.0).sum(1) / mask.sum(1)
            return torch.nn.functional.normalize(pooled, p=2, dim=1).tolist()

    return encode


class LocalMultilingualEmbedding:
    def __init__(
        self,
        *legacy,
        manifest_path: str | Path = "",
        timeout_secs: float = 10.0,
        max_text_bytes: int = 8192,
        max_batch_size: int = 4,
        cpu_threads: int = 2,
        runtime_factory: RuntimeFactory | None = None,
    ):
        if (
            not math.isfinite(timeout_secs)
            or not 0 < timeout_secs <= 60
            or not 1 <= max_batch_size <= 4
            or not 1 <= cpu_threads <= 2
            or not 1 <= max_text_bytes <= 8192
        ):
            raise ValueError("embedding-resource-bounds-invalid")
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self.timeout_secs = timeout_secs
        self.max_text_bytes = max_text_bytes
        self.max_batch_size = max_batch_size
        self.cpu_threads = cpu_threads
        self._runtime_factory = runtime_factory or _runtime
        self._encoder: Encoder | None = None
        self._identity: EmbeddingIdentity | None = None
        self._files: dict[str, str] = {}
        self._task: asyncio.Task | None = None

    @property
    def identity(self) -> EmbeddingIdentity:
        if self._identity is None:
            if self.manifest_path is None:
                raise ProviderUnavailable("embedding-manifest-unavailable")
            try:
                if self.manifest_path.stat().st_size > 32768:
                    raise ValueError("manifest bound")
                manifest = json.loads(self.manifest_path.read_text())
                files = manifest["files"]
                required = {
                    "config.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "model.safetensors",
                }
                allowed = required | {
                    "special_tokens_map.json",
                    "sentencepiece.bpe.model",
                    "README.md",
                }
                if (
                    manifest["schema"] != "roma-embedding-manifest-v1"
                    or manifest["model"] != MODEL
                    or manifest["revision"] != REVISION
                    or manifest["dimension"] != 384
                    or manifest["preprocessing"] != PREPROCESSING
                    or not isinstance(files, dict)
                    or not required <= files.keys()
                    or not files.keys() <= allowed
                    or any(
                        not isinstance(v, str) or not re.fullmatch("[0-9a-f]{64}", v)
                        for v in files.values()
                    )
                ):
                    raise ValueError("manifest identity")
                digest = hashlib.sha256(
                    json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                self._files = files
                self._identity = EmbeddingIdentity(MODEL, REVISION, 384, PREPROCESSING, digest)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ProviderUnavailable("embedding-manifest-invalid") from exc
        return self._identity

    def _embed_native(self, request: EmbeddingRequest) -> EmbeddingResult:
        identity = self.identity
        assert self.manifest_path is not None
        if self._encoder is None:
            root = self.manifest_path.parent.resolve()
            total = 0
            for name, expected in self._files.items():
                path = root / name
                if path.is_symlink() or path.resolve().parent != root:
                    raise ProviderUnavailable("embedding-artifact-invalid")
                size = path.stat().st_size
                total += size
                if size > 512 * 1024 * 1024 or total > 550 * 1024 * 1024:
                    raise ProviderUnavailable("embedding-artifact-bound-exceeded")
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != expected:
                    raise ProviderUnavailable("embedding-artifact-hash-mismatch")
            self._encoder = self._runtime_factory(root, self.cpu_threads)
        prefix = "query: " if request.kind == "query" else "passage: "
        values = self._encoder(tuple(prefix + text for text in request.texts))
        result = EmbeddingResult(
            tuple(tuple(float(value) for value in row) for row in values),
            identity.dimension,
            identity,
        )
        return validate_embedding_result(result, count=len(request.texts), identity=identity)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        if (
            request.kind not in {"query", "document"}
            or not 1 <= len(request.texts) <= self.max_batch_size
            or any(
                not isinstance(text, str)
                or not text.strip()
                or len(text.encode("utf-8")) > self.max_text_bytes
                for text in request.texts
            )
        ):
            raise ValueError("embedding-input-invalid")
        if request.model is not None and request.model != MODEL:
            raise ValueError("embedding-model-mismatch")
        if self._task is not None and not self._task.done():
            raise ProviderUnavailable("embedding-native-busy")
        self._task = asyncio.create_task(asyncio.to_thread(self._embed_native, request))
        # Shield preserves native ownership after cancellation/timeout; admission
        # refuses another native call until the original worker actually exits.
        self._task.add_done_callback(
            lambda task: None if task.cancelled() else task.exception()
        )
        try:
            return await asyncio.wait_for(asyncio.shield(self._task), self.timeout_secs)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise ProviderUnavailable("embedding-timeout") from exc
        except (ValueError, OSError, ImportError, RuntimeError, TypeError, KeyError) as exc:
            raise ProviderUnavailable("embedding-runtime-unavailable") from exc
