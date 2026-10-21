# Operating guide for Voice_Agent

Commands describe the existing comparison backend and the Level 1 native foundation path. Local-model/container-deployment commands will be added only when implemented. No live calls or provider spending are part of documentation maintenance.

## Environment and local services

Use the locked project environment with telephony/dev/workers extras, or let the Makefile run the same locked tooling:

```bash
make sync
```

Use `.env.example` as the settings reference; do not overwrite an existing `.env`. Configure local PostgreSQL and Redis endpoints, Twilio/Sarvam/Anthropic settings as needed by the selected baseline, `API_TOKEN`, public HTTPS base and keyed caller identity. Do not print or commit their values.

For the v4 foundation use native PostgreSQL/Redis services, each bound to the intended local/private interface with persistent data outside source. `DATABASE_URL` uses `postgresql+asyncpg://`; `REDIS_URL` matches the configured private Redis service. Use the installed service's administrator tools to create a dedicated development database/user; credentials are local secrets.

Existing `compose.yaml` starts PostgreSQL/Redis as pre-v4 compatibility tooling. It is optional for restoring that baseline, not the Level 1 learning requirement or a completed Level 13 deployment.

For the Level 1 native path, install PostgreSQL and Redis with the teacher-approved system package manager first, then use the Makefile. It creates project-local service state under ignored `var/dev-services/` and uses only local development credentials:

```bash
make services-up
make db-upgrade
make app-start
```

`make app-start` is the one-command development startup: it starts native PostgreSQL/Redis if needed, applies Alembic migrations and runs `roma.main:create_app` on `127.0.0.1:8020`. Override `HOST`/`PORT` for a local smoke check. Stop local services with `make services-down`. The older `scripts/start_roma.sh` remains the live-call/tunnel helper and still requires real carrier settings.

Apply existing migrations to the intended development database before answer-webhook testing. Database commands need only `DATABASE_URL` from the environment or `.env`, without voice-provider/Redis credentials. Follow the [migration guide](21-database-migrations.md) for forward changes, SQL review and revision-specific rollback:

```bash
make db-upgrade
```

