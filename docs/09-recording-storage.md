# Recording and post-call lifecycle

**Selected baseline:** local caller/agent stereo capture and post-call finalization. Carrier-side recording is not an unresolved default choice. Pending institute disclosure wording/audio causes captures to be discarded; do not weaken that gate.

Under `Settings.roma_data_dir` (default `var/roma`), raw audio is `media/{date}/{call_sid}.s16le`, fallback jobs are under `spool/postcall/`, and finalized consent-approved WAV/JSON artifacts are under `recordings/{date}/`. Files/directories are owner-only, 0600/0700.

## Durability and ack

1. Media teardown enqueues a phone-number-free post-call message, with disk spool on enqueue failure.
2. One recording worker stores captured audio or completes a required policy discard. Calls with no capture still proceed through business handoff.
3. PostgreSQL atomically commits the completed call and idempotent job intents.
4. Only then acknowledge the recording message using its original raw payload identity.
5. Dispatcher/actors process durable job IDs and settle each database effect/status together.

Finalized files use temporary writes, fsync and atomic replacement. Filesystem storage and PostgreSQL are separate failure boundaries; replay must detect already-stored artifacts and retry failed database handoff safely. A local write is not proof of host-loss recovery.

## Privacy and remaining work

Retention default is 90 days and a local pruning path exists. Approved retention/deletion must cover raw capture, spool/dead letters, WAV/metadata, database records, replicas and backups. Define crash/poison cleanup and audited manual replay rather than deleting evidence blindly.

L8 supplies role permissions, access audit and complete deletion policy. L10 integrates time-limited authorized playback and recording metadata as required. No public blob/URL access or carrier-recording enablement is implied by these docs. Summaries currently use deterministic call metadata, not paid transcript generation; follow-up delivery is inactive.

See [security](07-security.md), [jobs](19-background-task-framework.md) and [audio asset instructions](../roma/realtime/assets/README.md).
