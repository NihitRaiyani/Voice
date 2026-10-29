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

Current code head is `20261026_0007`: human sessions/refresh credentials (`0004`), managed privacy (`0005`), knowledge governance (`0006`) and vectors/ingestion (`0007`) append to the preserved `20261004_0003` schema. `0007` requires the PostgreSQL `vector` extension to be installed for that server and migration permission to create it. Sections 34–40 exercised `0007` only on disposable databases; do not apply it to working `roma` as a documentation/test side effect. The explicitly authorized local operational rollout upgraded working `roma` to `0006` on 2026-10-24 after an isolated backup/restore and migration/auth rehearsal; other environments require their own rollout. Applied migrations `20260920_0001` and `20260930_0002` remain unchanged. Seed/demo data now has a [separate opt-in command](21-database-migrations.md#seeds-stay-outside-schema-history): `APP_ENV=dev python scripts/seed_demo.py --demo`. It requires head, preserves existing data, inserts only inactive synthetic offerings plus role vocabulary, and creates no accounts. Keep schema upgrades seed-free. Do not downgrade a working database merely to demonstrate rollback; tests use disposable databases.

The checkpoint migration was verified on empty and legacy-populated disposable databases, and configured `roma` was recorded at then-head `20261004_0003` on 2026-10-04. At the historical section 28 increment, `20261023_0004` was tested only in disposable databases. The later owner-authorized 2026-10-24 rollout applied through `20261024_0006` to working `roma`, as recorded above. Run `alembic upgrade head` in every other intended environment before starting code that queries turn-language metadata, and use `alembic current`/`alembic check` to verify its state. Existing rows retain NULL language; schema migrations seed no historical guesses or sample records; the separate demo command is explicit.

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

Do not replay dead letters until business effect/idempotency/privacy consequences are understood. Section 26 followup execution requires persisted consent and a configured idempotent sender; the default has neither and sends no message.

## Offline checks

```bash
# One-time explicit tokenizer install; never performed by tests or app startup.
make nltk-data
make ci
make test
uv run --no-sync python scripts/run_eval.py
```

Existing PostgreSQL tests try a disposable Testcontainers database, then installed native `initdb`/`pg_ctl` binaries (including `/Library/PostgreSQL/18/bin`). They never target the configured working database. Report unavailable-service skips/failures explicitly. Broker integration tests require a disposable local Redis server.

Sentence aggregation needs NLTK `punkt_tab`. `make nltk-data` explicitly installs that package only, with bounded network waits, under ignored `var/roma/nltk_data`. Make exports the same `NLTK_DATA` for `test`, `test-ci`, `app-start` and the prototype. Direct Python startup uses the same repo-local default. Tests, app startup and prototype startup validate that the English parameters load; missing/corrupt data stops with `TOKENIZER-DATA-MISSING: run make nltk-data before startup or tests.` They never download resources implicitly. Current async tests use `asyncio.run`.

`configs/ci-tests.json` is the single explicit offline module catalog. `make test-ci` validates and runs it; GitHub Actions calls that same target. Every `tests/**/test_*.py` must appear in `modules` or in `excluded` with a nonempty reason. The current catalog includes every module and has no exclusions. The meta-test rejects new unaccounted modules, stale entries, duplicates and unreasoned exclusions.

Three historical filenames intentionally remain: `test_openai_llm.py` checks the old adapter's SDK streaming/error/budget/accounting compatibility with fake HTTP transports; `test_openai_language.py` checks the reusable language corpus, safety and human-scoring rules using mocks; `test_openai_llm_settings.py` checks legacy settings and that current factory selection rejects OpenAI/Qwen. They do not enable an OpenAI runtime or make paid requests.

`make ci` applies migrations to its configured `DATABASE_URL`. For verification that must preserve working `roma`, explicitly use isolated native PostgreSQL/Redis services and the disposable database URL. On resource-limited macOS, release the isolated migration server after its schema check before the full tests create their own disposable clusters.

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

To reproduce only the complete offline caller flow with disposable demo calls/branch/slots, run:

```bash
HF_HUB_OFFLINE=1 uv run --no-sync pytest -q -p no:cacheprovider \
  tests/repositories/postgres/test_booking_module.py::test_offline_conversation_books_restores_and_protects_demo_slot
```

This test proves offer, readback, affirmation, one SQL commit, slot/call updates, restore and competing-caller protection. It does not seed configured `roma`, invoke Claude, open a microphone or synthesize audio.

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
# Cached, reviewed IndicConformer only; override stale local .env values explicitly.
HF_HUB_OFFLINE=1 INDIC_TRUST_REMOTE_CODE=true STT_LANGUAGE=gu \
INDIC_MODEL_REVISION=2a77d305038dfc502fd9464e7d028b90381976cb \
uv run --no-sync python scripts/speech_lab.py --provider indic_conformer --wav /path/to/approved.wav
# Public benchmark audio only; never supply private caller audio to public Spaces.
# Use STT_LANGUAGE=hi for Hindi or en for the separate public Whisper Space.
STT_LANGUAGE=gu uv run --no-sync python scripts/speech_lab.py --provider hf_public_lab --allow-network --public-benchmark --wav /path/to/public-fleurs.wav
# Optional private custom handler; requires its separately approved endpoint/capacity.
uv run --no-sync python scripts/speech_lab.py --provider hf_indic_conformer --allow-network --wav /path/to/approved.wav
# Separate authenticated HF router, only when explicitly selected and credits exist.
STT_LANGUAGE=en uv run --no-sync python scripts/speech_lab.py --provider hf_whisper --allow-network --wav /path/to/approved.wav
```

`roma/core/speech_settings.py` reads `STT_LANGUAGE=hi|gu|en`, `STT_TIMEOUT_SECS` (default 30, maximum 60), `STT_MAX_AUDIO_SECS` (default/maximum 30), Indic decoder/revision/code opt-in, Whisper model/cache path, `HF_ASR_ENDPOINT_URL` and redacted `HF_TOKEN`. Public-Space calls use `HF_PUBLIC_TIMEOUT_SECS` (default 90, maximum 120), including upload/queue/response, to accommodate the observed 61.589-second English smoke. `HF_INDIC_SPACE_URL` and `HF_WHISPER_SPACE_URL` default to the selected HTTPS Space roots; `HF_WHISPER_MODEL` configures the separate router route. Token aliases `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_API_KEY` and `HUGGING_FACE_API_KEY` are accepted; `HF_TOKEN` wins when several are present. Keep tokens in environment/ignored `.env`, never paste them into commands/logs. Do not point the URL at a Hub page or generic hosted handler; see [the custom endpoint template](../benchmarks/asr/hf_endpoint/README.md). No endpoint creation/inference or ASR runtime installation is part of automated checks. The real public smoke is separately recorded in [Level 4](levels/level-04.md#implemented-boundary-audit-and-hosted-smoke).

The optional `indic-asr` extra provides PyTorch and Transformers; the application already supplies NumPy, Hugging Face Hub and ONNX Runtime. Install the locked optional runtime with `uv sync --locked --extra dev --extra telephony --extra workers --extra voice-lab --extra indic-asr`. The separately provisioned AI4Bharat snapshot is pinned to revision `2a77d305038dfc502fd9464e7d028b90381976cb`; 404 files total 2,556,502,929 bytes (2.557 GB / 2.381 GiB). The optional package directories occupy 645,087,232 bytes (645 MB / 615 MiB) on this Mac. Cache misses fail without a network fallback. The gated model needs accepted access terms and explicit reviewed-code opt-in; `HF_HUB_OFFLINE=1` must precede Python/Hub imports. It does not support English; select the Whisper baseline for English rather than overriding the hint. See [local readiness evidence](levels/level-04.md#local-indicconformer-readiness--2026-10-21).

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

# Apple Silicon optional runtime; host PortAudio is required.
uv sync --inexact --locked --extra dev --extra telephony --extra workers --extra voice-lab --extra mlx-asr

# Real integration is explicit opt-in; the owner authorized a bounded check on 2026-10-22.
# Turbo is a development baseline; quality/native gates are not passed.
# Do not resolve a venv interpreter symlink to its base Python executable.
HF_HUB_OFFLINE=1 STT_PROVIDER=whisper_mlx \
WHISPER_MLX_MODEL_DIR="$(pwd)/var/roma/models/whisper-large-v3-turbo-mlx-4bit" \
STT_TIMEOUT_SECS=60 STT_LANGUAGE=en \
INDIC_TTS_PYTHON="$(pwd)/var/roma/tts-runtime/bin/python" \
INDIC_TTS_MANIFEST="$(pwd)/var/roma/indic-tts/manifest.json" \
TTS_TIMEOUT_SECS=180 \
uv run --no-sync python scripts/voice_prototype.py --mode local --llm-provider mock \
  --allow-microphone --allow-database-write
```

Prototype diagnostics use one shared enum/mapping in `roma/core/voice_errors.py`: ASR, TTS, DB, DEVICE, CAPTURE, LLM, SAFETY and BOOKING-DATA. Setup also has TOKENIZER-DATA-MISSING; cancellation has CANCELLED. CLI messages contain fixed codes/instructions only, never raw exception details. Ready/result events store those same validated codes. A persistent DB outage cannot guarantee a durable error write; the CLI still reports DB and stops. The text service's checkpoint failures retain DB identity through the prototype boundary.

The command above uses mock wording with real local microphone/ASR/TTS and no paid client. For the authorized Claude path set private `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL=claude-haiku-5-5`, change to `--llm-provider claude` and add `--allow-paid-api`. This does not pass native/representative prerequisites or change production speech. Synthetic mode remains mocked; OpenAI calls/fallback are forbidden.

Use headphones and consenting test participants; Ctrl+C stops. For supervised runs, announce “Now listening” only after capture is actually open; setup/tool latency can otherwise miss short consent replies. Preserve the five-minute cap and use disposable demo data for booking. The [2026-10-22 follow-up](levels/level-06.md#supervised-claude-and-booking-follow-up--2026-10-22) records real local speech/Claude/booking outcomes and remaining acceptance limits. Local mode refuses hosted ASR and missing local runtime prerequisites. It warms cached ASR, short safe TTS and bundled Silero with synthetic silence before capture or creating the lab call. No ASR runtime/weights are installed by this command. Pinned Turbo MLX 4-bit is provisioned; the base comparison cache remains, and IndicConformer weights have been removed. All local providers remain offline/cache-only. To explicitly compare base, use `STT_PROVIDER=whisper` and `WHISPER_MODEL_ID` pointing to the base directory. Claude timeout/fallback/spend caps apply; actual model quality and native approval are separate gates.

Set `STT_LANGUAGE=gu`, `hi` or `en` for the caller’s starting test language; this initializes the saved ASR route before any transcript exists. Subsequent changes still use the existing smoothed language router. Optional `--input-device N --output-device N` select PortAudio indices; `--max-turns N` defaults to 30, capped at 60 and five minutes. The microphone closes before speaking; barge-in is later work. Every attempt/ready/result uses short PostgreSQL writes in an isolated `voice-lab-…` call. Redis holds and existing DB reference slots back actual booking; startup creates no slots, seed data or migration. Schema must already match the current Alembic code head (`20261024_0006` after section 33); the prototype resolves it from revision files, not a stale constant.

By default, durable events/turns omit full transcript text; checkpoint slots remain necessary persisted participant data. Use `--persist-transcripts` only for approved test retention: caller text is in the call event and final agent text in the correlated call_turn row. Existing schema has one row per logical turn, so speaker pairs are not forced into duplicate turn numbers. Failed/unaccepted input remains an attempt event. Four-hour expiry metadata requires the project's retention/deletion procedure; do not assume automatic purging or delete committed booking truth. No microphone audio is written to disk or uploaded to HF.

The default lab and automated CI use synthetic PCM, scripted ASR and a fake speaker, never the physical microphone or paid API. [Level 6](levels/level-06.md) distinguishes implemented integration, actual fixed-question synthesis and the still-pending hardware/native gate.


Claude schema readiness is distinct from a caller turn. The official SDK transforms the Pydantic schema for API compatibility; first-time remote schema compilation can exceed the ordinary four-second attempt budget and should fail to the approved line. Do not silently raise live-turn timeouts or make paid startup requests. The explicit synthetic readiness evidence uses a separate larger timeout, then confirms the normal budget. Current short Haiku requests use low effort with optional thinking disabled; temperature/top_p/top_k are omitted. The 40-case lab admits requests against its remaining ₹2 run ceiling and retains blank native scores.


The [2026-10-20 physical English follow-up](levels/level-06.md#real-claude-english-microphone-follow-up--2026-10-20) passed real Claude extraction and prepared-state wording through installed local speech, with clear playback confirmed by the owner. The working DB has no branch/slot setup: expect approved unavailable speech rather than normal discovery. Explicit owner-approved active demo references were exercised only in a disposable database, distinct from the existing inactive `seed_demo` profile. Do not silently activate demo offerings in the working DB or treat test slots as real appointments. No appointment was committed in the microphone demo.

For local speech, the voice-specific prompt spells every loanword/acronym in Devanagari; unsupported generated script and token-truncated replies use the approved fallback before TTS. The provider's configured output limit and existing stage/spend/time limits still apply. The documented CLI remains unchanged apart from using that configured output ceiling. The temporary test harness used an audible capture cue; normal CLI interaction should wait until startup is ready and until playback ends. A silent attempt or session-ceiling stop is a failed attempt, not a model pass. The owner deferred further live/paid testing due to limits; native/multilingual and complete booking gates remain pending.

## Local IndicConformer comparison

Historical reproduction only: the owner later removed this Indic cache. Do not run/reinstall it as part of current setup. The [Turbo selection](#local-single-pass-code-mix-benchmark) supersedes this comparison profile; old evidence remains readable.

Use the already provisioned optional runtime and pinned Hub cache. These commands use existing public audio only, with no network, new weights, paid provider or microphone. A full Indic report exits **1** after recording all 15 cases because English is unsupported; inspect the report rather than treating the exit as a cache/runtime failure. Selecting `--languages gu hi gu-en hi-en` instead runs only 12 cases and is a different report scope.

```bash
HF_HUB_OFFLINE=1 INDIC_TRUST_REMOTE_CODE=true \
INDIC_MODEL_REVISION=2a77d305038dfc502fd9464e7d028b90381976cb \
uv run --no-sync python scripts/asr_benchmark.py run --provider indic_local \
  --manifest benchmarks/asr/section18/manifest.json \
  --report var/roma/asr-benchmark/indic-conformer-local-new.json

# Fresh base measurement, in a separate process with the same manifest.
HF_HUB_OFFLINE=1 uv run --no-sync python scripts/asr_benchmark.py run \
  --provider whisper_local --local-model-dir var/roma/models/whisper-base \
  --manifest benchmarks/asr/section18/manifest.json \
  --report var/roma/asr-benchmark/whisper-base-local-new.json

uv run --no-sync python scripts/asr_benchmark.py compare \
  --indic-report var/roma/asr-benchmark/indic-conformer-local-new.json \
  --whisper-report var/roma/asr-benchmark/whisper-base-local-new.json \
  --report var/roma/asr-benchmark/local-comparison-new.json

uv run --no-sync python scripts/asr_benchmark.py failures \
  --manifest benchmarks/asr/section18/manifest.json \
  --report var/roma/asr-benchmark/indic-conformer-local-new.json \
  --export-dir var/roma/asr-benchmark/failures-indic-conformer-local-new

uv run --no-sync python scripts/save_benchmark.py \
  --report var/roma/asr-benchmark/indic-conformer-local-new.json \
  --run-key section18-indic-conformer-local-new --allow-database-write
```

Choose new report/run identities for a fresh measurement. Local Indic preflight requires the approved model/revision, offline Hub state and explicit reviewed-code opt-in, records full-snapshot observed hashes and rejects missing/extra/size-drifted artifacts. The snapshot digest identifies the measured local files; it is not independent publisher attestation. Resume also requires identical provenance, so an observed same-size file change cannot silently reuse a prior checkpoint. The comparison checks manifest, normalized scoring and per-case audio/reference identity; unsupported timings are not presented as inference latency. Warm timing excludes the first successful case from each process on both sides; it is not a representative p95 or live voice metric. See the [Level 4 result](levels/level-04.md#local-indicconformer-versus-whisper-base--2026-10-21).

## Local single-pass code-mix benchmark

Current local development ASR is **Whisper Turbo MLX 4-bit**. After explicit owner approval, mixed routes use at most two complete contiguous speech chunks in one encoder batch; all PCM is retained. Context preserves Gujarati/Hindi script unless English evidence is strong. Pure gu/hi/en decoding stays unchanged. No second ASR model or retry is added. The new run recovers English suffixes but fails native/noisy accuracy and latency gates. `single_pass_local`, `adaptive_local` and whole-turn `whisper_turbo_local` are historical report identities; their executable selections are retired and reports remain importable.

```bash
# Installed optional runtime on this Apple Silicon Mac; --inexact preserves other extras.
uv sync --inexact --locked --extra dev --extra telephony --extra workers --extra voice-lab --extra mlx-asr

# Offline, unchanged 15 public cases. Pick a fresh output file for a new run.
HF_HUB_OFFLINE=1 uv run --no-sync python scripts/asr_benchmark.py run \
  --provider whisper_turbo_batch_local \
  --local-model-dir var/roma/models/whisper-large-v3-turbo-mlx-4bit \
  --manifest benchmarks/asr/section18/manifest.json \
  --audio-dir var/roma/asr-public \
  --report var/roma/asr-benchmark/turbo-batch-new.json

uv run --no-sync python scripts/asr_benchmark.py failures \
  --report var/roma/asr-benchmark/turbo-batch-new.json \
  --manifest benchmarks/asr/section18/manifest.json \
  --audio-dir var/roma/asr-public \
  --export-dir var/roma/asr-benchmark/failures-turbo-batch-new

uv run --no-sync python scripts/save_benchmark.py \
  --report var/roma/asr-benchmark/turbo-batch-new.json \
  --run-key section18-whisper-turbo-batch-new --allow-database-write
```

The local decoder requires `HF_HUB_OFFLINE=1` and verifies the exact [artifact manifest](../benchmarks/asr/whisper-turbo-mlx-artifacts.json) before loading. Its installed three-file directory is 464,280,053 bytes. `mlx-whisper` 0.4.3 expects `weights.safetensors`; the upstream `model.safetensors` was renamed without changing bytes. A new machine must provision that exact revision/layout before use; inference never downloads weights. Linux/Intel hosts retain mocks or explicit base comparison until a separately measured supported runtime is chosen; do not install MLX there.

For a separately authorized vocabulary comparison, add `--booking-vocabulary` to the local Turbo benchmark command and select a fresh report path/run key. This opts into the fixed reviewed Weltec/course/branch nouns and a 410-token output cap; it records `whisper-turbo-vocabulary-v1` and metadata-only prompt provenance. Other benchmark providers reject the flag. The registry's `WHISPER_BOOKING_VOCABULARY` defaults false; enabling it applies the same fixed policy to single and batched local decoding. There is no freeform prompt setting and no caller/booking data is inserted. Historical reports stay unchanged. Offline contract checks do not demonstrate recognition improvement; [Level 4](levels/level-04.md#opt-in-reviewed-vocabulary--2026-10-25) records the unmeasured quality/latency limits.

For an explicitly approved fresh-machine provisioning only (not needed on this already-provisioned Mac):

```bash
hf download mlx-community/whisper-large-v3-turbo-4bit \
  config.json model.safetensors multilingual.tiktoken \
  --revision 0f058d38170d183f9fdee07908f5b515d91793a8 \
  --local-dir var/roma/models/whisper-large-v3-turbo-mlx-4bit
mv var/roma/models/whisper-large-v3-turbo-mlx-4bit/model.safetensors \
  var/roma/models/whisper-large-v3-turbo-mlx-4bit/weights.safetensors
```

Do not rerun that download into the renamed installed directory; the extra source filename would intentionally fail exact-file-set verification. Restore into a fresh directory and validate it. Download authentication uses the HF CLI's private token environment/login; never put token values in commands, committed config or public-Space requests.

Choose fresh report/run keys; never overwrite committed evidence. Local benchmarking forbids `--allow-network`. References and scoring remain unchanged; no injected missing words or transliterated reference creates a pass. Review monolingual, emitted-loanword and full-sentence switching separately. Native dates such as `કાલે`/`कल` remain for context-aware date extraction. The [current evidence](levels/level-04.md#single-model-speech-batching--2026-10-21) reports actual accuracy, memory limits and rejected decode settings.

The default section 20 check stays `make voice-prototype`, with scripted ASR through this same one-provider wrapper (no actual model batching in mock mode) and no network/hardware/DB writes. A later authorized microphone run must explicitly select `STT_PROVIDER=whisper_mlx`; optional `WHISPER_MLX_MODEL_DIR` changes only the verified local path. `.env` and production defaults remain unchanged. Paid Claude, microphone/native review and the full Level 6 gate remain deferred.


## Explicit disposable voice booking database

```bash
make demo-db
# Copy the printed DATABASE_URL into this shell before a separately authorized lab run.
# Do not use the working roma database for a demo.
make demo-db-down
```

Requires installed native PostgreSQL (`initdb`, `pg_ctl`, `createdb`) and `APP_ENV=dev` or `test`. This command ignores the working `DATABASE_URL`, chooses a free loopback port and uses ignored `var/roma/demo-db`. It refuses an existing directory; stop/remove the owned demo first before rebuilding. Down discards its TEST ONLY data. No automatic startup seeding, model download or microphone action occurs. Canonical branch name is Vadodara; TEST ONLY institute/branch/course codes distinguish demo records. Slots are computed relative to now, within existing visiting hours.

Local prototype startup checks booking data before models/call creation/devices and reports `BOOKING-DATA-MISSING` if the selected branch has no usable future slots. Capture-deadline empty output is `CAPTURE-DEADLINE-NO-TRANSCRIPT`; entirely rejected batch output is `ASR-BATCH-REJECTED`. These extend the shared safe enum, not raw exception logging.

Prototype turn settings and the mock timing table are owned by [Level 6](levels/level-06.md#offline-output-and-demo-readiness--phase-3). `VOICE_TURN_BUDGET_MS` is unset by default; configuring it reports overruns without introducing an acceptance gate. Paid Claude/microphone/native testing remains deferred.


## Manifest-v2 text report annotation (offline only)

First set `min_distinct_speakers` and `min_distinct_recordings` in [the corpus configuration](../configs/asr-corpus-v2.json); both default to null, so v2 evaluation refuses to proceed until the owner sets them. Use [the protocol](26-asr-recording-protocol.md) and [schema](../configs/asr-corpus-v2.schema.json) for metadata/permission requirements. This tooling grants no permission to collect audio.

For a separately authorized future corpus, supply a JSON report with a `cases` list containing one `recording_id` and `hypothesis` string per recording. Empty strings are valid recognition failures; rows explicitly marked `status: error` count as empty hypotheses and remain in the report. Optional reference/checksum fields and the report manifest hash must match the declared metadata. Duplicate, unknown or missing recording rows are rejected.

```bash
uv run --no-sync python scripts/asr_benchmark.py annotate \
  --manifest var/roma/asr-corpus-v2/manifest.json \
  --report var/roma/asr-corpus-v2/transcripts.json \
  --corpus-config configs/asr-corpus-v2.json \
  --output var/roma/asr-corpus-v2/scored.json
```

This command reads JSON text/metadata only. It never loads a model, opens audio, records, fetches or calls a provider. It writes a separate report and refuses to overwrite its inputs. V2 adds `per_speaker` WER/CER edit totals and `per_entity` exact normalized phrase recall (name/date/time/branch). Unicode normalization is shared with existing evaluation; semantic equivalents are not inferred. Failed recordings stay in denominators. V1 report fields remain unchanged, and the existing benchmark runner/section18 manifest are preserved. No new WER model result or gate pass is claimed by tooling tests.


## Structured logging (section 21)

The production composition and voice-prototype CLI call `configure_logging()`. Roma stdlib and configured Uvicorn records emit newline-delimited JSON on their handlers. Prototype instructions and final JSON reports remain stdout output. For an entirely offline example:

```bash
make --silent voice-prototype > "$TMPDIR/roma-prototype-report.json" 2> "$TMPDIR/roma-prototype-events.jsonl"
jq -c 'select(.event == "turn_completed" or .event == "turn_failed")' "$TMPDIR/roma-prototype-events.jsonl"
```

Use the `call_id` to follow all attempts, `correlation_id` for the prototype/request scope, and `request_id` plus `turn_id` for one attempt. HTTP/WebSocket accept headers return fresh server-generated request/correlation IDs. Incoming IDs are ignored; use these response IDs when reporting an error. Null call/turn/stage fields are normal outside a call. An ASR-rejected attempt has its own `turn_id` without advancing the conversation controller.

Check `event`, `level`, stage timings and the shared safe `error_code`; do not enable transcript/exception dumps to diagnose failures. Legacy messages are omitted and represented as `legacy_log`; add a reviewed event and explicit safe metadata when extending operational coverage. Do not use phone numbers, lead tokens, prompts, raw transcripts, slot values or paths as IDs. SDKs using independent sinks may require their own configuration; this increment does not claim that every third-party diagnostic is a Roma JSON event. [Level 6](levels/level-06.md#section-21--structured-logging) owns implementation details and limitations.


## Streaming and interruption (sections 22–23)

```bash
make --silent voice-prototype ARGS=--streaming
make --silent streaming-lab
```

Both commands above use mocks and make no paid API calls, open no microphone, download no models and write no working-database records. The first exercises all eight queue stages; the second runs four concurrent fake calls with deliberate CPU/TTS delays and fake WebSocket heartbeats. Plain `make voice-prototype` retains the existing sequential mode. Preserve real local mode's existing microphone/paid-API/DB opt-ins; `--streaming` grants none of them. Local capture now defaults to [full duplex](#phase-5c-local-full-duplex); `--half-duplex` is an explicit diagnostic override.

Watch `queue_summary` by call ID: capacity, current/peak depth, high-water hits, oldest/newest drops, discarded cancellation data, `put_wait_ms`, `get_wait_ms` and combined `wait_ms`. `STREAM-BACKPRESSURE` stops the turn with approved fallback or close; do not ignore a drop and continue speech. CPU worker and stage timings overlap and must not be summed as serial durations. `speech_to_first_audio_ms` is measured from the saved last-speech timestamp to the first software write; real acoustic latency still needs independent testing.

Transport adapters feed final audio through `StreamingInput`, optional interim text through `partial()`, and confirmed VAD evidence through `observe_speech()`. Call `disconnect()` for transport loss; it shares the cancellation/clear path and releases resources. Provide `clear`/`close` transport callbacks and `release_on_disconnect` where the transport owns them. `prime_filler()` prepares approved cached filler outside the hot path; `play_cached_filler()` rechecks safety and never synthesizes on a miss. No filler is played over active caller speech automatically. See [Level 7](levels/level-07.md#sections-2223-implementation) for defaults, DB event semantics and pending advanced/native gates.

## Redis state and milestone recovery (section 24)

Existing app/prototype commands now compose leased Redis state and supervised SQL milestones; there is no new service, migration or automatic data seed. The local prototype retains all existing microphone/paid-API/database opt-ins. Do not run a live call to verify this increment. Reproduce isolated recovery with `uv run pytest -q tests/services/test_live_call_state.py tests/repositories/redis/test_call_lease.py tests/repositories/postgres/test_live_call_state.py`; repository fixtures start disposable services.

Observe `checkpoint_failed`/`state_cache_failed` plus numeric `checkpoint.pending` and `checkpoint.lag_ms`. SQL failure blocks the milestone; Redis publication failure leaves SQL authoritative. Restore uses SQL schema/policy/retention rules and repairs missing/stale hot keys. Do not delete another worker's lease or restart an expired owner; allow a new store to claim after expiry. Default lease is 360 s with no renewal, so worker crashes may delay admission until expiry. Ownership/SQL failure refuses fresh-state continuation. Keep acknowledged milestone loss at zero within the SQL durability policy; in-progress work may require the caller to repeat it. See [Level 7](levels/level-07.md#section-24-live-state-and-supervised-checkpoints) for timeout/retry/cache limits and pending advanced replay/native gates. No running Redis configuration or working DB change is needed.

## Provider reliability (section 25)

Existing commands/settings remain. Diagnose reviewed provider failure kinds and stage codes; raw upstream response bodies must not enter logs. A transient Claude outage retries only before output and within the inherited turn deadline; repeated failures open the per-adapter circuit for 15 s, then one probe is allowed. Do not retry a partial spoken answer or an ambiguous Twilio dial. Native model/hash cancellation retains capacity until actual work finishes. Reproduce offline with `uv run pytest -q tests/providers/test_reliability.py tests/providers/ai/test_claude_llm.py tests/realtime/test_provider_llm.py`; full evidence is in [Level 7](levels/level-07.md#section-25-provider-reliability). No paid/microphone test is required for this command.

## Background job extensions (section 26)

Existing recording/dispatcher/worker commands remain. Keep one recording consumer. Default summaries are deterministic metadata; summary jobs route to the lower-priority `roma_background_summary` queue. Later explicit paid opt-in is `uv run --extra telephony --extra workers python scripts/run_background_worker.py --allow-paid-summary`; do not run it during deferred paid testing. To isolate worker resources further, use Dramatiq's `--queues`/process settings for separate normal/summary workers; no preemption or live-call latency guarantee is claimed.

Inspect `followup_jobs.status`, attempts, last_error, available_at and lease owner/expiry using the existing SQL inspection query. Missing consent/sender and exhausted failures remain dead_letter with reviewed reasons; scheduling is not delivery. Configure an external sender only after contact policy and provider idempotency are approved. No concrete sender/CRM transport or Message Batches adapter is installed. [Level 8](levels/level-08.md#section-26-background-processing) owns the exact summary/token/spend and retry bounds.

## Webhook processing windows (section 27)

Production composition supplies `RedisWebhookWindow` to `WebhookService`; signed/account validation still runs first. Inspect only opaque key/TTL/status diagnostics—never provider form/phone/token payloads in logs. Processing lease60 s can cause503 while another handler owns the same event; make retry policy an explicit provider deployment task. Done marker86,400 s still requires SQL verification. SQL receipts have independent retention; do not purge them to clear a transient cache lock. Compare-owner release/expiry is safer than deleting another worker's marker. Test using `uv run pytest -q tests/services/test_webhook_window_service.py tests/repositories/redis/test_webhook_window.py tests/repositories/postgres/test_webhook_window_sql.py`; fixtures use disposable services. No working-DB or carrier change is required.

## Section 28 human authentication setup

Authentication fails closed until configured. Review the target database and backup/rollback plan, then apply `make db-upgrade` explicitly; this session applied the new revision only to disposable databases. Never stamp head to avoid an upgrade. Set `AUTH_JWT_SECRET` to an independently generated private random value of at least 32 bytes in `.env` or your secret manager. Keep it out of terminal logs, git and shared examples. Key replacement invalidates existing access tokens; plan re-login and family revocation rather than treating it as transparent rotation.

Defaults: `AUTH_JWT_ISSUER=roma`, `AUTH_JWT_AUDIENCE=roma-api`, `AUTH_ACCESS_TTL_SECS=900`, `AUTH_REFRESH_TTL_SECS=604800`, `AUTH_RATE_LIMIT=10`, `AUTH_RATE_WINDOW_SECS=300`, `AUTH_ALLOW_LEGACY_API_TOKEN=false`. Ensure private Redis is available for login/refresh rate limiting. Credentials are sent only over HTTPS outside local development. Do not trust forwarded peer headers without a separately reviewed proxy design.

Provision the first Admin explicitly after migration; the script prompts twice without echo and prints no password/token:

```bash
uv run python scripts/bootstrap_user.py --email teacher@example.invalid --display-name Teacher --role admin
```

Use an approved real operator identity instead of the placeholder. Passwords require 12–256 characters, at most 1,024 UTF-8 bytes. `--role` may repeat for known roles; existing identities are not overwritten. Automated provisioning can explicitly select `--password-stdin`; never put passwords in CLI arguments. No provisioning or migration runs in AuthService startup.

Login with `POST /api/v1/auth/login` JSON `{email, password}`. Keep returned access/refresh values private; send `Authorization: Bearer <access_token>` to resources. Rotate with `POST /api/v1/auth/refresh` JSON `{refresh_token}` and replace both stored credentials atomically. Reuse revokes the family, including a racing successor. Logout sends the refresh credential to `/api/v1/auth/logout`; subsequent access is denied. Admins manage accounts via `/api/v1/users` and `/api/v1/users/{user_id}`. Role/activity changes are checked live. No browser storage/client implementation is supplied.

The legacy machine bearer is available only with explicit `AUTH_ALLOW_LEGACY_API_TOKEN=true`; it cannot administer users or access future knowledge/safety-management permissions. Keep it off for human access. Signed carrier routes are independent. [Permissions](levels/level-08.md#section-28-authentication-and-role-permissions) include Viewer recording URL redaction; full recording access audit/privacy remains later work. No paid/microphone/native acceptance was resumed here.

## Callback and stream security (section 29)

Deploy the dialer and answer/media handlers together: outbound callbacks now require Roma's server-issued expiring intent, and Media Stream custom parameters carry a fresh signed capability. Do not manually copy old answer URLs or remove intent/capability checks to work around an expiry. Keep proxy paths and PUBLIC_BASE_URL consistent with the exact URL used by Twilio signature validation. The existing private Twilio authentication secret derives a purpose-specific capability key; no new default secret is generated.

Use offline capability, answer-idempotency and media-auth tests before deployment. Forged/expired/call-mismatched capabilities must fail before constructing speech providers. Redis failure denies stream admission; inspect sanitized security_rejected events and Redis availability, without printing tokens, signed forms or full URLs. Provider-supported signing semantics are documented by [Twilio request security](https://www.twilio.com/docs/usage/security) and [Media Streams authentication](https://www.twilio.com/docs/global-infrastructure/firewall-configurations/media-streams-configuration). Twilio signatures have no timestamp; inbound replay freshness is not claimed. SQL receipts prevent duplicate durable effects; the 600-second stream admission marker does not replace them.

## Shared rate limiting (section 30)

The production app uses the existing private Redis connection and a purpose-derived HMAC key from PII_HASH_KEY. Configure a strong private PII hash key; do not use shared development examples. The reviewed budgets are in [Level 8](levels/level-08.md#section-30-shared-request-limits). Keep AUTH_RATE_LIMIT/AUTH_RATE_WINDOW_SECS consistent with the intended additional auth-service policy; defaults are 10/300. Request middleware enforces its published perimeter policies independently, including /ws before acceptance; rejected WebSocket admission closes with 1013.

429 means wait for Retry-After; 503 means admission is unavailable. Do not bypass Redis or retry a dial blindly. Observe metadata-only rate_limited events, Redis TTLs and request/correlation IDs. User identity comes from verified live authentication; forwarded peer headers are ignored. Review deployment proxy handling and NAT/callback traffic before setting production capacity. No new expensive HTTP endpoint, paid request or GPU test is started by installation.

## Privacy setup and managed operations (section 31)

Review the intended database and backup/rollback procedure before explicitly applying migration 20261024_0005 or later. Keep PII_HASH_KEY, PII_ENCRYPTION_KEY and RECORDING_URL_SIGNING_KEY private and distinct. Selected field encryption is off until PII_ENCRYPTION_ENABLED=true and a valid 32-byte hexadecimal key is provided. Recording URL signing requires its independent 32-byte hexadecimal key; no default key is invented. Store keys in the operator's private environment/secret manager. Do not print them, commit them, or remove the old encryption key before an explicit re-encryption/restore plan is verified.

PII activation is configured in API, prototype and worker processes. It does not encrypt old rows automatically. The explicit CLI previews by default and never migrates a database or generates keys:

```bash
uv run --no-sync python scripts/privacy.py --call-id <approved-call-uuid>
uv run --no-sync python scripts/privacy.py --call-id <approved-call-uuid> --action anonymize
uv run --no-sync python scripts/privacy.py --expired --limit 100
uv run --no-sync python scripts/privacy.py --backfill-encryption --limit 100
```

Run only against the explicitly chosen DATABASE_URL. Add --apply only after reviewing the preview and approved request/policy. Backfill apply additionally requires encryption activation and its existing private key; repeat bounded batches until no plaintext rows remain. CLI output contains counts/status, not private field values. No such apply command ran during implementation.

The human API uses POST /api/v1/recordings/{recording_id}/access, then authenticated GET of its short-lived returned content URL. A link is not an independent bearer grant: the same current human identity/recording permission is required. POST /api/v1/privacy/calls/{call_id} requires Admin and defaults to apply=false. Audit failures deny output; failed cleanup remains pending and can be retried. Review the tombstone alongside Redis/file cleanup, and reapply tombstones before restored backups serve traffic. Use the external_pending fields to track backups, exports and unmapped TTL caches; do not call managed cleanup complete proof of global erasure.

PRIVACY_RETENTION_DAYS stays unset until the institute approves policy. An explicit stored retention expiry or approved configured policy drives selection; legacy null expiries require inventory. Do not enable capture without approved disclosure plus deterministic call-bound evidence. Signed recording access does not approve consent wording or retention. Confirm Anthropic account/model/feature retention terms and legal duties using [the source review](07-security.md#claude-api-retention-review--2026-10-24) before private caller-text/live acceptance.

## Knowledge governance (sections 32–33)

Apply the reviewed migration to the explicitly chosen environment before using governance APIs; implementation tests migrate disposable databases only. Configure human authentication/Admin accounts separately. POST /api/v1/knowledge/documents accepts bounded JSON text with source_key, source_reference, title, positive version, category, language, owner_id, optional course_id/branch_id, aware effective_from/optional effective_until and content. Categories are course, policy, branch and schedule; fees/salary/placement/discounts stay outside knowledge.

An Admin calls POST /api/v1/knowledge/documents/{id}/approve with approval_reference. Activation/archive use their respective POST suffix and expected_version. Approval actor/time/hash are server-owned; uploaded content cannot self-approve. Create a new version for changed evidence. Do not bypass approved-document SQL immutability guards, activate expired/future evidence, invent approval references or add unapproved course claims.

Sections 34–40 extend these endpoints with managed ingestion intent and explicit ingestion/status/search operations below. Upload does not run inference or download a model; the bounded SQL worker performs cache-only embeddings. Existing approved static facts and deterministic no-evidence deferral retain priority. Keep runtime retrieval off until its separate representative/Claude/native quality gate is accepted.

## Verified local operational configuration

The owner-authorized 2026-10-24 rollout completed isolated backup/restore, migration rollback/reupgrade, real-SQL auth/RBAC and synthetic selected-field encryption before touching local working `roma`. Independent read-only verification on 2026-10-25 confirmed `20261024_0006`, 31 application tables, the approved Roma Local Admin, encryption enabled and no selected plaintext names/transcripts. Working `roma` contains no TEST ONLY booking reference data.

The generated login/password is in owner-only ignored `var/roma/ops-20261024/credentials/admin.json`; open it privately on this machine. Do not paste its contents into chat or commit it. JWT/AES/link secrets are loaded through private `.env`; retain the original field-encryption key for encrypted legacy/backups. Protected pre-upgrade encrypted backups and their separate key are under the same ignored operational directory. Their disk decryption/restore was verified; independent off-host backup/key escrow is not configured.

Preserve the unchanged PII hash key: rotating it without an explicit relinking migration breaks caller identity. Do not downgrade working data for teaching evidence. The isolated downgrade proof covered original `0003` rows; newer sessions, governance and disposal records need a compatible recovery plan.

Recording remains blocked by pending disclosure/consent policy. Retention remains unset. Review the [proposed policy and restore/erasure procedure](07-security.md#recommended-policy-before-production-acceptance) with the institute; private keys do not establish privacy/legal approval.

## Supervised pause/resume and deadline checks

After loading the isolated SQL/Redis/data-root and the existing cache-only local speech settings, append `--pause-after-turn 1` to the explicit local prototype command. Wait for the flushed `CAPTURE_STARTED` cue; it follows successful input stream start. `CALL_PAUSED` means the unfinished call was checkpointed and ownership released. Resume with the same explicit local opt-ins plus `--resume-call <voice-lab-SID>`; remove the pause flag unless pausing again. No resume is automatic.

Resume retains the original five-minute SQL start, controller turns and attempt numbering, including downtime and model readiness. Terminal, expired, deleted or SQL/local-tombstoned calls are rejected. A confirmed appointment is reconciled from SQL and emits `BOOKING_RESTORED` without another capture/commit. Wall and monotonic deadline checks reject stale input before ASR/controller and late speaker writes after sleep; stage latency remains monotonic. The current local default is [full duplex](#phase-5c-local-full-duplex), with `--half-duplex` available for diagnosis.

The 2026-10-25 acceptance follow-up retained INR6 of the INR50 aggregate ceiling as conservative reservations across three invocations; reservations are not claimed invoice costs. The private ledger was preserved at `var/roma/ops-20261024/acceptance-session-reservations.jsonl` before the owned TEST ONLY services/runtime were removed. Reuse that aggregate before any further paid run. The owner rated naturalness 2.5/5 and found no 8 kHz/16 kHz improvement. Full multilingual/booking/native/reliability acceptance is unfinished; see [Level 6 evidence](levels/level-06.md#supervised-acceptance-follow-up--2026-10-25).


## Phase 5A offline audio replay

Run `uv run --no-sync python scripts/adaptive_audio_lab.py` for the fixed-seed synthetic report; no services, models, microphone, paid API or database are needed. Compare the [saved evidence](../benchmarks/voice/phase5a-adaptive-replay.json). Private replay payloads require consent/access/retention approval. The optional Redis journal accepts unique `voice-lab-` UUID identities and strict metadata/controls, expires without renewal and fails explicitly at capacity; test with disposable Redis. It is not enabled in production or the microphone CLI and does not replay booking effects.

Local input opens once after preflight/readiness and before the first `CAPTURE_STARTED`, then closes at cleanup. Phase 5C keeps it active during synthesis/playback; only `--half-duplex` selects the earlier software-muted behavior. `DEVICE-CAPTURE-STALLED` means callbacks stopped during active capture. Use an explicitly supervised diagnostic to inspect device health; never silently restart or concatenate incomplete audio. Native callback/interruption acceptance remains pending.


## Phase 5B local TTS lifecycle

The local prototype warms the active Hindi/female TTS voice before opening capture and registers provider cleanup before readiness. A retained worker is bounded and is killed/drained/reaped on timeout, failure, cancellation and shutdown. Do not create a worker per sentence after warmup. Local mode defaults to audited sentence streaming; `--buffered-speech` restores whole-response delivery for a diagnostic, independently of full-duplex input. Mock default remains offline and buffered unless `--streaming` is supplied.

[Native synthetic timing](../benchmarks/tts/phase5b-native-20261025.json) and private A/B WAVs under `var/roma/phase5b-tts-20261025` contain no participant recording. Generate future comparisons as new evidence runs; do not infer a listener rating. Phase 5C supersedes the previous microphone half-duplex default; physical acceptance remains separate.

## Phase 5C local full duplex

Install the locked optional native AEC runtime with `uv sync --locked --extra dev --extra telephony --extra workers --extra voice-lab --extra mlx-asr --extra duplex` when preparing a supervised local environment; this is dependency setup, not a model-download or microphone command. Existing MLX/TTS cache requirements still apply. Plain `make sync` does not install the optional `duplex` extra. Full-duplex preflight checks exact `pywebrtc-audio==0.2.0` availability before model/device readiness and refuses missing or failed AEC.

Local mode defaults to full duplex plus audited sentence streaming. Append `--half-duplex` to an otherwise authorized local command for software-muted diagnosis; append `--buffered-speech` for whole-response speech while retaining full-duplex input. Neither option changes the existing microphone, paid-API, database or model-cache opt-ins. This phase authorizes no new microphone/paid run or model download.

Run `uv run --no-sync python scripts/echo_lab.py` for deterministic voiced/noise probes with noise suppression off/on, generated PCM only and no devices, models or services. The report separates echo attenuation, double-talk energy/correlation, near-only preservation and DSP frame timing. It is not a speech intelligibility, physical latency or native listening result. Preserve native speaker/microphone echo-tail, false-interruption and near-end speech checks as pending acceptance.

## Known-resource provider erasure

This is a separately selected operator operation. Local `scripts/privacy.py` preview/apply remains unchanged and retains external-provider pending items. Do not run against real accounts as an automatic test or as a restore-drill side effect. Obtain authorization for the specific account/resources and supported provider action, prepare the known inventory independently and retain unknown archives/products/exports for manual resolution. This command does not enumerate an account or infer that a manifest lists every copy.

Prepare an owner-only JSON manifest (`chmod 600`) in ignored private runtime storage. Exact fields are `version: 1`, `account_sid` (AC + 32 hexadecimal characters), `call_sid` (CA + 32 hex), `recording_sids` (list of RE + 32 hex), and `transcriptions` (list of objects with only `sid` (TR + 32 hex) and `recording_sid` referencing a listed RE). Lists may be empty, but this states only what is known. Maximum 50 RE and 50 TR; unknown/duplicate fields, duplicate IDs, invalid relationships, nonterminal calls or unfinished resources fail closed. Do not add caller text, phone numbers, media URLs, credentials or approval documents. Example shape with identifiers to replace:

```json
{
  "version": 1,
  "account_sid": "AC11111111111111111111111111111111",
  "call_sid": "CA22222222222222222222222222222222",
  "recording_sids": ["RE33333333333333333333333333333333"],
  "transcriptions": [
    {"sid": "TR44444444444444444444444444444444", "recording_sid": "RE33333333333333333333333333333333"}
  ]
}
```

Use separately provisioned restricted API-key credentials, injected by the operator's secret mechanism through `ROMA_ERASURE_TWILIO_ACCOUNT_SID`, `ROMA_ERASURE_TWILIO_API_KEY_SID` and `ROMA_ERASURE_TWILIO_API_KEY_SECRET`. No secret belongs on the command line. The CLI never loads `.env`, application settings or `TWILIO_AUTH_TOKEN`; it cannot verify an SK key's restriction type. Confirm the key permits only the intended account's supported fetch/delete operations. Explicit `--account-sid` can override the erasure account environment variable; the manifest and SDK request account must match.

Default preview below makes zero requests, constructs no provider client and writes no runtime files. `--verify` deliberately makes read-only provider GETs, projects only metadata and returns `verified_manifest_digest` if every declared target belongs to the account/call/recording and is terminal. These are example commands, not activities performed during implementation:

```bash
uv run --no-sync python scripts/provider_erasure.py --manifest var/roma/privacy/provider-manifest.json
uv run --no-sync python scripts/provider_erasure.py --manifest var/roma/privacy/provider-manifest.json --verify
```

Review counts and the exact manifest before applying. Copy the verified digest into the explicit apply command, and choose a durable owner-only ledger directory outside any backup that could overwrite its evidence. The command creates missing directories as 700 and ledger/lock files as 600; existing permissive, symlinked, corrupted, account-mismatched or manifest-mismatched ledgers fail closed. Keep this ledger and the original manifest together under restricted access. On this Unix runtime, parent symlinks are also rejected; use canonical filesystem paths.

```bash
uv run --no-sync python scripts/provider_erasure.py \
  --manifest var/roma/privacy/provider-manifest.json \
  --apply --verified-digest <digest-returned-by-verify> \
  --ledger var/roma/privacy/provider-erasure/receipt.json
```

Apply re-fetches all targets before effects, writes/fsyncs minimized verified intent and an `attempting` state before each independent TR→RE→CA delete, and stops at the first unresolved step. Requests are bounded to 3 seconds and the aggregate service budget to 30 seconds; neither SDK nor locked aiohttp retries automatically and redirects are disabled. Rerun the identical apply command with the same ledger after a transient timeout or cancellation. A lost successful response is resolved by the matching ledger plus account-scoped 404; a first-run 404 with no prior verified ledger remains `ownership_unverified` and needs manual review. Do not replace or edit a ledger to bypass this check. Missing/denied restricted credentials produce `authentication_manual`; resolve access manually rather than using the dial token. A failed fsync or concurrent owner produces `ledger_or_configuration_pending`, with no subsequent DELETE. Preserve pending/attempting receipts after interruption.

Exit 0 means preview succeeded, known resources verified, or the requested known-resource deletes were acknowledged/absent. It never means full physical erasure. `manual_pending` and `physical_erasure_complete: false` remain even after `known_resources_absent`. Record provider-specific confirmations separately under approved access: unknown Twilio inventory/archives/other products, Anthropic organization + configured model + features + agreement/retention, Sarvam supported rights process/retention, replicas/backups/exports. No unsupported Anthropic or Sarvam DELETE is sent, and no account tier implies ZDR or legal approval. No actual provider erasure/account verification was performed to produce the offline evidence.

Offline checks (no provider access):

```bash
uv run --no-sync pytest -q -p no:cacheprovider \
  tests/privacy/test_provider_erasure.py \
  tests/privacy/test_provider_erasure_cli.py \
  tests/privacy/test_twilio_erasure_adapter.py
```

## Phase 6 guided live acceptance

Run from the Roma repository:

```bash
make acceptance-live ARGS="--check"
make acceptance-live
```

The first command verifies cached pinned ASR/TTS artifacts, local AEC and configuration without microphone, model inference or a paid request. The second asks for an explicit ready indication, then creates and verifies its own TEST ONLY PostgreSQL/Redis, warms the existing models, and opens the full-duplex microphone/speaker. Gujarati input and the pseudonymous listener label `Owner` are defaults; use `ARGS="--language hi --listener AB"` or device-index arguments as needed. Replies remain Hindi-base Hinglish. Preparation precedes the existing five-minute call ceiling; the guided checks may need extra utterances for corrected readback and explicit booking confirmation. The lab pre-fills fictional discovery and starts at CLOSE; it does not prove a full discovery journey.

Ask for Vadodara visit options, try one short `haan`/`okay` during speech, then change the time mid-response to tomorrow 5pm. The demo has tomorrow's 11am/3pm/5pm slots. If needed, first request 11am to elicit a readback, then interrupt it with 5pm. Confirm only the correctly spoken final time. A short acknowledgement causing a cutoff is a failed observation, not permission to mark interruption handling passed. After normal completion, booking is independently queried and restored from SQL. If the run fails before verification, booking values remain null with `booking_verified: false`; this does not establish that no commit occurred.

Capture loss discards the incomplete utterance and pending stale work, advances the turn epoch, clears output and asks for the full request again. Cleanup temporarily mutes the input buffer, then reactivates the same native input stream. At most three recoveries are allowed within the original call/capture deadline; a fourth loss or failed cleanup ends the run. These are bounded discard/retry semantics, not lossless capture or replay. A `capture_recovered` event records software recovery, not successful recognition or acoustic interruption.

Before a launch, the operator ledger `var/roma/ops-20261024/acceptance-session-reservations.jsonl` is exclusively locked and receives a durable INR2 reservation. The existing INR6 historical reservations count toward one INR50 ceiling; malformed, missing, incomplete or exhausted history refuses a launch. Reservations survive ambiguity/interruption and are not invoice costs. The Claude adapter is separately capped at INR2 with no automatic retries. The runner does not edit private `.env`, reuse working `roma`/Redis, switch/download models, dial phones or run vendor deletion.

After microphone cleanup, enter actual naturalness/correctness scores (1–5; `skip` is allowed), notes without personal details, and the observed backchannel/interruption outcomes. Owner-only receipts and a separate HMAC key live under ignored `var/acceptance/`: `session.json`, `physical.json` (including turn-budget timings/events), and `voice_rating.json` when supplied. Source commit, current Python source-tree fingerprint and dirty-state flag distinguish uncommitted test source from the base commit. HMAC verifies local integrity, not identity, legal authority or independent measurement. No caller audio/transcripts are retained by this runner. Software claim-to-clear/write timing is explicitly **not** acoustic stop/first-audible latency; these fields remain null without independent measurement. One short run cannot pass representative corpus, native multilingual, concurrency or full production gates.

To add a later actual listening rating or regenerate current observation blocks from verified receipts without another call:

```bash
make acceptance-live ARGS="--record-rating --listener Owner"
make acceptance-live ARGS="--update-readiness"
```

Only bounded `Current live acceptance` blocks in Level 6/7/8 briefs are replaced. Historical scores and unchecked full gates remain intact. Missing/tampered receipts fail closed. A valid rating from another run/source stays private and is omitted from current readiness until a matching actual rating is supplied. `--no-rating` lets a supervisor collect the actual score separately; `--yes-ready` is for an already received explicit ready indication, not unattended testing.

The 2026-10-26 supervised follow-up produced two played turns before its fourth capture loss ended the run. The owner supplied **4/5 naturalness**; correctness, backchannel preservation and interruption success were not scored. No SQL booking outcome was verified. Software first-audio timings were **7.95/7.07 s**, with reported TTS timings **600/13 ms** and one cache hit; acoustic latency remains unmeasured. The earlier **2.5/5** listening result remains historical evidence and native/full gates stay open. A separate supervised probe temporarily raised the weak system input level from 15% to 80%, returned a 26-character ASR result, then restored and verified 15%. See [verification](12-verification.md#phase-6-guided-acceptance--2026-10-26) for source identity and evidence limits.

Governance import is a separate offline operation:

```bash
make acceptance-live ARGS="--governance /absolute/private/confirmed-governance.json"
```

The owner-only 600 JSON file must contain exactly `approver`, `approved_at` (past timezone-aware ISO timestamp), `policy_reference`, `audio_retention_days`, `transcript_retention_days`, `anthropic`, `erasure`. Anthropic fields are `organization_reference`, `model` (configured Haiku), `features` (actual active features), `retention_terms`, `terms_reference`. Erasure must carry an actual known-resource `outcome: known_resources_absent`, `physical_erasure_complete: false` and the remaining nonempty `manual_pending` scope from the separately authorized provider operation; raw provider details are projected away. Placeholders, previews, wrong models and fabricated whole-erasure claims are rejected. Retain the original approval/account/provider evidence independently; local signing does not establish authenticity. This importer activates no recording/retention setting and sends no external request. The existing deletion CLI uses explicit `--apply`, not `--execute`; specific account/resource authorization and its verified digest/ledger are still required. Institute/legal/account terms, approved disclosure audio and broader erasure cannot be supplied by a recommended default.

## Governed ingestion, retrieval and golden evaluation (sections 34–40)

Use a disposable, explicitly selected PostgreSQL database for rehearsal. Install pgvector in the server's extension directory first, review `20261026_0007`, then migrate only that chosen database. Working local `roma` remains at its previously approved `0006`; existing `make app-start` automatically migrates its configured database, so do not invoke it against working data for this rehearsal. Downgrading `0007` deletes ingestion/vector metadata; it deliberately leaves a possibly shared `vector` extension installed.

The locked optional runtime is `uv sync --locked --extra embeddings --extra dev --extra workers --extra telephony`. This installs libraries, not model artifacts. Owner authorization permits the pinned E5 snapshot if RAM fits; runtime never downloads it. Keep reviewed artifacts and manifest in ignored `var/roma/models/multilingual-e5-small/`. The manifest schema is `roma-embedding-manifest-v1`, model `intfloat/multilingual-e5-small`, revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, dimension 384, preprocessing `e5-prefix-mean-l2-v1` and `files` mapping approved artifact names to SHA-256 hashes. Required files are config.json, tokenizer.json, tokenizer_config.json and model.safetensors; symlinks, mismatched identities/hashes and oversized artifacts fail closed. No remote code or cloud embedding fallback exists.

For an explicitly selected lab process, provide `EMBEDDING_PROVIDER=local_multilingual`, `EMBEDDING_MANIFEST` pointing to that manifest, `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` through process environment without editing private `.env`. Bounds default to 8,192 UTF-8 bytes/text, four texts/batch, two CPU threads and a ten-second embedding timeout. A timed-out/cancelled native worker retains admission until completion; do not start overlapping workers to bypass busy refusal. Isolated RAM was about 807 MB on the 8 GiB host; measure simultaneous voice-stack headroom before accepted voice use.

Admin workflow after authentication and chosen-environment migration:

1. `POST /api/v1/knowledge/documents` with reviewed bounded text/Markdown and complete governance/scope metadata. Managed upload returns ingestion job identity/status; a duplicate pipeline request reuses the durable job.
2. Drain pending SQL jobs explicitly against that same chosen database and manifest:

   ```bash
   uv run --no-sync python -m roma.workers.knowledge --drain --allow-database-write --limit 100
   ```

3. Inspect `GET /api/v1/knowledge/documents/{id}/ingestion-jobs` for status, attempts, safe reason and quarantine flag. `POST /api/v1/knowledge/documents/{id}/ingest` with `{}` explicitly enqueues/reuses the current content/pipeline intent. Drain again when delayed retries become available; five attempts exhaust into dead letter. Processing leases expire after 300 seconds and stale owners are fenced. Fix rejected content in a new reviewed version; do not edit successful jobs, clear quarantine or forge completion.
4. Approve with a real `approval_reference`, then activate with `expected_version`. Approval may precede processing, but managed activation requires nonempty completed identity-consistent ingestion. This is a pipeline fence; a human must still approve the facts and semantic fitness. Archive revokes eligibility and is rechecked before answer release.

The explicit SQL drain is the available operator composition; the injectable Dramatiq actor is not a separately installed always-running worker. Each drain owns a one-connection pool. Include all process pools in the existing deployment connection budget. Inference does not hold a transaction; duplicate/crashed workers settle through SQL leases and canonical publication checks.

`POST /api/v1/knowledge/search` requires `rag.debug` (Admin or Counsellor), enabled configured retrieval and JSON query/category/language plus optional course_id, branch_id, version and allow_english. The response exposes chunk/document IDs, scores, filters and embedding/retrieval timings, never the raw query or document text. Missing scope, model identity, approval, completed ingestion or audit fails closed. No arbitrary upload URL, file execution or external fetch is supported.

Runtime defaults remain `RAG_ENABLED=false`, `RAG_TOP_K=5`, `RAG_MIN_SCORE=0.8`, `RAG_RETRIEVAL_TIMEOUT_SECS=0.45`, `RAG_GENERATION_TIMEOUT_SECS=10`. Enabling a chosen process makes cache-only model readiness precede caller admission and injects scoped answers into existing text/controller/prototype seams. A factual interruption reaches retrieval only after approved static lookup misses. Live replies remain Hindi-base Hinglish; unknown/ambiguous course/branch scopes, unsupported output script, stale/weak evidence, safety rejection, generation failure or audit failure use approved fallback. Do not enable accepted production traffic based solely on the synthetic benchmark.

The default benchmark uses mocks, synthetic documents and a deterministic fixture selector, with no services, models, devices or paid API:

```bash
make rag-benchmark ARGS="--mode mock --check"
```

For actual local embeddings/exact pgvector, create a fresh loopback database named with the required `roma_rag_benchmark_` prefix, migrate it through `0007`, and export its URL privately as `RAG_BENCHMARK_DATABASE_URL`. The benchmark refuses working `roma`, remote hosts, non-test names and nonempty governed fixture state. It never migrates or creates a database. Then run:

```bash
make rag-benchmark ARGS="--mode local --test-only --embedding-manifest var/roma/models/multilingual-e5-small/manifest.json --report var/roma/rag-benchmark/local-report.json --check"
```

Add `--persist` only to save minimized aggregate evidence into that same TEST ONLY database. The native run inserts synthetic governed documents/jobs/chunks and audit metadata; it calls no Claude. It calibrates on the declared 24 cases only, evaluates the separate 38 held-out cases, and atomically writes the report. `--no-calibrate` uses the declared 0.80 default; it is not a way to tune on held-out failures. `--check` enforces provisional mechanical targets and never claims the full quality gate. Preserve model/corpus/material fingerprints and failures with results; report retrieval warm/cold scope separately from complete answer latency. The checked-in [local report](../benchmarks/rag/local-v1-report.json) is mechanical inference evidence, with real Claude groundedness null and gate false. [Level 9](levels/level-09.md) owns measured outcomes and remaining acceptance.

## Local speech serving and telephony (sections 41–44)

This is an explicit operator path; final checks/native acceptance remain pending in [verification](12-verification.md#sections-4146-telephony-serving-telemetry-and-costs--2026-10-26). Preserve the pinned Whisper Turbo MLX 4-bit cache, Indic-TTS manifest/runtime/voice, Claude Haiku 5.5 and Hindi-base Hinglish. No model download, paid call, microphone or working database mutation is part of these commands.

Provision a separate owner-only private `SPEECH_SERVICE_KEY` (32–512 printable ASCII characters), never the API/JWT/carrier key. `SPEECH_SERVICE_URL` defaults to literal loopback `http://127.0.0.1:8091`; non-loopback/proxy/redirect URLs are rejected. Start one worker explicitly with already approved cached artifacts:

```bash
uv run --no-sync python -m roma.serving --allow-local-models
```

The command sets offline Hugging Face/Transformers flags and requires its private key before binding. Warmup loads the selected ASR and retained local TTS and discards the safe synthesis PCM. Authenticated `GET /health` reports liveness/readiness/inflight; `GET /ready` returns 200 only after warmup. Never put the key into a URL or tracked command/fixture. A missing key, runtime or cache fails closed. Keep the process private and shut it down gracefully after use; bounded shutdown may cancel pending async tasks, while underlying native cancellation retains its existing provider limits.

Defaults: timeout 30 s, warmup 180 s, shutdown 35 s, inflight 1 (maximum 2), body 3,000,000 bytes, audio 30 s and text 4,096 bytes. All are bounded settings in `.env.example`. The service accepts bounded `/v1/stt`, `/v1/stt/batch` (at most two serialized items) and `/v1/tts`. Busy work returns 429, cold/unavailable work 503, expired work 504 and invalid/oversized payloads 422/413. Client cancellation/deadline does not free native admission before native completion.

Only after the service is ready, explicitly select `VOICE_SPEECH_MODE=local_http` in the authorized process configuration and start the existing app. Default `legacy_cloud` preserves the baseline. App startup and per-call readiness fail closed when local speech is cold/unavailable. With RAG enabled, app startup also safety-checks and synthesizes the approved Hindi `NO_EVIDENCE_LINE` through the warmed HTTP TTS service into `PhraseCache` before callers arrive; failure blocks startup, and answer timeout later plays cached fixed PCM without new inference. Complete Claude JSON quote validation/revalidation/audit precedes controller punctuation chunking and final sentence safety/TTS. RAG still needs independent approved enabled configuration; recording still needs approved disclosure/retention plus explicit call consent and remains disabled. The local profile uses a call-owned `PlaybackWindow`: local TTS reserves before Pipecat enqueue, and a direct gate chunks/reserves other fixed raw assets before transport output. The window permits at most 0.4 s of unacknowledged PCM and a 0.25 s enqueue wait; these are source engineering bounds, not accepted physical stop/native-capacity SLOs. Installed `WindowedOutputTransport` acknowledges after software write/pacing, using a generation captured before the await so stale writes cannot refund a reset window. Controller interruption/cancel/end advances the shared turn epoch and resets the window before awaited base handling, including without RAG. Pressure remains latched and requests close/cancel. `VOICE_MEDIA_SEND_TIMEOUT_SECS` separately defaults to 1 s; per-message byte overflow or stalled ordered WebSocket output closes safely. The legacy cloud profile is unchanged. Do not interpret these limits as RTP loss tolerance.

Run the synthetic capacity harness without models/network spending:

```bash
uv run --no-sync python -m roma.serving.capacity --mode fake --requests 20 --concurrency 1 --operation mixed
```

For a separately authorized native service probe, explicitly select `--mode local --allow-local-models --server-pid <owned-server-pid>` and bounded request/concurrency values. Do not run it automatically as part of ordinary checks. Reports label evidence mode and all-attempt latency population, error/status counts, throughput, available process CPU/RSS and unavailable VRAM as null. Fake throughput, serialized HTTP batching and Apple unified memory do not prove GPU capacity. Account-specific `ANTHROPIC_ORG_RPM`, `ANTHROPIC_ORG_ITPM` and `ANTHROPIC_ORG_OTPM` remain optional verified inventory; existing per-adapter budgets/circuits still govern Claude.

## Metrics and usage/cost operations (sections 45–46)

`GET /metrics` serves no-store Prometheus text to actual socket loopback or a live active Admin JWT. Forwarded headers do not authorize callers. Protect this route at any reverse proxy, especially one connecting to Roma over loopback. Keep Prometheus/Grafana private. [The native lab guide](../configs/observability/README.md) owns exact existing-binary startup, loopback scrape/data source/provisioning paths, ignored TSDB/log state and Grafana private-password setup. It starts no service automatically. [The isolated native receipt](../benchmarks/telemetry/native-20261026.json) verifies pinned Prometheus 3.15.1/Grafana OSS 13.2.4 against synthetic metrics and a provisioned 10-panel dashboard; real-call observation and production acceptance remain separate. Use one API worker per scrape target because the registry is process-local; background-worker signals need their own targets. Set absolute `GF_PATHS_DATA`, `GF_PATHS_LOGS`, `GF_PATHS_PLUGINS` and `GF_PATHS_PROVISIONING` as shown in the native guide; `cfg:default.paths.*` does not override the INI paths. Keep the Admin password private and disable plugin preinstallation/automatic updates/key retrieval for the isolated lab. Optional OpenTelemetry requires the explicitly provisioned `observability` extra and `TRACING_ENABLED=true`; the default is false. Enabled app lifespan retains at most 256 reviewed span-metadata rows in memory through `app.state.recent_trace_spans`, clears them on shutdown and restores the previous tracer. It installs no global provider/network exporter and ignores exporter environment variables. Never add call IDs, phone numbers, transcripts, prompts or raw exceptions to metric labels/span attributes.

Protected `/api/v1/analytics/cost/today`, `/provider`, `/call/{call_id}` and `/average` require `analytics.view`. Use one optional UTC `day=YYYY-MM-DD` or `month=YYYY-MM`, with provider filtering on `/provider`; the call route covers its lifetime and populated turn groups. Inspect `unpriced_events`, `unknown_units`, `complete` and each currency separately before reading totals/averages. Empty reports remain incomplete. Local/carrier rates and GPU time are unknown until verified, not zero. The versioned direct-standard Haiku estimate is documented in [pricing provenance](16-observability-testing-and-delivery.md#section-46-normalized-usage-and-costs); historical records and conservative INR admission reservations remain unchanged.

After interrupted/failed writes, retry the same immutable usage identity; changed content must conflict rather than double-count. Recording write/close numeric diagnostics travel through the existing original-payload post-call handoff. This does not authorize capture, replay of private audio or retention changes. No sections 47–48/outcome supervisor feature is activated by these operations.


## Analytics, supervisor and evaluation — sections 47–53

Run the selected voice app through its existing operational path; this increment does not migrate working `roma`. `GET /api/v1/analytics/overview`, `/funnel` and `/branches` require `analytics.view` and optional `start_date`/`end_date` (exclusive) ISO dates, at most 366 days. Responses have `Cache-Control: no-store`; empty denominators and unknown latency/cost/branch attribution stay explicit. The funnel starts from call-start cohorts and requires ordered observed events plus SQL appointment confirmation, never final-stage inference.

Only an active Admin can fetch `/api/v1/supervisor/snapshot` or connect `/api/v1/supervisor/ws`. Use a same-origin WebSocket, then send a first frame containing only `{"token":"<short-lived Admin JWT>"}` within five seconds. Do not put tokens in URLs. One connection per Admin uses an owned 30-second Redis lease; auth/role/lease checks repeat every five seconds. Admission allows ten attempts per socket peer/minute, ignoring forwarded identities. On `resync_required`, fetch the authenticated SQL snapshot and reconcile internal call UUIDs; deduplicate live event IDs. Slow/offline clients disconnect instead of accumulating data. There is no durable Pub/Sub replay. The channel never sends caller text, names, phone numbers or carrier SID. The backend channel has no new visual dashboard.

Use an isolated locked environment for checks to preserve the existing speech environment:

```sh
UV_PROJECT_ENVIRONMENT=/tmp/roma-checks uv sync --locked --extra dev --extra backend --extra telephony --extra workers --extra observability --extra security
UV_PROJECT_ENVIRONMENT=/tmp/roma-checks uv run --no-sync python scripts/evaluate_project.py --run-offline --run-chaos --allow-disposable-services --output /tmp/roma-readiness.json
UV_PROJECT_ENVIRONMENT=/tmp/roma-checks uv run --no-sync python scripts/load_backend.py --native-services --requests-per-user 2 --output /tmp/roma-mock-load.json
UV_PROJECT_ENVIRONMENT=/tmp/roma-checks uv run --no-sync python scripts/check_production_policy.py
```

Native evaluation creates and cleans owned random-port PostgreSQL/Redis and never reads private settings. It requires installed native binaries/pgvector and existing tokenizer data. The mock load ladder is 1/10/25/50/100 sessions, with finite fixture-driven text calls and SQL connection/Redis probes; it does not exercise speech models, carrier sockets, durable booking/checkpoint load, GPU or paid Claude. Optional `--max-error-rate`, `--max-fallback-rate`, `--max-p95-ms`, `--max-stall-ms` and `--max-peak-rss-mb` stop further tiers. Unset limits are not accepted SLOs. `--require-full-gate` deliberately fails while representative native/human/governance evidence remains missing. See [Level 12](levels/level-12.md) for the actual report and limitations.

For the reviewed scanner, install the pinned checksum-verified official binary with `python scripts/install_gitleaks.py --destination /tmp/roma-tools/gitleaks`, then run `python scripts/scan_secrets.py --binary /tmp/roma-tools/gitleaks`. No scan output should expose secret values. Security CI also exports the hashed locked runtime/dev closure and runs `pip-audit`; it does not ignore advisories or download models. The explicit local target policy validates configuration only; it is not proof of live production traffic. The legacy-cloud rollout default stays unchanged.

## Full mock Docker stack — section 53

An image is an immutable packaged filesystem; a container is its running process; named volumes retain SQL/Redis/monitoring data across container replacement. Compose declares their private networks, runtime limits, ports and health dependencies. PostgreSQL/pgvector and Redis must be healthy before migration/API/worker startup. Application images contain code/dependencies, not speech weights, runtime audio or private `.env`.

With an installed running Docker engine and enough local disk/RAM:

```sh
python scripts/container_stack.py up
python scripts/container_stack.py smoke
python scripts/container_stack.py down
```

The wrapper uses the named `roma-stack-local` project, generates owner-only local keys under ignored `var/container-stack/local/`, builds the mock images, starts dependencies, migrates and checks API/worker/monitoring. It passes an empty Compose env file rather than loading the private project `.env`. API `http://127.0.0.1:18020`, Prometheus `http://127.0.0.1:19090` and Grafana `http://127.0.0.1:13000` bind to loopback. Grafana's generated private password is in the owner-only stack directory. Do not paste credentials into chat or track them. The datastore/worker network is internal; published services use a separate bridge for Docker Desktop forwarding. Metrics require their private scrape credential or a live active Admin outside trusted loopback.

`down` preserves named volumes and never creates replacement secrets. Startup refuses missing/partial keys or a lost local state directory when the named project has retained volumes; restore the original owner-only keyset before retrying. A complete legacy keyset is adopted without rotation. Keep approved encrypted backups of these keys separate from the retained data. Do not use `down -v` for normal operation. A separately approved backup/recovery policy is required before deleting volumes or changing schemas. For an already-built compatible previous application tag, use `python scripts/container_stack.py rollback --previous-tag TAG`; it does not pull an invented image or downgrade migrations. The recorded same-image tag rehearsal and later distinct compatible local-image rollback prove orchestration/persistence; real historical-release rollback and clean-second-host proof remain open.

GPU and native-Mac profiles explicitly fail closed: selected MLX ASR is Mac-specific and no approved Linux/NVIDIA backend or Mac serving bridge is packaged. A future compatible GPU profile needs NVIDIA host runtime and mounted approved weights. Do not switch models or claim live speech from mock stack success. [Level 13](levels/level-13.md) records the local rehearsal and remaining gate.


## Remediation investigation and isolated microphone speech

Keep the private `SPEECH_SERVICE_KEY` separate from JWT/API/carrier credentials. Start the existing cache-only service in its own terminal with the already approved cached model settings:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --no-sync python -m roma.serving --allow-local-models
```

The command deliberately loads/warms native models. It does not open a microphone, place a call or call Claude. Once authenticated `/ready` succeeds, select `--speech-transport local_http` on the existing prototype, or on a newly supervised acceptance invocation:

```bash
make acceptance-live ARGS="--check --speech-transport local_http --language gu"
```

This readiness check authenticates to the service and verifies TTS artifact identity. It does not record a native acceptance result. A subsequent actual run still needs the supervisor ready at the device, the existing aggregate INR50 budget ledger, TEST ONLY SQL/Redis and fictional details. Remove `--check` only for that supervised session. No runtime fallback follows from unavailable/busy service; the original `in_process` diagnostic remains explicit. Keep the server worker count at one and do not claim two-call capacity from the default one-inflight admission setting.

Physical receipts now include content-free lifetime capture health. A high callback interval with low queue pressure differs from a high queue watermark with normal callbacks; ADC/native overflow counters differ from application loss. Preserve the complete run's counters and stage timings before attributing loss to a driver, GIL or memory. Do not retain caller audio to debug it without approved recording consent.

Subject erasure extends the existing preview-first tool: `uv run --no-sync python scripts/privacy.py --caller-id <UUID>` previews persisted sibling calls; add `--apply` only for the intended approved subject. The Admin endpoint is `POST /api/v1/privacy/subjects/{caller_id}` with the existing `action`/`apply` fields. Active calls block disposal; 1,000 linked calls/history is the current operator bound. An anonymized caller row, appointment statuses and financial totals remain. SQL changes are atomic; managed file/Redis cleanup is retryable, and the same caller UUID can resume cleanup after links are removed. No wildcard erasure of other calls' holds, provider API execution, legal sign-off or whole-backup erasure is implied. Retain tombstone-ledger restore reapplication and review the returned `external_pending` scope.


### Offline acoustic timing analysis

The new analyzer reads an **already approved** stereo PCM16 WAV, never a microphone. Isolate the caller reference and speaker response on separate channels. Choose a quiet calibration window and a reference window enclosing the entire stimulus with silent guards. For example (thresholds and windows are calibration examples, not acceptance targets):

```bash
uv run --no-sync python scripts/measure_acoustic_ttfa.py --input /private/approved/loopback.wav --approved-input --provenance physical_loopback --reference-channel 0 --speaker-channel 1 --noise-window-ms 0 100 --reference-window-ms 200 700 --signal-rms 0.04 --max-noise-rms 0.005
```

Only scalar estimates are emitted. Noise, clipping, detectable overlap/bleed, ambiguous boundaries, unsupported files and non-isolated signals fail with fixed error codes. Energy thresholds cannot prove audibility, speaker identity, semantic answer onset or distinguish delayed echo from a reply. `physical_loopback` is the operator's provenance declaration; `physical_setup_verified` and `acceptance_gate_passed` stay false. Calibrate and independently review the physical setup before using measurements in an approved population/P50/P95 report. No caller recording permission is granted by the analyzer flag.

### Supervisor view

Open `/admin/supervisor` on the intended Roma app. The page shell contains no call data; sign in with the existing active Admin account. The page uses same-origin JWT-protected APIs and the existing supervisor WebSocket. Tokens stay in page memory, never URLs or local storage. Logout, revoked access and failed live sessions clear the display; reconnect by signing in for a fresh SQL snapshot. Historical UTC windows use existing SQL aggregates and observed ordered milestones. Unknown latency, pricing, historical stages and branch attribution remain explicit, and currencies remain separate. This is a read-only operator view, not a native voice/operational acceptance receipt.


The 2026-10-28 callback-output repair keeps one lazy-open speaker stream for the call. Normal interruption clears the bounded PCM queue and waits for a new-generation callback; it no longer runs concurrent blocking output write/abort. Report `playback_active` separately from `output_pending` (queued PCM or estimated already-handed DAC tail), and retain actual callback-based first-write timing. A 600 ms sustained-speech decision precedes clear. The 100 ms guard bounds only the callback-acknowledgement wait; subsequent reference/DSP draining and native driver teardown can still block. Neither timing proves <50 ms acoustic interruption. Callback stalls, starvation and native loss remain explicit failures. Device-level changes used in a supervised experiment must be recorded and restored, even if the native child crashes. Never change the existing INR50 ledger or infer a successful booking from playback alone.


For clock diagnosis under the **2026-10-29 policy**, preserve `capture_adc_subframe_uncertainty_frames` / `capture_adc_subframe_uncertainty_max_ms` and `capture_adc_fullframe_uncertainty_frames` / `capture_adc_fullframe_uncertainty_max_ms` separately from actual native/application loss. ADC/current/performance timestamp disagreement, including at least 20 ms, does not discard complete delivered PCM or create an overflow. Native input overflow, invalid PCM, bounded-ring capacity loss and callback contention still fail explicitly. Small-error alignment calibration remains capped at 50 μs per callback. Earlier dated reports retain their original policies and outcomes. Output reference uses one first-handoff DAC-to-monotonic anchor and every native 20 ms callback, including silence. `output_reference_clock_uncertainty_frames` / `output_reference_clock_uncertainty_max_ms` describe raw wall-clock estimate differences; they do not retime the PCM or prove physical alignment. Independently measure the microphone/speaker alignment and audible stop; sample counting does not prove a shared hardware clock or acoustic AEC alignment, and these counters cannot exclude an unflagged hardware fault.


### Strict booking and native-script vocabulary follow-up — 2026-10-29

`uv run --no-sync python scripts/acceptance_live.py --language hi --native-booking-vocabulary` explicitly selects the fixed native-script booking vocabulary; use `gu` or `en` for the other input routes. Microphone and paid acceptance still require operator readiness and the existing shared INR50 ledger; never reset it for a retry. This option is experimental, defaults false and does not alter the private `.env`. Hindi/Gujarati hints contain only fixed booking nouns/periods/negation, never a consent answer or a selected time. English keeps its existing route. The Gujarati native run did not establish an accuracy benefit; do not enable the option by default.

Only complete positive literal Hindi/Gujarati tomorrow/period/hour booking requests can skip extraction. Unsupported wording, negation, questions, alternative times and branch mismatches use the existing extraction path; availability, readback, explicit consent and the SQL transaction remain authoritative. Clear confirmation spelling variants use finite exact phrases after a selected readback. Unknown or corrupted affirmative speech requires clarification, and `हाई` remains rejected. A spoken readback alone is never proof of booking; inspect both SQL commit and independent restore.
