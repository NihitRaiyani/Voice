# Representative ASR recording protocol

Planning only. No audio is recorded or fetched by this phase. The existing section18 corpus remains unchanged: its 15 derived cases from three source recordings cannot establish a model decision. Historical WER results stay historical.

## Coverage

Set the owner-reviewed `min_distinct_speakers` and `min_distinct_recordings` in [the v2 configuration](../configs/asr-corpus-v2.json) before collection. Both are unset; this document invents no sample minimum.

Use scripted booking dialogues covering caller **name**, appointment **date**, **time** and **branch**, including corrections and confirmations. Include Gujarati, Hindi, English, Gujarati–English and Hindi–English, with different speakers and speaking styles. Include natural language switches mid-sentence **without a pause**, as well as switches after a pause; do not substitute stitched speech for natural code-mix. Obtain clean recordings and genuine phone-channel captures. Synthetic noise/codec variants may supplement them, but cannot establish representative channel performance.

## Identity and annotation

Assign one opaque `speaker_id` per participant and one `recording_id` per independent source take; keep identity mappings private. Derived variants/slices cannot increase independent recording or speaker counts. The metadata schema lists source recordings; later audio evaluation must preserve those source identities when segmenting for the existing audio-duration cap.

For each source, annotate its project language state, `natural_switch`, ordered `switch_positions_ms`, duration, clean/phone channel and audio checksum. Transcribe exactly, then tag each entity with its exact reference text. Cover all four entity types across the corpus. Validate against [schema v2](../configs/asr-corpus-v2.schema.json); duplicate IDs/audio, missing consent/licence and incomplete entity references are rejected. Validation checks metadata consistency, not actual audio or authenticity of permissions.

## Consent and storage

Obtain explicit documented permission for recording and ASR evaluation before capture; attach the licence/permission statement and consent reference. Decide permitted use, withdrawal/deletion procedure, retention period and authorized reviewers first. No implied live-call consent or unrelated private recordings.

Store consent/identity mappings separately with restricted access. Keep recordings, transcripts and evaluation manifests in approved private storage, such as ignored `var/roma/asr-corpus-v2`, with the agreed retention policy. Commit only synthetic fixtures/schema/tooling; never real names, phone numbers, transcripts, consent forms or audio. Native reviewers must have authorized access.

## Before collecting

The owner must approve speaker/recording minimums, language/channel coverage, independent-source granularity, entity annotation rules, licence/consent terms, storage/retention and named reviewers. Separately agree quality, entity recall and latency acceptance criteria. This protocol grants no collection permission and passes no gate. [Level 4 readiness](levels/level-04.md) and [verification](12-verification.md) remain the evidence owners.
