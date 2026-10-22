import pytest
from roma.domain.audio.endpointing import Utterance
from roma.domain.audio.speech_chunks import split_at_strongest_pause


def span(start, end):
    return Utterance(b"", start, end, 1, "silence")


def test_chunk_split_keeps_every_sample_exactly_once_in_original_order():
    pcm = bytes(range(256)) * 2500
    parts = split_at_strongest_pause(pcm, [span(1000, 100000), span(140000, 250000)])
    assert len(parts) == 2 and b"".join(parts) == pcm
    assert len(parts[0]) == 240000


def test_only_strongest_internal_pause_splits_and_short_edges_are_preserved():
    pcm = b"\0\0" * 320000
    parts = split_at_strongest_pause(
        pcm, [span(10, 40000), span(45000, 100000), span(140000, 250000)]
    )
    assert len(parts) == 2 and len(parts[0]) == 240000
    assert split_at_strongest_pause(pcm, [span(10, 40000), span(41000, 50000)]) == (pcm,)
    assert split_at_strongest_pause(pcm, []) == (pcm,)


@pytest.mark.parametrize("pcm", [b"x", "not bytes"])
def test_invalid_audio_is_rejected(pcm):
    with pytest.raises(ValueError):
        split_at_strongest_pause(pcm, [])
