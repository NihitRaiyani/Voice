"""Who is allowed to open `/ws` — the replacement for the Twilio account-SID check.

## What this replaces, and why it could not simply be deleted

On Twilio, `/ws` was gated by comparing the `accountSid` in the `start` event against
`TWILIO_ACCOUNT_SID`. That single comparison was the **only** authentication on the socket.
Vobiz's `start` carries `callId` and `streamId` but no account identifier, so the check has
no direct analogue — and removing it would leave the endpoint open to anyone who learns the
URL, on a service that answers with a live microphone feed of a lead's conversation.

## Why a minted token rather than correlating on `callId`

Correlating `start.callId` against calls we know are in flight is the obvious move, and it
was the first choice. It was rejected on a failure-mode argument:

* The exact field name in Vobiz's Answer-URL POST body is not pinned down in the docs we
  have. If it is spelled differently than expected, correlation matches nothing.
* That failure is silent and total — **every** call rejected — and it only shows up on a live
  call, after a lead has already picked up.
* The obvious mitigation, failing open when the registry is empty, is worse: it is an
  authentication bypass that switches itself on precisely when the mechanism is broken.

The token depends on nothing but us. We mint it when serving `/answer`, put it in the
`<Stream>` URL we control, and require it back on `/ws`. Single-use and time-boxed, so a URL
captured from a log is worthless: the call it belonged to has already consumed it.

`call_id` is still recorded and compared when Vobiz supplies one — not as a gate, but so a
mismatch is *visible* rather than invisible. That keeps the observability of correlation
without making the call path depend on an unverified field name.

Once the production origin has a static IP, an IP allowlist at the ingress is the right second
layer (Vobiz recommends the same posture for trunks). This is the application-level half.
"""

import secrets
import time

# How long a minted token stays usable. Vobiz opens the socket immediately after fetching the
# answer XML, so this only has to cover answer -> connect, not the call. Short enough that a
# leaked URL is stale almost at once; long enough to survive a slow carrier.
TOKEN_TTL_SECS = 120.0

# Refuse to grow without bound if something mints tokens that are never consumed (a carrier
# that fetches the answer URL and never connects). Oldest are dropped first.
MAX_PENDING = 256


class PendingStreams:
    """Tokens minted at `/answer` and redeemed at `/ws`. One per call, single use.

    Not thread-safe and does not need to be: it is touched only from the event loop, like
    everything else in this process (docs/08).
    """

    def __init__(
        self, ttl_secs: float = TOKEN_TTL_SECS, max_pending: int = MAX_PENDING
    ) -> None:
        self._ttl = ttl_secs
        self._max = max_pending
        # token -> (call_id, minted_at)
        self._pending: dict[str, tuple[str | None, float]] = {}
        self.minted = 0
        self.redeemed = 0
        self.rejected = 0

    def mint(self, call_id: "str | None" = None, *, now: "float | None" = None) -> str:
        """Issue a token for a call that is about to connect."""
        now = time.monotonic() if now is None else now
        self._evict(now)
        if len(self._pending) >= self._max:
            oldest = min(self._pending, key=lambda t: self._pending[t][1])
            del self._pending[oldest]
        token = secrets.token_urlsafe(32)
        self._pending[token] = (call_id, now)
        self.minted += 1
        return token

    def redeem(
        self, token: "str | None", *, now: "float | None" = None
    ) -> "tuple[bool, str | None]":
        """Consume `token`. Returns `(ok, call_id)`.

        Consumed whether or not the caller goes on to succeed — a token is one connection's
        worth of authority, and a retry needs a new answer.
        """
        now = time.monotonic() if now is None else now
        self._evict(now)
        entry = self._pending.pop(token, None) if token else None
        if entry is None:
            self.rejected += 1
            return False, None
        self.redeemed += 1
        return True, entry[0]

    def _evict(self, now: float) -> None:
        expired = [t for t, (_, minted) in self._pending.items() if now - minted > self._ttl]
        for t in expired:
            del self._pending[t]

    def __len__(self) -> int:
        return len(self._pending)


__all__ = ["PendingStreams", "TOKEN_TTL_SECS", "MAX_PENDING"]
