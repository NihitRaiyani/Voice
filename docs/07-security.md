# Security, privacy and production gates

## Existing trust boundaries

Validate `X-Twilio-Signature` against the exact configured external HTTP/WebSocket URL and verify start-event Account SID before constructing media. Human/client bearer authentication is distinct from carrier signature validation and worker access. Malformed/non-ASCII bearer credentials fail authentication with 401 before any resource/provider action. The current service has no frontend. Section 28 adds SQL-backed JWT sessions and role checks; the old static bearer is an explicit compatibility option, disabled by default. See [the implemented access contract](levels/level-08.md#section-28-authentication-and-role-permissions).

Keep secrets in environment-backed secret settings, hide SQL parameters and redact errors/logs. Full phone numbers, transcripts and authorization tokens do not belong in ordinary logs, metrics or fixtures. `var/roma`, Redis dumps and credentials remain ignored and access-controlled.

Stream metadata carries the lead token via TwiML `<Parameter>` and `start.customParameters`, not the Stream URL. **The current outbound answer callback URL does contain `?lead=...`.** Treat it as sensitive authorization data, suppress/redact proxy/access-log URLs and evaluate that exposure at L8; do not claim tokens never appear in any URL.

## Dial safeguards and limits

The current API validates Indian mobiles, calling window (09:00–21:00 IST), configured denylist, Claude spend, public reachability and hourly cap (default 20), with bearer auth first. An empty denylist blocks nothing; no real DND/DLT feed is implied. `ANTHROPIC_BUDGET_INR` defaults to ₹200 as a configured ceiling, not remaining provider credit; Sarvam readiness/credits are not covered by it.

Owner-approved contact/recording policy is required before real lead/production traffic. These code defaults do not establish legal clearance. Pending recording wording/audio causes capture discard rather than retained storage.

## Level 8 access and remaining security work

Section 28 implements user identity, issuer/audience-validated JWT expiry, rotating refresh-family revocation and deny-by-default roles. Operational secret/account setup and institute approval remain explicit. Recording access auditing, complete privacy/deletion and callback freshness are still later work. Keep callback signing separate and define replay behavior using actual provider capabilities; Twilio signatures alone do not impose a timestamp freshness window.

Current `/answer` already has atomic durable receipt/call/event acceptance. Extend identity/lease/completion semantics only to required callbacks. A processing claim cannot mark an effect completed before its transaction commits. Signature verification and idempotency solve distinct problems.

Use shared rate limits where deployment requires them. Encrypt recoverable sensitive fields; use keyed phone hashing for identity matching rather than enumerable plain hashes. `PII_HASH_KEY` supports outbound caller linkage today, not a claim of complete encrypted storage.

## Recordings and retention

Approved disclosure wording, matching audio and the institute's consent/retention policy precede retention. Source retention default is 90 days; local pruning exists, but complete raw/spool/database/backup deletion and audit remain future controls. Use authorized expiring playback access and audit reads/deletes; no public recording links.

CI uses synthetic/redacted fixtures and fake paid providers. L12 adds security scans and final-local-profile enforcement. L13 injects secrets at runtime, excludes PII/model weights from images and verifies backup/rollback. See [decisions](decisions.md), [recordings](09-recording-storage.md) and [API tutorial](15-api-security-and-jobs.md).


## Section 12 prompt and safety boundary

The [safety contract](04-guardrails.md#untrusted-inputs-and-limits) defines bounded caller/slot characters, length and enum validation, instruction-override canaries and the final deterministic output gate. Rejected utterances never become live model history. Structured events omit raw text and preserve call/turn correlation; API serialization exposes only approved audit metadata. Database write failures produce a safe line and an explicit structured failure signal. This is not complete semantic injection prevention, universal PII detection or complete semantic/production security acceptance. Section 28 separately adds user authorization.


## Sections 29–31 implementation and operational boundary

[Level 8](levels/level-08.md) owns callback capabilities, shared API admission and privacy/access implementation. The new recording route replaces permanent URL exposure; call-resource recording_url values are always null. Setup of private keys, migration, approved accounts, consent wording/retention and deployment access audits remains explicit. Historical descriptions above must not be read as current permanent-link authorization.

### Claude API retention review — 2026-10-24

The [Claude API retention documentation](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) makes eligibility depend on model, API feature and organization arrangement. It currently identifies specific Covered Models requiring retention; eligibility alone does not prove this project's account has zero data retention. The [commercial retention policy](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) describes deletion within 30 days under its standard policy, with contractual, feature, abuse and legal exceptions. These pages have differing general wording: the operator must confirm the applicable account agreement, exact configured Haiku model and enabled features with Anthropic before private caller-text acceptance. No ZDR agreement, model-specific entitlement or provider deletion was verified in this increment. Preserve the configured model and deferred paid testing.

The adapter masks recognized phone numbers, email addresses, hashes and locations before Claude requests; prompts must contain only the current bounded controller task. Ordinary operational logs carry reviewed metadata, not prompts/transcripts. Pattern redaction is not universal PII detection, and selected encrypted SQL fields do not encrypt Redis/checkpoints, all database fields, audio files or backups automatically.

### Institute privacy/legal review

Collect only counselling-flow data, state the approved purpose, and require deterministic recording consent. Consent/disclosure wording and retention periods need institute approval; a configured duration is not legal sign-off. Support authorized erasure/anonymization and preserve retry evidence without private transcript content. India's DPDP Act has phased commencement: see the [official MeitY commencement notification](https://www.meity.gov.in/static/uploads/2025/11/c56ceae6c383460ca69577428d36828b.pdf). The institute's legal advisor must confirm current duties, applicable dates, notices/consent, rights handling, processors and backup/deletion obligations. Source code/offline tests do not establish legal compliance.

### Recommended policy before production acceptance

The owner delegated conservative recommendations. The following is a proposed institute policy, not a legal opinion, signed consent notice or assertion of Anthropic account entitlement. Supervised tests use fictional details with recording and transcript retention off. Keep `PRIVACY_RETENTION_DAYS` unset for real data until the institute approves the scope, exceptions and enforcement; a global duration would silently assign a policy to legacy data.

| Data | Conservative recommendation | Enforcement/approval boundary |
|---|---|---|
| Supervised TEST ONLY audio/transcripts | Retain none by default; exceptional approved fixtures at most 24 hours | Separate explicit fixture consent; delete managed artifacts afterward |
| Lab checkpoints / Redis hot state | Existing four hours / one hour | Expiry metadata is not proof of physical deletion |
| Production caller/call private data | 30 days after last legitimate counselling interaction | Proposed only; appointments need a separate business/legal rule |
| Consent-approved recordings | Seven days; disabled by default | Approved disclosure/audio plus per-call consent and expiry first |
| Metadata-only security logs | 30 days | No prompts, transcripts, full phones or bearer values |
| Encrypted rolling backups | Seven days plus independent restricted key escrow | Proposed schedule; include replicas, restore rehearsals and legal holds |
| Operator exports | None by default; approved purpose, at most seven days | Inventory owner, destination, expiry and deletion receipt |
| Minimized disposal/idempotency ledger | Entire callback, job and backup replay horizon | Current tombstones stay permanent until safe pruning is demonstrated |

Proposed recording notice for institute review: “हम आपकी कोर्स काउंसलिंग और अपॉइंटमेंट में मदद के लिए ज़रूरी जानकारी लेंगे। क्वालिटी की जाँच के लिए क्या इस कॉल की रिकॉर्डिंग की अनुमति है? मना करने पर भी काउंसलिंग जारी रहेगी।” This is documentation only: it does not replace the pending runtime constant or create matching approved audio. The institute must specify a real privacy contact, rights/request process, processor disclosure (including minimized text sent to Claude), withdrawal path, retention exceptions and any child-data requirements with its legal advisor. Do not describe refusal as supported until the live no-recording continuation is verified.

Before real caller text is sent, obtain evidence for the actual Anthropic organization, configured Haiku model, Messages/structured-output/cache features and agreement. Public feature eligibility is not an organization-level ZDR confirmation. Record the approval reference without copying credentials or contracts into ordinary logs. Keep native ratings and legal/provider approvals blank until their responsible humans supply them.

### Backup and export erasure procedure

Keep the minimal disposal ledger outside the backup being restored. Pause ingress/workers and restore into a loopback-only quarantined environment. Authenticate/decrypt the saved encrypted artifact before `pg_restore --exit-on-error --no-owner --no-privileges`; stream cleartext through memory/stdin rather than creating a plaintext dump file. Verify revision/data and decrypt selected fields with the original field key. Restoring schema with a different key can preserve ciphertext while making data unreadable.

Before opening traffic, replay the latest authorized call-disposal IDs through `scripts/privacy.py --call-id <uuid>` (preview), then repeat with `--apply` against the restored environment. Reapply actual scrubbing even when a tombstone already exists: importing tombstones alone does not erase restored private rows. Verify transcripts/checkpoints/events/jobs, recording references/files and attributable Redis/queues, and keep cleanup failures retryable. Preserve the minimal ledger through the oldest backup/replica/job replay horizon. Never replay provider calls, booking effects or spoken output as part of this drill.

Inventory operator exports and provider/replica copies separately, revoke issued recording access, request authorized deletion through each system's supported process and retain metadata-only confirmations. A local call-scoped cleanup cannot certify whole-person erasure, sibling calls, unknown exports, providers or expired backups. Keep those items `external_pending` until confirmed. Historical encrypted backups are still retained for recovery; no unapproved automatic deletion schedule was enabled by the local operational setup.

### Synthetic restore-erasure evidence — 2026-10-25

A separate native PostgreSQL/Redis drill saved an encrypted pre-disposal SQL/file/Redis bundle and independently protected minimal deletion ledger. Restoring the actual saved disk artifact into a new quarantined database reproduced synthetic private names, transcripts, checkpoints, job/event payloads, files/spool and Redis queues. Importing a completed tombstone alone was insufficient; preview/apply through the production PrivacyService re-scrubbed all these managed copies before traffic. Repeated apply, preservation of unrelated sentinels and queued PostcallService replay suppression passed. The drill services and restored runtime were removed; protected synthetic recovery artifacts remain ignored. [Sanitized evidence](../benchmarks/security/restore-erasure-20261025.json) records the exact scope. This proves the local procedure, not external/unknown backup, provider, whole-person or legal erasure.
