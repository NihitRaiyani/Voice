# Canned audio assets (Step 1)

8kHz mono μ-law raw clips spoken on connect before TTS exists (Bulbul lands in Step 3).

- `consent.ulaw` — the recording-consent disclosure (`CONSENT_LINE`).
- `opening.ulaw` — **the inbound opener** (`OPENING_LINE`), played the instant the socket
  connects. Real speech in Roma's voice, not a placeholder. Re-render after any wording
  change: `uv run python scripts/make_opening_clip.py --force`.
- `filler_*.ulaw` — the turn-latency fillers (`scripts/make_filler_clips.py`), neutral pool
  plus the intent-matched `dekhiye` / `samajh_rahi_hoon` clips.
- `phrases/` — the fixed-phrase TTS cache (`scripts/warm_phrase_cache.py`): every constant
  line Roma speaks, plus a `manifest.json` recording the exact text each clip speaks. Edit a
  wording anywhere and its clip goes stale (refused at load, degrades to live TTS) until the
  script re-renders it.

The opener is cached rather than generated for the same reason the fillers are: the caller
dialled Weltec and is waiting to hear it rang through, and the cold first LLM turn costs
3.28 s (CAfe5a00b). Keep it short — it is audio the caller has to barge in over.

**`consent.ulaw` is a PLACEHOLDER tone clip**, not speech. Regenerate from
approved recordings:

```
uv run python scripts/make_canned_clip.py from-wav approved_consent_8k.wav \
    roma/realtime/assets/consent.ulaw
```

The recording must be 8kHz / mono / 16-bit PCM WAV (re-encode with
`ffmpeg -i in.wav -ar 8000 -ac 1 -sample_fmt s16 out_8k.wav`).

⚠️ Consent wording needs Weltec compliance sign-off; never point a live call at a real
lead with placeholder consent audio (docs/decisions.md → Open). Test numbers only.
