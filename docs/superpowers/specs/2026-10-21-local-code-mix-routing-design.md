# Local code-mix ASR routing design

## Goal

Recover English suffixes in Gujarati-English and Hindi-English turns with the already-pinned local IndicConformer and Whisper base models. Paid Claude, microphone input, production telephony, model weights and database state remain outside this increment.

## Decision

Keep explicit monolingual routing unchanged: Gujarati and Hindi use IndicConformer; English uses Whisper base. For an explicit `gu-en` or `hi-en` request, find one strong internal speech gap from bounded 16 kHz PCM before ASR. Decode the head with the base Indic language and let Whisper's language identification inspect candidate English spans. Accept a Whisper result only when it identifies English and the text contains credible English evidence; otherwise decode a tail with IndicConformer or leave an unusable head empty. Apply the same gate if a no-gap Indic result has too little base-script evidence. Provider failures remain failures and never trigger model switching.

This is preferable to a CTC blank-tail trigger because the reviewed adapter exposes neither trustworthy token timestamps nor calibrated blank confidence. It is also preferable to frame-level acoustic language identification because no reviewed classifier or measured natural code-mix corpus exists in the project. The detector identifies a phrase boundary, not a language by waveform alone; the two model outputs provide bounded arbitration.

## Components and data flow

`LocalLanguageSTT` remains the public composite. It accepts `gu`, `hi`, `en`, `gu-en` and `hi-en`. Mixed requests use a pure PCM energy helper that selects the longest sufficiently quiet internal run while requiring useful audio on both sides. If no strong boundary exists, the entire turn stays on IndicConformer’s fast path.

The head is sent to IndicConformer with `gu` or `hi`. The tail is sent to Whisper without forcing a language, because energy detects a boundary rather than its language. Whisper must report `en` and produce either two English words, one reviewed domain term or one reviewed short acknowledgement before its result is accepted. Otherwise the tail is sent to IndicConformer. Two or more letters in the expected Unicode script make an Indic result usable. Text is joined in time order, normalized through the existing transcript normalizer, and returned with the explicit mixed-language label.

A deterministic exact-token loanword map canonicalizes a small reviewed booking vocabulary such as phonetic Indic spellings of `appointment`, `booking`, `confirm`, `confirmation`, `cancel` and `tomorrow`. It handles adjacent Indic punctuation but does not fuzzy-match names, compound words or arbitrary caller text.

## Failure and safety behavior

- Malformed audio still fails in the concrete provider validation.
- A provider exception propagates; it is not treated as low confidence.
- Empty or one-letter Indic output can select Whisper only on the explicit mixed route.
- Plausible English text is rejected when Whisper identifies the tail as another language.
- Bounds cap the detector to the existing 30-second request and one internal split.
- No network, download, paid model, microphone, schema or production carrier change occurs.

## Evidence

Unit tests cover direct routing, boundary detection, fallback, provider failure, short Indic acknowledgements and exact loanword normalization. The immutable 15-case Section 18 corpus is rerun through the adaptive provider. Reports must separate monolingual and stitched synthetic code-mix quality, wall latency and limitations. Synthetic stitched speech cannot approve native intra-sentential code-mix or the Level 4/6 gates.

