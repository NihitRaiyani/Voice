# Fixed speech audio assets

Baseline clips are 8 kHz mono μ-law and support approved cached speech alongside the implemented cloud TTS path. L5 introduces local synthesis and text/language/voice/model identity; documentation redesign does not regenerate assets.

| Asset | Role |
|---|---|
| `consent.ulaw` | Disclosure audio; currently a placeholder requiring owner-approved replacement |
| `opening.ulaw` | Cached inbound opener matching the fixed opening line |
| `filler_*.ulaw` | Neutral/intent-matched fillers |
| `phrases/manifest.json` and clips | Exact-text approved fixed speech cache |

Change fixed wording and matching audio together. Stale content identity is refused; dynamic readbacks must not reuse another caller's audio. Rendering may spend provider credits and belongs to an authorized asset task. Avoid forced bulk regeneration unless approved text/voice changed.

Convert approved disclosure speech from 8 kHz mono 16-bit PCM WAV:

```bash
uv run python scripts/make_canned_clip.py from-wav approved_consent_8k.wav \
    roma/realtime/assets/consent.ulaw
```

Opener/filler/phrase tools are `scripts/make_opening_clip.py`, `scripts/make_filler_clips.py` and `scripts/warm_phrase_cache.py`. Verify manifest/text/audio identity after rendering. Pending disclosure cannot authorize retained recordings. Live replies remain Hindi-base Hinglish; multilingual lab clips belong to a separate profile.

See [safety](../../../docs/04-guardrails.md), [recordings](../../../docs/09-recording-storage.md) and [decisions](../../../docs/decisions.md).