Latest head is `20261004_0003`; it adds four checkpoint/model/benchmark tables and nullable turn-language metadata. Applied migrations `20260920_0001` and `20260930_0002` remain unchanged. Seed/demo data now has a [separate opt-in command](21-database-migrations.md#seeds-stay-outside-schema-history): `APP_ENV=dev python scripts/seed_demo.py --demo`. It requires head, preserves existing data, inserts only inactive synthetic offerings plus role vocabulary, and creates no accounts. Keep schema upgrades seed-free. Do not downgrade a working database merely to demonstrate rollback; tests use disposable databases.

The new migration is verified on empty and legacy-populated disposable databases, and the configured `roma` database was upgraded to `20261004_0003 (head)` on 2026-10-04. Run `alembic upgrade head` in every other intended environment before starting code that queries turn-language metadata, and use `alembic current`/`alembic check` to verify its state. Existing rows retain NULL language; schema migrations seed no historical guesses or sample records; the separate demo command is explicit.

A rollback to `20260930_0002` drops the four new tables and the new language column, destroying only data introduced through this increment. Export/backup any new evidence/checkpoints and coordinate compatible code before a real rollback. Tests exercise downgrade/re-upgrade only on disposable data; existing calls/turns survive both directions. [Schema rationale and retention](20-relational-schema.md) define what future producers may store. Knowledge tables/extensions remain Level 9.

## Database capacity and pool pressure

Keep the engine defaults unless measurement warrants tuning. These new non-secret settings describe the entire deployment sharing this database:

```dotenv
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT_SECS=5.0
DATABASE_POOL_PROCESSES=4
DATABASE_CONNECTION_BUDGET=60
```

This example counts one API process, one recording worker, one dispatcher and one Dramatiq process: retained capacity 20, peak 60. Four Dramatiq threads share its process pool. Scale replicas/processes by updating the count on every process; configuration cannot discover or enforce a cluster-wide semaphore. Mixed pool configurations require summing each group's peak and allocating independently. Positive pool size/finite timeout and nonnegative overflow are enforced; budget mismatch fails settings validation before engine use. An omitted budget retains compatibility but is not production sizing approval.

Check the actual server before starting/scaling:

```bash
uv run --no-sync python scripts/check_database_pool.py --headroom 10
```

The command reads `max_connections`, `superuser_reserved_connections` and `reserved_connections` (zero if unavailable), subtracts headroom for other clients/operations, and exits nonzero when planned peak does not fit. Increase `--headroom` to cover all other applications, migration/inspection tools and operational margin; its default 10 is an example, not observed demand. It prints only non-secret sizing data and does not write business rows. It uses existing environment settings; offline setup can supply unused cloud-key placeholders because this tool invokes no AI/carrier providers.

Run synthetic pressure on a dedicated development/disposable database:

```bash
uv run --no-sync python scripts/check_database_pool.py --headroom 10 \
    --run-load --concurrency 100 --units-per-call 3 --audio-wait-ms 10
```

Each simulated call opens a short session/transaction, runs `SELECT 1`, commits/closes, then waits outside the session. Output includes completed units, timeouts/errors, peak/final checked-out connections and P50/P95 unit latency (checkout + query + commit/close). Capacity preflight must pass before load runs. This measures one process and simulated media waits; benchmark representative writes/real audio and the full process topology before claiming production capacity. Do not increase pools solely to hide long transactions. Consider PgBouncer when measured process fan-out needs it; validate asyncpg prepared statements and pooling mode first.

A live call has many independent units of work. Do not pass an ORM session into the media pipeline, await inference/audio/Redis or make external provider calls inside a unit of work. Use plain domain records after exit. Existing repository writes require explicit commit; omitted commit, exceptions and cancellation rollback/close, and active nested reuse is rejected.

## Backend and private diagnostics

```bash
uv run --extra telephony uvicorn roma.main:create_app --factory --host 127.0.0.1 --port 8020
```

`roma.main` configures logging, database/service lifecycle and REST hang-up. It rejects an unreachable localhost public base because Twilio reaches `/answer` and `/ws` externally. The development `scripts/serve_media:create_app` adds debug routes using the same production composition. Those routes may reveal transcript/turn diagnostics and must stay private.

`GET /health` is process health, not evidence that models, carrier credits, migrations or GPU capacity are ready. `/answer` requires migrated PostgreSQL and returns 503 when durable acceptance is unavailable.

`scripts/start_roma.sh` restarts Redis/tunnel/backend processes and rewrites the temporary public URL in `.env`. It is an operational action, not an offline verification command. Tunnels are development tooling; production uses an owner-selected stable TLS origin. Protect access logs because outbound answer URLs currently contain a lead token query.

## Workers and inspection

Run these as separate processes after services/migrations/configuration are ready:

```bash
uv run --extra telephony --extra workers python scripts/run_postcall_worker.py
uv run --extra telephony --extra workers python scripts/dispatch_background_jobs.py
uv run --extra telephony --extra workers python scripts/run_background_worker.py
```

Use one recording consumer. Its `--queue=spool` path supports spool recovery and `--drain` processes existing messages. Dispatcher `--once` publishes one batch. Dramatiq defaults to one process/four threads and is stopped normally to settle active work.

Inspect PostgreSQL using a private authenticated SQL client:

```sql
SELECT job_type, status, attempts, available_at, last_error
FROM followup_jobs ORDER BY created_at DESC LIMIT 20;
```

Do not replay dead letters until the business effect/idempotency/privacy consequences are understood. `send_followup` remains ineligible; these processes do not authorize messaging.

## Offline checks

```bash
make ci
make test
uv run --no-sync python scripts/run_eval.py
```

Existing PostgreSQL tests try a disposable Testcontainers database, then installed native `initdb`/`pg_ctl` binaries (including `/Library/PostgreSQL/18/bin`). They never target the configured working database. Report unavailable-service skips/failures explicitly. Broker integration tests require a disposable local Redis server.

Sentence aggregation needs NLTK `punkt_tab` environment data; if missing, install it explicitly as setup and verify provenance before tests. Tests must not download resources implicitly. Current async tests use `asyncio.run`.

## Authorized carrier checks and recovery

Before a separately authorized test, verify destination/consent, all provider readiness/credits, migrated database, window/denylist/budget/cap, public TLS reachability, signature external URL and hang-up cleanup. Current ₹200 source ceiling is not a provider balance. `scripts/place_test_call.py` is for an approved verified handset, not production dialing clearance.

Recovery drills at L8–13 cover GPU OOM/restart/admission, Redis reconstruction from durable checkpoints, PostgreSQL restore/PITR, safe job replay, immediate knowledge-version deactivation/cache exclusion and compatible API/worker/model rollback. Define RTO/RPO, stop new intake, preserve queue/spool/database state and record observed recovery evidence. These are future acceptance procedures, not guarantees of the present code.

See [verification](12-verification.md), [security](07-security.md), [webhooks](18-webhook-idempotency.md), [jobs](19-background-task-framework.md) and [decisions](decisions.md).

## Claude section 11 text workflow

Read [the selected source](roadmaps/README.md) and [Claude adapter contract](25-claude-llm.md). `make text-console` and `make language-lab` default to mocks. Tests use injected SDK transports and never dial, spend or download models. Preserve the existing private `.env`; set `ANTHROPIC_API_KEY` there and use `ANTHROPIC_MODEL=claude-haiku-5-5`. Never print key values. `.env.example` documents anthropic output/deadline/retry/token/spend limits; low effort is the default and temperature/top_p/top_k must be omitted.

Synthetic paid Claude verification is explicitly authorized for this switch. Use synthetic identities/prompts only:

```bash
uv run --no-sync python scripts/text_console.py --provider claude --allow-paid-api
uv run --no-sync python scripts/claude_language_lab.py --provider claude --allow-paid-api
```

The scripts still require the explicit paid flag; no OpenAI calls or fallback. Native language scoring and full voice/interruption approval remain pending. A successful synthetic API response proves access/stream/accounting, not native quality. No local LLM is installed, and the text switch needs no schema change or seed. On auth/billing/credit/invalid requests stop immediately; transient pre-text retries remain inside the total budget. Discard partial failed text, use approved fallback and retain conservative reservations when usage is uncertain. Investigate Claude 429/5xx/outage without retrying beyond the turn deadline or weakening safety/booking. Distinguish cache creation/read and ordinary input cost; preserve fail-closed spend metering.

## Section 12 safety audit

Use `make test-ci` for bounded mocked safety/API/native-PostgreSQL checks, and `make test` for the full offline suite. Automated tests never dial or consume AI quota. Production startup wires PostgreSQL audit into the existing pre-TTS processor; `make text-console` remains synthetic and its audit sink is process-local. A durable text integration must inject `SafetyService(PostgresSafetyEventStore(session_factory))` for an existing provider call ID, alongside the checkpoint store.

Query `/api/v1/safety/events`, `/api/v1/safety/stats` and `/api/v1/calls/{call_id}/safety-events` with the existing bearer authorization; never put tokens into URLs. Optional call/rule/category filters use v1 validation; event lists also accept limit/offset/sort. A missing call returns 404, a missing database 503 and invalid parameters 422. Follow [the REST contract](24-rest-api-design.md) and [safety contract](04-guardrails.md).

Investigate `safety_audit_write_failed` as a lost/uncertain durable write, not a successfully saved event. Inspect database availability and pool pressure; do not bypass safety to restore speech. Event-ID retries are idempotent when the same activation is available, but automatic durable outage replay is not implemented here. There is no section 12 migration or seed, and no working-database data was altered during verification.

## Section 13 booking operations

Production composition uses PostgreSQL booking plus Redis advisory leases. Apply existing migrations with `make db-upgrade`; section 13 needs no new migration. Do not seed the working database as a side effect of startup/tests. Approved active branch reference data, its IST timezone and actual future `appointment_slots` must be provisioned separately through existing repository/import tooling. Demo references are inactive and do not authorize real availability; an empty DB intentionally offers no invented times.

Defaults in `.env.example`: `BOOKING_HOLD_TTL_SECS=90`, `BOOKING_DB_TIMEOUT_SECS=3`, `BOOKING_REDIS_TIMEOUT_SECS=1`. The database bound includes pool checkout and cancels failed/late units of work. Keep the existing pool/process budget; a slot lock lasts only through one booking/cancellation transaction. Redis holds are transient and expiry is normal, never evidence of a reservation. Inspect PostgreSQL appointments after uncertain commit responses, using stable retry identities instead of making a fresh request.

A 409 means choose another verified slot; repeated 503 means check PostgreSQL/Redis availability, configured references and pool pressure. Do not restore static unlimited availability in production. Cancellation updates appointment and slot together; never manually clear a booked flag without reconciling active appointments. Capacity remains one, and terminal records require a new booking rather than PATCH reactivation. Reconnect reconciles same-SID appointment truth before prompts; redial identity/recovery drills remain later work. External calendar/CRM/WhatsApp delivery is not promised.

Run `make test-ci` for mocked provider/API/controller checks plus native disposable PostgreSQL/Redis booking races and failure tests. `make test` includes the complete existing suite. Native binaries and tokenizer preparation are covered above. Tests never need paid AI quota, real caller data or mutation of the configured `roma` database. See [Level 3 evidence](levels/level-03.md) and [API bodies](24-rest-api-design.md#appointment-holds-and-confirmation).

## Section 14 audio lab and stream metrics

`make audio-lab` prints deterministic JSON for clean/loss/reorder/duplicate/burst-delay/variable-jitter profiles. It loads no `.env`, credentials, model, database or network service and writes no artifact by default. To compare fixed targets, run `uv run --no-sync python scripts/audio_lab.py --jitter-ms 40` (also 50 or 60). Use `make test-ci` for the codec/filter/framing/buffering/live validation and clean energy-VAD checks; `make test` includes full regressions.

The report is synthetic scheduling/conversion evidence, not speech recognition quality or real end-to-end latency. Default target 60 ms, queue eight normalized packets; timestamps and the injected monotonic clock drive playout. A timer must drain due audio even without another arrival. Gaps are counted and discontinuous; there is no adaptive delay or PLC. PCM16 final tails need explicit padding/discard and independent streams need reset/new instances.

Production continues with native Pipecat codec/control behavior and the current 8 kHz cloud/VAD/recording path. `ValidatedTwilioSerializer` rejects wrong stream/track, malformed base64, bad/regressing metadata, duplicate/reordered media and oversize envelopes before decoding; it checks PCM mono/alignment afterward. At normal teardown, inspect `carrier_audio_summary` with its stream ID, or active `CallHandles.audio_metrics`. Counts contain no payload/transcript/secrets. `sequence_gaps` counts missing **media.chunk messages**, not missing samples or TCP packet loss. Correlate gaps with invalid/reordered counts; diagnose TCP delay/backpressure independently.

Bounds are 8,000 decoded audio bytes and 16,384 envelope characters; actual Twilio messages may contain more or fewer than 20 ms. Do not enable the lab's 16 kHz/filter/jitter path on live calls until later provider/performance gates pass. Adaptive buffering, PLC and measured UDP/RTP VAD/ASR impairment remain gated. See [section 14 policy/evidence](levels/level-04.md) and [audio contract](05-endpointing-vad.md).

## Sections 15–17 speech-input lab

From `<project-root>`, after the locked development/telephony install:

```bash
make speech-lab
```

This default uses synthetic probabilities/transcripts, no `.env`, services, credentials, new weights or model/API calls. It reports short-ack/pause segmentation, smoothed caller routes and checkpoint round-trip. Synthetic timings are not ASR performance. Existing bundled Silero has a separate silence/reset smoke test.

For an explicit real lab, supply an uncompressed mono 16 kHz PCM16 WAV (at most five minutes). No OpenAI call or TTS is made. Default diagnostics omit caller text; add `--show-transcripts` only for approved local data. The script uses real bundled Silero, a bounded sequential segment path and the selected STT adapter. ASR errors fail with safe codes; native/quality approval is not automatic.

```bash
# Cached local baseline only; never downloads a missing model.
uv run --no-sync python scripts/speech_lab.py --provider whisper --wav /path/to/approved.wav
# Cached, reviewed IndicConformer only. Set INDIC_MODEL_REVISION to the reviewed 40-character commit.
HF_HUB_OFFLINE=1 INDIC_TRUST_REMOTE_CODE=true uv run --no-sync python scripts/speech_lab.py --provider indic_conformer --wav /path/to/approved.wav
# Public benchmark audio only; never supply private caller audio to public Spaces.
# Use STT_LANGUAGE=hi for Hindi or en for the separate public Whisper Space.
STT_LANGUAGE=gu uv run --no-sync python scripts/speech_lab.py --provider hf_public_lab --allow-network --public-benchmark --wav /path/to/public-fleurs.wav
# Optional private custom handler; requires its separately approved endpoint/capacity.
uv run --no-sync python scripts/speech_lab.py --provider hf_indic_conformer --allow-network --wav /path/to/approved.wav
# Separate authenticated HF router, only when explicitly selected and credits exist.
STT_LANGUAGE=en uv run --no-sync python scripts/speech_lab.py --provider hf_whisper --allow-network --wav /path/to/approved.wav
```

`roma/core/speech_settings.py` reads `STT_LANGUAGE=hi|gu|en`, `STT_TIMEOUT_SECS` (default 30, maximum 60), `STT_MAX_AUDIO_SECS` (default/maximum 30), Indic decoder/revision/code opt-in, Whisper model/cache path, `HF_ASR_ENDPOINT_URL` and redacted `HF_TOKEN`. Public-Space calls use `HF_PUBLIC_TIMEOUT_SECS` (default 90, maximum 120), including upload/queue/response, to accommodate the observed 61.589-second English smoke. `HF_INDIC_SPACE_URL` and `HF_WHISPER_SPACE_URL` default to the selected HTTPS Space roots; `HF_WHISPER_MODEL` configures the separate router route. Token aliases `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_API_KEY` and `HUGGING_FACE_API_KEY` are accepted; `HF_TOKEN` wins when several are present. Keep tokens in environment/ignored `.env`, never paste them into commands/logs. Do not point the URL at a Hub page or generic hosted handler; see [the custom endpoint template](../benchmarks/asr/hf_endpoint/README.md). No endpoint creation/inference or ASR runtime installation is part of automated checks. The real public smoke is separately recorded in [Level 4](levels/level-04.md#implemented-boundary-audit-and-hosted-smoke).

The optional Indic runtime uses the official model card's PyTorch/torchaudio/Transformers plus ONNX dependencies; faster-whisper uses its own optional runtime/converted cache. Provision these in a separately reviewed compatible environment; they are deliberately absent from the default lock/install. Cache misses fail without a network fallback. The selected gated Indic model needs accepted access terms and reviewed pinned custom code; `HF_HUB_OFFLINE=1` must precede Python/Hub imports. It does not support English; select the comparison baseline for English rather than overriding the hint.

`hf_public_lab` uses the community IndicConformer 600M CTC Space for Gujarati/Hindi and the official Whisper large-v3 Space for English. It requires `--allow-network --public-benchmark`; the CLI marks each request `data_classification=public_benchmark`. Both clients are anonymous by default. Only the later explicit official Whisper authentication option uses the HF edge token; the Indic community client remains credential-free. Keep private audio on an approved private route. `hf_whisper` sends its token only to the fixed official HF inference router. The read-valid token returned 402 for unavailable inference credits on 2026-10-18; that is an operator prerequisite failure with no automatic retry, fallback, credit purchase or deployment.

`EndpointConfig` owns the lab's thresholds/durations; [Level 4](levels/level-04.md#sections-1517-design-and-acceptance) records defaults and limits. Production retains the current cloud/VAD/recording path. Three public clips have real hosted inference/timing evidence; representative ASR WER/CER/RTF, noisy/code-mix endpoint quality, later serving and complete voice approval remain pending.

A saved English route with an Indic adapter returns `stt-unsupported-or-invalid-request`; it never relabels English as Hindi or silently switches models. Select the explicit Whisper comparison for that lab. The real-WAV CLI exits nonzero on segment errors and sanitizes malformed/truncated WAV failures.

To reproduce the three-language public smoke through the actual configured adapters:

```bash
uv run --no-sync python scripts/hf_asr_smoke.py --allow-network --fetch-public-samples --report benchmarks/asr/hosted_smoke.json
```

This command fetches only public FLEURS validation-row-0 audio/reference metadata for `gu_in`, `hi_in` and `en_us`, caches WAV/identity metadata under ignored `var/roma/asr-public`, and declares `public_benchmark` in every STT request. It downloads no ASR model weights and never uses the paid router. Conversion requires an already installed `afconvert` (macOS) or `ffmpeg`; the script installs neither. Omit `--fetch-public-samples` to reuse the hash-checked cache. The explicit smoke report includes public reference/hypothesis text, per-clip normalized WER/CER and observed total request time; it is not the default caller-transcript diagnostic. One clip per language is a smoke score, not a representative quality estimate. Public service failure exits nonzero with a safe error and does not trigger another provider.

## Section 18 ASR benchmark and failure review

Run from `<project-root>`. Public FLEURS downloads/inference require explicit network flags. Anonymous mode sends no token; the explicitly enabled official Whisper edge authentication below is the sole public-host exception. Raw audio and exported failures stay under ignored `var/roma`. The [Level 4 brief](levels/level-04.md#section-18-asr-benchmark) owns dataset limitations and model decisions.

```bash
# Existing public cache can be reused without network. Add --allow-network
# --fetch-public-samples --samples-per-language N to fetch more public rows.
uv run --no-sync python scripts/asr_benchmark.py prepare \
  --manifest var/roma/asr-benchmark/manifest.json \
  --include-stitched-code-mix --variants clean noise telephone

uv run --no-sync python scripts/asr_benchmark.py run \
  --manifest var/roma/asr-benchmark/manifest.json \
  --provider hf_indic --languages gu hi gu-en hi-en \
  --report var/roma/asr-benchmark/indic.json --allow-network
uv run --no-sync python scripts/asr_benchmark.py run \
  --manifest var/roma/asr-benchmark/manifest.json \
  --provider hf_whisper_space \
  --report var/roma/asr-benchmark/whisper.json --allow-network

# Offline read/export; JSON retains public reference/hypothesis/edit counts.
uv run --no-sync python scripts/asr_benchmark.py failures \
  --report var/roma/asr-benchmark/indic.json --min-wer 0.25 \
  --manifest var/roma/asr-benchmark/manifest.json \
  --export-dir var/roma/asr-benchmark/failures-indic
# Inspect the exported per-clip JSON files and matching WAVs before replay.
# A replay is another explicit network request, not an automatic retry.
uv run --no-sync python scripts/asr_benchmark.py replay \
  --manifest var/roma/asr-benchmark/manifest.json --provider hf_indic \
  --report var/roma/asr-benchmark/indic.json --allow-network

# Finish inference first. This command opens a short SQL transaction only.
uv run --no-sync python scripts/save_benchmark.py \
  --report var/roma/asr-benchmark/indic.json \
  --run-key section18-indic-20261019-v1 --allow-database-write
```

`--resume` skips completed cases; `--retry-errors` explicitly retries errors. Keep provider/config/manifest identities identical. Changing an already saved run's evidence requires a **new run key**; the same key is immutable. Provider errors have no invented WER/CER. A completed report means every selected case was attempted; a report with errors exits nonzero and persists as failed. `request_rtf` includes remote transfer/queue time, while unavailable isolated compute timing stays null. Native code-mix audio is not created by joining two read-speech clips.

## Section 19 local TTS and phrase cache

`make tts-lab` exercises synthetic PCM, multilingual normalization, safety, cache hits and carrier frames without weights/network/credentials. Tones do not measure speech quality. Local Indic inference requires separate explicit setup; the owner approved required TTS downloads on 2026-10-19. It never occurs during app startup or automated tests.

The official [Indic-TTS release](https://github.com/AI4Bharat/Indic-TTS/releases/tag/v1-checkpoints-release) supplies FastPitch/HiFi-GAN/speaker files. Archives are roughly 1.5 GB per language; maintain sufficient space for a temporary archive plus extracted artifacts. The setup script permits only reviewed GitHub release origins, verifies exact byte ranges, bounds extraction, hashes all five files and writes a manifest. Publisher checksums are absent: recorded hashes identify downloaded artifacts, not independent publisher attestation. Local pickle checkpoints are trusted code/data inputs; provision only reviewed official artifacts.

```bash
# Isolate legacy Coqui dependencies from the app's locked environment.
uv venv --python 3.11 var/roma/tts-runtime
uv pip install --python var/roma/tts-runtime/bin/python \
  -r benchmarks/tts/runtime-macos-arm64-py311.txt
uv run --no-sync python scripts/setup_indic_tts.py --allow-download \
  --workers 8 --resume-downloads --languages gu hi en

INDIC_TTS_PYTHON="$(pwd)/var/roma/tts-runtime/bin/python" \
INDIC_TTS_MANIFEST="$(pwd)/var/roma/indic-tts/manifest.json" \
TTS_TIMEOUT_SECS=180 TTS_MAX_AUDIO_SECS=60 \
uv run --no-sync python scripts/tts_lab.py --provider indic_tts \
  --language all --audio-dir var/roma/tts-lab \
  --report var/roma/tts-lab/report.json

uv run --no-sync python scripts/save_benchmark.py \
  --report var/roma/tts-lab/report.json \
  --run-key section19-indic-20261019-v1 --allow-database-write
```

The recorded runtime snapshot is for this Mac ARM64/Python 3.11; other platforms need a separately verified compatible environment. The model manifest pins the runtime distribution/version and every acoustic/config/speaker/vocoder hash. The adapter rejects absent/tampered files without downloading or falling back. CPU workers are killed/reaped on cancellation/timeout and cold-load on each cache miss; reported synthesis time includes process startup and model loading. No resident-model or streaming-TTFA claim is made. A process-local bounded LRU shares simultaneous misses and keys exact safety-approved normalized text, language, voice, model/artifact version, normalization version and format. Every cache hit still passes safety audit.

Numbers are conservatively spelled digit by digit; valid ISO/Indian dates become day/month/year and reviewed course terms receive language-specific pronunciation. Native naturalness remains a review requirement. The 16 kHz waveform is filtered to 8 kHz where needed, mu-law encoded and padded only at the final 20 ms carrier tail. Existing Sarvam/Pipecat playback and fixed phrase assets remain the live path until later integration approval. See [Level 5](levels/level-05.md).

The passing section 19 lab uses an explicit 60-second audio bound because the long Gujarati fixture produces 32.279 seconds; the default remains 30 seconds and correctly rejects it. Model workers reject unsupported lexical characters before synthesis; Exact approved Roman safety substitutions now resolve to hash-pinned Devanagari pronunciation through the safety service, with Hindi synthesis across caller routes. Native listening approval remains pending before future live integration. The new normalizer rejects invisible format controls, and failed cache attempts store no audio. Reports resolve the attempted model identity before synthesis; unresolved setup provenance is retained on disk and rejected by the SQL importer.


The section 19 approved-line lab reuses already provisioned artifacts; no setup download is needed:

```bash
# Default is synthetic/offline; optional real inference below uses the installed runtime.
uv run --no-sync python scripts/tts_safety_lab.py
INDIC_TTS_PYTHON="$(pwd)/var/roma/tts-runtime/bin/python" \
INDIC_TTS_MANIFEST="$(pwd)/var/roma/indic-tts/manifest.json" \
TTS_TIMEOUT_SECS=180 \
uv run --no-sync python scripts/tts_safety_lab.py --provider indic_tts \
  --audio-dir var/roma/tts-safety-lab/audio \
  --report var/roma/tts-safety-lab/report.json
```

Expect six misses/stores and 18 audited hits, actual Hindi voice for all requested language routes. The report marks its in-memory synthetic audit sink and pending native review. Preserve canonical policy text and existing fixed audio; unsupported ordinary text must still fail before inference/cache.

For hosted Whisper, retain the default `HF_WHISPER_SPACE_URL=https://hf-audio-whisper-large-v3.hf.space` with `HF_WHISPER_API_NAME=transcribe`. If explicitly selecting `https://openai-whisper.hf.space`, set `HF_WHISPER_API_NAME=predict` as well. The corrected adapter reads detailed events from the same job; `asr-hosted-quota-exhausted` means the public service has no admitted GPU quota. Stop replaying until capacity is available; switching public demos, increasing timeout or changing language cannot establish a successful comparison. Anonymous mode sends no token, the explicit official Whisper edge option below is scoped to reviewed hosts, and the credit-requiring HF router is never a silent fallback. Preserve immutable failed reports, then use a new report/run identity for a fresh complete comparison. [Level 4](levels/level-04.md#hosted-whisper-failure-diagnosis--2026-10-19) records current external limits.


## Authenticated official Whisper benchmark

The user's existing HF key is now read from `.env` with `HF_WHISPER_SPACE_AUTHENTICATED=true` for the requested official Whisper benchmark. The library default remains false. The token uses Gradio's `X-HF-Authorization` edge header, restricted to `hf-audio-whisper-large-v3.hf.space` or `openai-whisper.hf.space`, with redirects/proxies disabled. It is never forwarded to the Indic community Space or arbitrary endpoints, logged, stored in reports or committed. Existing key values are unchanged.

```bash
# Use only the existing public FLEURS cache; no model download.
HF_WHISPER_SPACE_AUTHENTICATED=true \
uv run --no-sync python scripts/asr_benchmark.py run --allow-network \
  --manifest benchmarks/asr/section18/manifest.json \
  --audio-dir var/roma/asr-public --provider hf_whisper_space \
  --report var/roma/asr-benchmark/whisper-authenticated-new.json
```

The actual authenticated run passed 7/15 cases, then the account's own ZeroGPU allowance was exhausted. The separately authenticated inference router returned credits unavailable. A valid token identifies the account and may unlock its free allowance; it does not add unlimited GPU time or buy credits. New runs stop at the first quota exception and preserve a resumable checkpoint. Resume explicitly only after account capacity resets/is available, using `--resume --retry-errors` and the same endpoint/API/auth provenance. Do not rotate keys/endpoints to conceal capacity failures, purchase credits automatically or download weights. Historical reports remain unchanged; [Level 4](levels/level-04.md#authenticated-whisper-follow-up--2026-10-19) owns the measurements.

## Local Whisper base benchmark

Only multilingual `base` is approved for this local follow-up. Install the optional locked runtime, then provision its pinned four-file snapshot once. This step downloads approximately 148 MB; runtime dependencies use additional disk/RAM. No HF inference service or token is required for this public model.

```bash
uv sync --locked --extra dev --extra telephony --extra workers --extra voice-lab
HF_HUB_DISABLE_IMPLICIT_TOKEN=1 uv run --no-sync hf download Systran/faster-whisper-base \
  config.json model.bin tokenizer.json vocabulary.txt \
  --revision ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66 \
  --local-dir var/roma/models/whisper-base --max-workers 2

# All 15 cases, using the existing public audio cache. No network/download during inference.
HF_HUB_OFFLINE=1 uv run --no-sync python scripts/asr_benchmark.py run \
  --provider whisper_local --local-model-dir var/roma/models/whisper-base \
  --manifest benchmarks/asr/section18/manifest.json \
  --report var/roma/asr-benchmark/whisper-base-local.json

uv run --no-sync python scripts/asr_benchmark.py failures \
  --report var/roma/asr-benchmark/whisper-base-local.json \
  --manifest benchmarks/asr/section18/manifest.json \
  --export-dir var/roma/asr-benchmark/failures-whisper-base-local

uv run --no-sync python scripts/save_benchmark.py \
  --report var/roma/asr-benchmark/whisper-base-local.json \
  --run-key section18-whisper-base-local-20261019-v1 --allow-database-write
```

Do not overwrite the saved run/report and reuse its immutable SQL run key for a new measurement. Use a new report filename/run key for each fresh run; `--resume` is only for the same identity/checkpoint. Hosted large-v3 evidence remains separate. The [Level 4 brief](levels/level-04.md#local-whisper-base-baseline--2026-10-19) records successful execution and failed multilingual quality; runtime availability alone does not pass the gate.

## Section 20 microphone-to-speaker prototype

```bash
# Default integration exercise: no hardware, keys, network or DB writes.
make voice-prototype

# Install optional audio I/O and faster-whisper runtime; host PortAudio is required.
uv sync --locked --extra dev --extra telephony --extra workers --extra voice-lab

# Explicit integration test using the approved base cache; full native/quality gates remain pending.
# Do not resolve a venv interpreter symlink to its base Python executable.
HF_HUB_OFFLINE=1 STT_PROVIDER=whisper \
WHISPER_MODEL_ID="$(pwd)/var/roma/models/whisper-base" \
STT_TIMEOUT_SECS=60 STT_LANGUAGE=en \
INDIC_TTS_PYTHON="$(pwd)/var/roma/tts-runtime/bin/python" \
INDIC_TTS_MANIFEST="$(pwd)/var/roma/indic-tts/manifest.json" \
TTS_TIMEOUT_SECS=180 \
uv run --no-sync python scripts/voice_prototype.py --mode local --llm-provider mock \
  --allow-microphone --allow-database-write
```

The command above uses mock wording with real local microphone/ASR/TTS and no paid client. For the authorized Claude path set private `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL=claude-haiku-5-5`, change to `--llm-provider claude` and add `--allow-paid-api`. This does not pass native/representative prerequisites or change production speech. Synthetic mode remains mocked; OpenAI calls/fallback are forbidden.

Use headphones and consenting test participants; Ctrl+C stops. Local mode refuses hosted ASR and missing local runtime prerequisites. It warms cached ASR, short safe TTS and bundled Silero with synthetic silence before capture or creating the lab call. No ASR runtime/weights are installed by this command. Only the already approved pinned Whisper base cache is currently provisioned; IndicConformer would additionally require separately approved reviewed weights/custom code. All local providers remain offline/cache-only. Claude timeout/fallback/spend caps apply; actual model availability and native quality are separate gates.

Set `STT_LANGUAGE=gu`, `hi` or `en` for the caller’s starting test language; this initializes the saved ASR route before any transcript exists. Subsequent changes still use the existing smoothed language router. Optional `--input-device N --output-device N` select PortAudio indices; `--max-turns N` defaults to 30, capped at 60 and five minutes. The microphone closes before speaking; barge-in is later work. Every attempt/ready/result uses short PostgreSQL writes in an isolated `voice-lab-…` call. Redis holds and existing DB reference slots back actual booking; startup creates no slots, seed data or migration. Schema head must already be `20261004_0003`.

By default, durable events/turns omit full transcript text; checkpoint slots remain necessary persisted participant data. Use `--persist-transcripts` only for approved test retention: caller text is in the call event and final agent text in the correlated call_turn row. Existing schema has one row per logical turn, so speaker pairs are not forced into duplicate turn numbers. Failed/unaccepted input remains an attempt event. Four-hour expiry metadata requires the project's retention/deletion procedure; do not assume automatic purging or delete committed booking truth. No microphone audio is written to disk or uploaded to HF.

The default lab and automated CI use synthetic PCM, scripted ASR and a fake speaker, never the physical microphone or paid API. [Level 6](levels/level-06.md) distinguishes implemented integration, actual fixed-question synthesis and the still-pending hardware/native gate.


Claude schema readiness is distinct from a caller turn. The official SDK transforms the Pydantic schema for API compatibility; first-time remote schema compilation can exceed the ordinary four-second attempt budget and should fail to the approved line. Do not silently raise live-turn timeouts or make paid startup requests. The explicit synthetic readiness evidence uses a separate larger timeout, then confirms the normal budget. Current short Haiku requests use low effort with optional thinking disabled; temperature/top_p/top_k are omitted. The 40-case lab admits requests against its remaining ₹2 run ceiling and retains blank native scores.


The [2026-10-20 physical English follow-up](levels/level-06.md#real-claude-english-microphone-follow-up--2026-10-20) passed real Claude extraction and prepared-state wording through installed local speech, with clear playback confirmed by the owner. The working DB has no branch/slot setup: expect approved unavailable speech rather than normal discovery. Explicit owner-approved active demo references were exercised only in a disposable database, distinct from the existing inactive `seed_demo` profile. Do not silently activate demo offerings in the working DB or treat test slots as real appointments. No appointment was committed in the microphone demo.

For local speech, the voice-specific prompt spells every loanword/acronym in Devanagari; unsupported generated script and token-truncated replies use the approved fallback before TTS. The provider's configured output limit and existing stage/spend/time limits still apply. The documented CLI remains unchanged apart from using that configured output ceiling. The temporary test harness used an audible capture cue; normal CLI interaction should wait until startup is ready and until playback ends. A silent attempt or session-ceiling stop is a failed attempt, not a model pass. The owner deferred further live/paid testing due to limits; native/multilingual and complete booking gates remain pending.
