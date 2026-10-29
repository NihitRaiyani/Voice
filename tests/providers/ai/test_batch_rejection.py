"""Decoder metadata is synthetic; these tests never load Whisper."""
from types import SimpleNamespace

import pytest


def row(text='clean reply', **values):
    return SimpleNamespace(text=text, no_speech_prob=values.get('no_speech_prob', 0.1),
                           avg_logprob=values.get('avg_logprob', -0.2),
                           compression_ratio=values.get('compression_ratio', 1.0))


@pytest.mark.parametrize('rows,expected', [
    ([row()], 'clean reply'),
    ([row('silence hallucination', no_speech_prob=0.9)], ''),
    ([row('low probability', avg_logprob=-2)], ''),
    ([row('loop loop loop', compression_ratio=3)], ''),
    # A missing chunk may contain negation or a date; never salvage a partial turn.
    ([row('keep'), row('drop', no_speech_prob=0.9)], ''),
    ([row(''), row('keep')], ''),
    ([row(no_speech_prob=0.9), row(compression_ratio=3)], ''),
    ([row(no_speech_prob=float('nan'))], ''),
])
def test_batch_rows_are_validated_before_text_is_joined(rows, expected):
    from roma.providers.ai.mlx_batch import validated_batch_text
    assert validated_batch_text(rows) == expected


def test_rejection_policy_is_configurable_and_validated():
    from roma.providers.ai.mlx_batch import BatchRejectionPolicy, validated_batch_text
    assert validated_batch_text([row(no_speech_prob=0.7)], BatchRejectionPolicy(no_speech=0.8))
    with pytest.raises(ValueError):
        BatchRejectionPolicy(no_speech=2)
