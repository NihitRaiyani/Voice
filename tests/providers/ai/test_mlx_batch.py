from types import SimpleNamespace

import pytest
from roma.providers.ai.mlx_batch import compact_greedy_loop


@pytest.mark.parametrize("limits", [(2, 5), (5, 2)])
def test_completed_chunk_stops_consuming_decoder_work_and_order_is_preserved(limits):
    mx = pytest.importorskip("mlx.core")
    from mlx_whisper.decoding import GreedyDecoder

    batches = []

    class Inference:
        kv_cache = None

        def logits(self, inputs, features):
            ids = [int(x) for x in features[:, 0].tolist()]
            batches.append(ids)
            if self.kv_cache is None:
                steps = [1] * len(ids)
            else:
                cached = self.kv_cache[0].tolist()
                assert [int(x[0]) for x in cached] == ids
                steps = [int(x[1]) + 1 for x in cached]
            self.kv_cache = (mx.array(list(zip(ids, steps, strict=True))),)
            logits = mx.full((len(ids), inputs.shape[1], 10), -100.0)
            for i, (identity, step) in enumerate(zip(ids, steps, strict=True)):
                logits[i, :, 0 if step >= limits[identity] else identity + 1] = 0.0
            return logits

    task = SimpleNamespace(
        inference=Inference(),
        tokenizer=SimpleNamespace(eot=0, no_speech=9),
        decoder=GreedyDecoder(0, 0),
        logit_filters=[],
        sample_len=10,
        n_ctx=32,
        sot_index=0,
    )
    tokens, scores, no_speech = compact_greedy_loop(
        task, mx.array([[0], [1]]), mx.array([[5, 6, 7, 8]] * 2)
    )
    assert tokens.tolist()[0][: 4 + limits[0]] == [5, 6, 7, 8] + [1] * (limits[0] - 1) + [0]
    assert tokens.tolist()[1][: 4 + limits[1]] == [5, 6, 7, 8] + [2] * (limits[1] - 1) + [0]
    assert [len(b) for b in batches] == [2, 2, 1, 1, 1]
    assert scores.shape == no_speech.shape == (2,)


@pytest.mark.parametrize(
    "base,probabilities,expected",
    [
        ("gu", {"gu": 0.2, "hi": 0.8, "en": 0.001}, "gu"),
        ("hi", {"hi": 0.1, "ur": 0.88, "en": 0.003}, "hi"),
        ("gu", {"gu": 0.001, "en": 0.99}, "en"),
        ("hi", {"hi": 0.1, "en": 0.79}, "hi"),
    ],
)
def test_chunk_language_keeps_indic_script_until_english_is_confident(
    base, probabilities, expected
):
    from roma.providers.ai.mlx_batch import choose_chunk_language

    assert choose_chunk_language(probabilities, base) == expected


def test_batch_bound_rejects_invalid_inputs_before_loading_optional_runtime():
    from roma.providers.ai.mlx_batch import decode_batch

    with pytest.raises(ValueError):
        decode_batch("not-loaded", [b"x"] * 3, "gu")


def test_speech_chunking_resets_scorer_and_never_discards_pcm():
    from roma.providers.ai.mlx_batch import speech_chunks

    class Scorer:
        resets = 0

        def reset(self):
            self.resets += 1
            self.index = 0

        def probability(self, frame):
            self.index += 1
            return 1.0 if self.index <= 50 or self.index > 90 else 0.0

    scorer = Scorer()
    pcm = b"\1\0" * 512 * 150
    parts = speech_chunks(pcm, scorer)
    assert len(parts) == 2 and b"".join(parts) == pcm
    assert speech_chunks(pcm, scorer) == parts and scorer.resets == 4
