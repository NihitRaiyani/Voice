import asyncio
import hashlib
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from roma.core.ai_contracts import ProviderUnavailable
from roma.providers.ai.contracts import EmbeddingRequest
from roma.providers.ai.mocks import MockEmbeddingProvider


def model_manifest(tmp_path):
    files = {}
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
        (tmp_path / name).write_bytes(b"fixture")
        files[name] = hashlib.sha256(b"fixture").hexdigest()
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "roma-embedding-manifest-v1",
                "model": "intfloat/multilingual-e5-small",
                "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
                "dimension": 384,
                "preprocessing": "e5-prefix-mean-l2-v1",
                "files": files,
            }
        )
    )
    return path


def test_mock_query_and_document_share_index_identity():
    async def run():
        provider = MockEmbeddingProvider()
        query = await provider.embed(EmbeddingRequest(("ગુજરાતી",), kind="query"))
        document = await provider.embed(EmbeddingRequest(("ગુજરાતી",), kind="document"))
        assert query.identity == document.identity == provider.identity
        assert query.identity.model == "mock-sha256"
        assert query.dimension == query.identity.dimension == 8
        assert query.vectors == document.vectors

    asyncio.run(run())


def test_native_timeout_keeps_admission_until_actual_worker_finishes(tmp_path):
    from roma.providers.ai.embeddings import LocalMultilingualEmbedding

    started, finish = threading.Event(), threading.Event()
    count = 0

    def runtime(root, threads):
        def encode(texts):
            nonlocal count
            count += 1
            started.set()
            finish.wait(2)
            return [[1.0] + [0.0] * 383]

        return encode

    async def run():
        provider = LocalMultilingualEmbedding(
            manifest_path=model_manifest(tmp_path), runtime_factory=runtime, timeout_secs=0.02
        )
        try:
            with pytest.raises(ProviderUnavailable, match="timeout"):
                await provider.embed(EmbeddingRequest(("hello",)))
            assert started.is_set()
            with pytest.raises(ProviderUnavailable, match="busy"):
                await provider.embed(EmbeddingRequest(("hello",)))
            assert count == 1
        finally:
            finish.set()

    asyncio.run(run())


def test_cache_tampering_and_oversized_input_never_reach_runtime(tmp_path):
    from roma.providers.ai.embeddings import LocalMultilingualEmbedding

    path = model_manifest(tmp_path)
    (tmp_path / "model.safetensors").write_bytes(b"tampered")

    def forbidden(root, threads):
        pytest.fail("invalid artifact reached model loading")

    async def run():
        provider = LocalMultilingualEmbedding(manifest_path=path, runtime_factory=forbidden)
        with pytest.raises(ValueError, match="input"):
            await provider.embed(EmbeddingRequest(("x" * 8193,)))
        with pytest.raises(ProviderUnavailable):
            await provider.embed(EmbeddingRequest(("valid",)))

    asyncio.run(run())


def test_embedding_resource_bounds_rejected_at_construction():
    from roma.providers.ai.embeddings import LocalMultilingualEmbedding

    for settings in (
        {"cpu_threads": 0},
        {"max_batch_size": 1000},
        {"timeout_secs": float("nan")},
    ):
        with pytest.raises(ValueError):
            LocalMultilingualEmbedding(**settings)


@pytest.mark.parametrize("row", [[float("nan")] * 384, [1.0] * 383, [0.0] * 384, [1.0] * 384])
def test_native_malformed_vectors_are_never_returned(tmp_path, row):
    from roma.providers.ai.embeddings import LocalMultilingualEmbedding

    async def run():
        provider = LocalMultilingualEmbedding(
            manifest_path=model_manifest(tmp_path),
            runtime_factory=lambda root, threads: lambda texts: [row],
        )
        with pytest.raises(ProviderUnavailable):
            await provider.embed(EmbeddingRequest(("hello",)))

    asyncio.run(run())


def test_native_cancellation_preserves_worker_admission(tmp_path):
    from roma.providers.ai.embeddings import LocalMultilingualEmbedding

    started, finish = threading.Event(), threading.Event()

    def runtime(root, threads):
        def encode(texts):
            started.set()
            finish.wait(2)
            return [[1.0] + [0.0] * 383]

        return encode

    async def run():
        provider = LocalMultilingualEmbedding(
            manifest_path=model_manifest(tmp_path), runtime_factory=runtime
        )
        operation = asyncio.create_task(provider.embed(EmbeddingRequest(("hello",))))
        try:
            await asyncio.to_thread(started.wait, 1)
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
            with pytest.raises(ProviderUnavailable, match="busy"):
                await provider.embed(EmbeddingRequest(("hello",)))
        finally:
            finish.set()

    asyncio.run(run())


def test_real_runtime_rejects_overlength_tokens_before_model_forward(tmp_path, monkeypatch):
    from roma.providers.ai.embeddings import LocalMultilingualEmbedding

    def tokenizer_load(root, **options):
        assert options == {"local_files_only": True, "trust_remote_code": False}

        def tokenize(texts, **settings):
            assert settings["truncation"] is False
            return {"input_ids": SimpleNamespace(shape=(1, 513))}

        return tokenize

    class Model:
        def to(self, device):
            assert device == "cpu"
            return self

        def eval(self):
            return self

        def __call__(self, **kwargs):
            pytest.fail("oversized tokens reached model inference")

    def model_load(root, **options):
        assert options == {
            "local_files_only": True,
            "trust_remote_code": False,
            "use_safetensors": True,
        }
        return Model()

    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(set_num_threads=lambda value: None)
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=tokenizer_load),
            AutoModel=SimpleNamespace(from_pretrained=model_load),
        ),
    )

    async def run():
        provider = LocalMultilingualEmbedding(manifest_path=model_manifest(tmp_path))
        with pytest.raises(ProviderUnavailable):
            await provider.embed(EmbeddingRequest(("valid sized text",)))

    asyncio.run(run())


def test_local_cache_embedding_applies_kind_and_checks_output_identity(tmp_path):
    from roma.providers.ai.embeddings import LocalMultilingualEmbedding

    received = []

    def runtime(root, threads):
        assert threads == 2

        def encode(texts):
            received.extend(texts)
            return [[1.0] + [0.0] * 383 for _ in texts]

        return encode

    async def run():
        provider = LocalMultilingualEmbedding(
            manifest_path=model_manifest(tmp_path), runtime_factory=runtime
        )
        result = await provider.embed(EmbeddingRequest(("ગુજરાતી",), kind="query"))
        await provider.embed(EmbeddingRequest(("हिंदी",), kind="document"))
        assert received == ["query: ગુજરાતી", "passage: हिंदी"]
        assert result.identity == provider.identity
        assert result.dimension == 384
        assert len(result.vectors[0]) == 384

    asyncio.run(run())
