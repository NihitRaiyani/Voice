"""One model/encoder batch for at most two full-coverage mixed-language chunks.

mlx-whisper 0.4.3's decoder internals are intentionally version-pinned. This
module never imports an optional runtime at module import time.
"""


import math
from dataclasses import dataclass

from roma.providers.ai.whisper_vocabulary import load_prompt, sample_length


@dataclass(frozen=True)
class BatchRejectionPolicy:
    no_speech: float = 0.6
    avg_logprob: float = -1.0
    compression_ratio: float = 2.4

    def __post_init__(self):
        if (any(isinstance(v, bool) or not math.isfinite(v) for v in
                (self.no_speech, self.avg_logprob, self.compression_ratio)) or
                not 0 <= self.no_speech <= 1 or self.avg_logprob > 0 or self.compression_ratio <= 0):
            raise ValueError("Invalid batch rejection thresholds")


def validated_batch_text(rows, policy=None):
    """Drop unreliable rows independently; no retry, fallback or raw diagnostics."""
    policy = policy or BatchRejectionPolicy()
    accepted = []
    for row in rows:
        values = (row.no_speech_prob, row.avg_logprob, row.compression_ratio)
        if (any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values)
                or not 0 <= row.no_speech_prob <= policy.no_speech
                or not policy.avg_logprob <= row.avg_logprob <= 0
                or not 0 <= row.compression_ratio <= policy.compression_ratio):
            continue
        if row.text.strip():
            accepted.append(row.text.strip())
    return " ".join(accepted)


def speech_chunks(pcm, scorer, policy=None):
    from roma.domain.audio.endpointing import EndpointConfig, EndpointDetector
    from roma.domain.audio.speech_chunks import split_at_strongest_pause
    from roma.domain.audio.turn_policy import TurnPolicy

    policy = policy or TurnPolicy()
    scorer.reset()
    endpoint = EndpointDetector(EndpointConfig(silence_ms=policy.batch_detector_silence_ms))
    spans = []
    try:
        for start in range(0, len(pcm), 1024):
            frame = pcm[start : start + 1024].ljust(1024, b"\0")
            spans.extend(endpoint.feed(frame, scorer.probability(frame)))
        spans.extend(endpoint.flush())
        return split_at_strongest_pause(pcm, spans, policy)
    finally:
        scorer.reset()


def decode_batch(folder, parts, base, policy=None, *, booking_vocabulary=False):
    if not 1 <= len(parts) <= 2:
        raise ValueError("Whisper batch requires one or two chunks")
    # Validate before accessing the process-global native model holder.
    prompt = load_prompt(booking_vocabulary)
    import mlx.core as mx
    import numpy as np
    from mlx_whisper.audio import N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.decoding import DecodingOptions, DecodingTask
    from mlx_whisper.transcribe import ModelHolder

    class ContextTask(DecodingTask):
        def _main_loop(self, features, tokens):
            return compact_greedy_loop(self, features, tokens)

        def _detect_language(self, features, tokens):
            _, probabilities = self.model.detect_language(features, self.tokenizer)
            # Detection scores are evidence for this chunk, not calibrated ASR
            # confidence or permission to change the persisted conversation state.
            languages = [choose_chunk_language(p, base) for p in probabilities]
            tokens[:, self.sot_index + 1] = mx.array(
                [
                    self.tokenizer.encoding.encode_single_token("<|" + lang + "|>")
                    for lang in languages
                ]
            )
            return languages, probabilities

    model = ModelHolder.get_model(folder, mx.float16)
    batch = mx.stack(
        [
            pad_or_trim(
                log_mel_spectrogram(
                    np.frombuffer(part, dtype="<i2").astype(np.float32) / 32768.0,
                    n_mels=128, padding=N_SAMPLES,
                ),
                3000,
                axis=-2,
            )
            for part in parts
        ]
    )
    task = ContextTask(
        model,
        DecodingOptions(
            language=None,
            task="transcribe",
            temperature=0,
            without_timestamps=True,
            sample_len=sample_length(booking_vocabulary),
            **({"prompt": prompt} if prompt is not None else {}),
        ),
    )
    results = task.run(batch)
    return validated_batch_text(results, policy)


def compact_greedy_loop(task, audio_features, tokens):
    """Greedy parity with pinned SDK; stop computing a row as soon as it emits EOT.

    The SDK's outer task still owns filtering, finalization and decoding. Only
    active rows and their complete self/cross-attention KV caches are compacted.
    No second encoding, beam search, retry or model is created.
    """
    import mlx.core as mx
    from mlx.utils import tree_map

    count = tokens.shape[0]
    active_ids = list(range(count))
    sums = mx.zeros(count)
    finished_tokens = [None] * count
    finished_scores = [None] * count
    no_speech = None
    features = audio_features
    for step in range(task.sample_len):
        inputs = tokens if step == 0 else tokens[:, -1:]
        if tokens.shape[-1] > task.n_ctx:
            break
        pre_logits = task.inference.logits(inputs, features)
        if step == 0:
            if task.tokenizer.no_speech is None:
                no_speech = mx.full(count, mx.nan)
            else:
                no_speech = mx.softmax(pre_logits[:, task.sot_index], axis=-1)[
                    :, task.tokenizer.no_speech
                ]
        logits = pre_logits[:, -1]
        for logit_filter in task.logit_filters:
            logits = logit_filter.apply(logits, tokens)
        tokens, _, sums = task.decoder.update(tokens, logits, sums)
        done = (tokens[:, -1] == task.tokenizer.eot).tolist()
        if not any(done):
            continue
        for i, complete in enumerate(done):
            if complete:
                original = active_ids[i]
                finished_tokens[original] = tokens[i].tolist()
                finished_scores[original] = sums[i].item()
        keep = [i for i, complete in enumerate(done) if not complete]
        if not keep:
            active_ids = []
            break
        indices = mx.array(keep)
        # SDK rearrange_kv_cache skips [0] as identity even when the original
        # batch had two rows. Explicit slicing also handles dropping its tail.
        task.inference.kv_cache = tree_map(
            lambda value, selected=indices: value[selected], task.inference.kv_cache
        )
        tokens, sums, features = tokens[indices], sums[indices], features[indices]
        active_ids = [active_ids[i] for i in keep]
    for i, original in enumerate(active_ids):
        finished_tokens[original] = tokens[i].tolist()
        finished_scores[original] = sums[i].item()
    width = max(len(row) for row in finished_tokens)
    padded = [row + [task.tokenizer.eot] * (width - len(row)) for row in finished_tokens]
    return mx.array(padded), mx.array(finished_scores), no_speech


def choose_chunk_language(probabilities: dict[str, float], base: str | None) -> str:
    """Keep the caller's known script; admit only strong English evidence."""
    import math

    if base is None:
        base = max(("gu", "hi", "en"), key=lambda lang: probabilities.get(lang, 0))
    if base not in {"gu", "hi", "en"}:
        raise ValueError("Unsupported local speech context")
    english, context = probabilities["en"], probabilities[base]
    if not all(math.isfinite(p) and 0 <= p <= 1 for p in (english, context)):
        raise ValueError("Invalid local language evidence")
    return "en" if english >= 0.8 and english > context else base


def turn_chunks(pcm, scorer, language, policy):
    """Choose one full-coverage batch or the original, byte-identical input."""
    if len(pcm) < policy.batch_min_turn_ms * 32:
        return (pcm,)
    return speech_chunks(pcm, scorer, policy)
