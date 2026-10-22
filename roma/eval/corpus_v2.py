"""Consent-aware corpus metadata only. Never reads, records or fetches audio."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from roma.domain.conversation.language import Language
from roma.eval.hosted_asr import normalized

ENTITY_TYPES = frozenset({'name', 'date', 'time', 'branch'})


def contains_phrase(text: str, phrase: str) -> bool:
    """Normalized complete tokens: Amit never matches Amitabh."""
    return bool(phrase) and f' {phrase} ' in f' {text} '


class Metadata(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CorpusMinimums(Metadata):
    min_distinct_speakers: int | None = Field(default=None, gt=0, strict=True)
    min_distinct_recordings: int | None = Field(default=None, gt=0, strict=True)


class Consent(Metadata):
    granted: StrictBool
    reference: str = Field(min_length=1, max_length=256)
    permitted_use: Literal['asr-evaluation']
    storage_policy: str = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def require_consent(self):
        if not self.granted or not self.reference.strip() or not self.storage_policy.strip():
            raise ValueError('Explicit evaluation consent and storage policy are required')
        return self


class EntityReference(Metadata):
    type: Literal['name', 'date', 'time', 'branch']
    reference: str = Field(min_length=1, max_length=256)


class Recording(Metadata):
    recording_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    speaker_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    language_state: Language
    natural_switch: StrictBool
    switch_positions_ms: list[float]
    duration_ms: float = Field(gt=0, allow_inf_nan=False)
    channel: Literal['clean', 'phone']
    audio_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reference: str = Field(min_length=1, max_length=4000)
    entities: list[EntityReference]
    license: str = Field(min_length=1, max_length=256)
    consent: Consent

    @field_validator('switch_positions_ms', mode='before')
    @classmethod
    def positions_are_numbers(cls, values):
        if not isinstance(values, list) or any(type(v) not in (int, float) for v in values):
            raise ValueError('Switch positions must be explicit numeric milliseconds')
        return values

    @model_validator(mode='after')
    def references_and_switches(self):
        text = normalized(self.reference)
        if not text or not self.license.strip():
            raise ValueError('Reference and license must be nonempty')
        if (bool(self.switch_positions_ms) != self.natural_switch
                or any(not 0 <= value < self.duration_ms for value in self.switch_positions_ms)
                or sorted(set(self.switch_positions_ms)) != self.switch_positions_ms):
            raise ValueError('Natural switch positions must be ordered, unique and within audio')
        seen = set()
        for entity in self.entities:
            key = (entity.type, entity.reference)
            if (entity.reference not in self.reference or
                    not contains_phrase(text, normalized(entity.reference)) or key in seen):
                raise ValueError('Entity references must match exact reference text without duplicates')
            seen.add(key)
        return self


class CorpusManifest(Metadata):
    schema_version: Literal['roma-asr-manifest-v2'] = Field(alias='schema')
    recordings: list[Recording] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_recordings_and_entities(self):
        ids = [row.recording_id for row in self.recordings]
        hashes = [row.audio_sha256 for row in self.recordings]
        if len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes):
            raise ValueError('Duplicate recordings or audio identity')
        annotated = {entity.type for row in self.recordings for entity in row.entities}
        if annotated != ENTITY_TYPES:
            raise ValueError('Corpus must provide exact references for all four entity types')
        return self


def validate_corpus(value: dict, minimums: CorpusMinimums) -> CorpusManifest:
    if minimums.min_distinct_speakers is None or minimums.min_distinct_recordings is None:
        raise ValueError('Owner must configure minimum distinct speakers and recordings')
    manifest = CorpusManifest.model_validate(value)
    if len({row.speaker_id for row in manifest.recordings}) < minimums.min_distinct_speakers:
        raise ValueError('Corpus is below the configured minimum distinct speakers')
    if len(manifest.recordings) < minimums.min_distinct_recordings:
        raise ValueError('Corpus is below the configured minimum distinct recordings')
    return manifest


def entity_recall(entities: list[EntityReference], hypothesis: str) -> dict[str, dict]:
    """Exact normalized phrase recall, not inferred slot/value equivalence."""
    text = normalized(hypothesis)
    counts = {kind: {'expected': 0, 'matched': 0} for kind in sorted(ENTITY_TYPES)}
    for entity in entities:
        counts[entity.type]['expected'] += 1
        counts[entity.type]['matched'] += int(contains_phrase(text, normalized(entity.reference)))
    return {
        kind: {**values, 'recall': values['matched'] / values['expected'] if values['expected'] else None}
        for kind, values in counts.items()
    }


def score_corpus(manifest: CorpusManifest, hypotheses: dict[str, str]) -> dict:
    """Pure scoring of supplied text; every recording must have one hypothesis."""
    from roma.eval.hosted_asr import score

    expected = {row.recording_id for row in manifest.recordings}
    if set(hypotheses) != expected or any(not isinstance(text, str) for text in hypotheses.values()):
        raise ValueError('Supply exactly one text hypothesis for each recording')
    speakers: dict[str, dict] = {}
    entities = {kind: {'expected': 0, 'matched': 0} for kind in sorted(ENTITY_TYPES)}
    for row in manifest.recordings:
        hypothesis = hypotheses[row.recording_id]
        metrics = score(row.reference, hypothesis)
        speaker = speakers.setdefault(row.speaker_id, {
            'speaker_id': row.speaker_id, 'recordings': 0,
            'reference_words': 0, 'word_edits': 0, 'reference_chars': 0, 'char_edits': 0,
        })
        speaker['recordings'] += 1
        for key in ('reference_words', 'word_edits', 'reference_chars', 'char_edits'):
            speaker[key] += metrics[key]
        for kind, values in entity_recall(row.entities, hypothesis).items():
            entities[kind]['expected'] += values['expected']
            entities[kind]['matched'] += values['matched']
    per_speaker = []
    for _, speaker in sorted(speakers.items()):
        per_speaker.append({
            **speaker,
            'wer': speaker['word_edits'] / speaker['reference_words'],
            'cer': speaker['char_edits'] / speaker['reference_chars'],
        })
    return {'per_speaker': per_speaker, 'per_entity': {
        kind: {**values, 'recall': values['matched'] / values['expected'] if values['expected'] else None}
        for kind, values in entities.items()
    }}
