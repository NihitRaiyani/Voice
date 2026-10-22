"""At most two contiguous chunks; VAD locates a pause but never trims PCM."""

from collections.abc import Sequence
from itertools import pairwise

from roma.domain.audio.endpointing import Utterance
from roma.domain.audio.pcm import validate_pcm16


def split_at_strongest_pause(pcm: bytes, spans: Sequence[Utterance]) -> tuple[bytes, ...]:
    validate_pcm16(pcm)
    samples = len(pcm) // 2
    candidates = []
    for left, right in pairwise(spans):
        gap = right.start_sample - left.end_sample
        cut = (left.end_sample + right.start_sample) // 2
        # At least 256 ms of measured gap; retain one second on either side.
        if gap >= 4096 and 16000 <= cut <= samples - 16000:
            candidates.append((gap, cut))
    if not candidates:
        return (pcm,)
    cut = max(candidates)[1] * 2
    return pcm[:cut], pcm[cut:]
