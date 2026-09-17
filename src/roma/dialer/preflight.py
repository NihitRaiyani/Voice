"""Is the address we are about to hand the carrier one the carrier can actually reach?

## The failure this exists to stop

`/answer` mints `wss://<PUBLIC_BASE_URL>/ws?t=…` and Vobiz dials that URL from ITS network.
When `PUBLIC_BASE_URL` names a host that no longer exists, every layer reports success right
up to the point where nothing happens:

  * the server boots fine — `require_reachable_base_url` only rejects *localhost*, and a dead
    tunnel hostname is not localhost;
  * `/api/call` passes every gate, because none of them look at the base URL;
  * Vobiz refuses the call with a 400, which surfaces as `502 carrier refused
    (HTTPStatusError)` — technically true, and it points the operator at the CARRIER when the
    fault is one line in `.env`.

That happened twice in two days (2026-08-04, 2026-08-05), both times because a `cloudflared`
quick tunnel died and took its hostname out of DNS with it while `.env` kept pointing at it.
The second time cost a live debugging session to rediscover the first.

## Why a network call on the dial path is worth it

It is ~200-800ms added to a request that is about to ring a phone for thirty seconds, and it
converts the least actionable error in the system into the most actionable one. The check is
also the only thing in the stack that tests the FULL loop the carrier will take — DNS, the
tunnel's edge, the tunnel process, and this server — from the outside.

Fails CLOSED, unlike the caps and stores around it. Those fail open because a broken counter
must not stop the operator working; this one is different in kind — if it cannot confirm the
carrier can reach us, a call placed anyway is known-dead air on a real person's phone.
"""

import logging

_log = logging.getLogger("roma.dialer")

# Long enough for a cold tunnel hop to Mumbai and back (measured 0.75-0.89s on a healthy
# bom09 edge), short enough that a dead host does not hold the browser's request open. A DNS
# failure — the actual symptom both times — returns in milliseconds and never waits this out.
REACHABILITY_TIMEOUT_SECS = 5.0


async def base_url_reachable(base_url: str, *, timeout_secs: float = REACHABILITY_TIMEOUT_SECS):
    """`(ok, reason)` — can something outside this machine fetch `<base_url>/health`?

    `/health` and not `/`: it is the one route guaranteed to exist, to need no auth, and to
    be cheap. A 200 proves DNS resolved, the tunnel edge accepted the connection, the tunnel
    process is alive, and this server answered — the entire path Vobiz is about to take.

    Any non-200 is a failure. A tunnel that resolves but returns 502 (edge up, origin down)
    is exactly as unusable as one that does not resolve at all, and telling the operator
    "reachable" because a packet came back would be worse than not checking.
    """
    import httpx

    url = f"{base_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.get(url)
    except httpx.ConnectError as exc:
        # Where a dead quick tunnel lands: the hostname is gone from DNS. Named separately
        # because "could not resolve" tells the operator to restart the tunnel, whereas a
        # timeout might just be a slow link.
        return (
            False,
            f"cannot reach PUBLIC_BASE_URL ({base_url}) — DNS or connection failed: {exc.__class__.__name__}",
        )
    except httpx.TimeoutException:
        return False, f"PUBLIC_BASE_URL ({base_url}) did not answer within {timeout_secs:.0f}s"
    except Exception as exc:  # noqa: BLE001 — an unexpected client error is still "unreachable"
        return False, f"PUBLIC_BASE_URL ({base_url}) is not reachable: {exc.__class__.__name__}"

    if response.status_code != 200:
        return False, (
            f"PUBLIC_BASE_URL ({base_url}) answered {response.status_code} — the tunnel is "
            "up but this server is not behind it"
        )
    return True, ""


__all__ = ["base_url_reachable", "REACHABILITY_TIMEOUT_SECS"]
