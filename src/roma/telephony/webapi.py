"""The browser UI's two endpoints, mounted onto the media app (docs/13).

Lives in `src/` and not in `scripts/serve_media.py` where it was first written: an
endpoint that rings real phones and spends real money needs to be import-testable, and
`scripts/` is not on the test path. The script is now only the wiring.
"""

import logging

_log = logging.getLogger("roma.dialer")


def _log_api(msg: str, *args) -> None:
    _log.info(msg, *args)


async def _put_dialing_guarded(status_store, request_uuid, lead_token, to_number) -> None:
    """Record the fired dial, and swallow a store failure rather than 500 a live call.

    By the time this runs the phone is already ringing. An error here must not surface as a
    500 for a dial that actually happened — the operator still gets the uuid; status polls
    answer 404 until Redis returns. Module-level (unlike everything else on this path) so the
    guard itself is directly testable; nothing inside `mount_web_api` can be patched.
    """
    try:
        await status_store.put_dialing(request_uuid, lead_token, to_number)
    except Exception:  # noqa: BLE001 — the record is not the call
        _log_api("call status write failed for request_uuid=%s", request_uuid)


def mount_web_api(app, *, reachable_fn=None) -> None:
    """The two endpoints the browser UI calls, plus CORS (docs/13).

    `reachable_fn` is the carrier-reachability preflight, injected so the offline suite can
    exercise the dial path without a DNS lookup. Defaults to the real
    `dialer.preflight.base_url_reachable`. It is a PARAMETER rather than a patchable module
    global because everything this function needs is imported inside it, so
    `monkeypatch.setattr(webapi, ...)` cannot reach any of it — the existing carrier-rejection
    test discovered that the hard way and had to weaken its own assertion to stay green.

    Mounted HERE and not in `build_media_app` for the same reason the `/debug/*` routes are:
    the 1291-test offline suite constructs that factory directly, and a surface that rings
    real phones does not belong in the object those tests build by default.

    ## This endpoint can spend money and ring strangers

    What bounds it:

      * Gate 0 runs FIRST — window, DNC denylist, spend — and only `to_number` is read
        from the body, so no request can widen a gate. The consent ALLOWLIST was removed on
        2026-08-04 (decisions.md): any valid Indian mobile now dials unless suppressed.
      * `OPENAI_BUDGET_INR` and the calling window still apply, unchanged.
      * **A bearer token is required and FAILS CLOSED** — no `API_TOKEN` configured means
        503, not an open door.
      * **`BIND_HOST` defaults to 127.0.0.1**, so binding wide is a deliberate config act.
      * An hourly dial cap bounds a stuck retry loop, which the spend cap cannot see coming.

    What the token is NOT: the browser sends it from `VITE_API_TOKEN`, which Vite inlines
    into the bundle, so it is not secret from anyone who can load the page. It guards the
    NETWORK boundary. A public deploy needs a session, not a bundled constant.
    """
    import secrets
    from datetime import datetime

    from fastapi import HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware

    from roma.config import get_settings
    from roma.dialer.callstatus import CallStatusStore, HourlyDialCap
    from roma.dialer.dnd import DenylistRegistry, is_indian_mobile, numbers_from_config
    from roma.dialer.leadstore import OutboundLead, RedisLeadStore
    from roma.dialer.openerstore import OpenerStore
    from roma.dialer.precall import precall_check
    from roma.dialer.preflight import base_url_reachable as _real_reachable
    from roma.dialer.trigger import DialPrereqError, trigger_outbound_call
    from roma.dialer.window import CALL_TIMEZONE
    from roma.postcall.paths import spend_ledger_path
    from roma.spend import SpendLedger
    from roma.telephony.dialer import build_vobiz_client
    from roma.telephony.render import render_trimmed

    s = get_settings()
    base_url_reachable = reachable_fn or _real_reachable

    # ONE origin, from config. Never "*" — a wildcard would let any page a browser happens to
    # load POST to this and dial on the operator's behalf.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.web_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "authorization"],
    )

    def _require_auth(request: Request) -> None:
        """Bearer token, or nothing happens. FAILS CLOSED.

        An unset `API_TOKEN` is a 503, never an open door: this endpoint rings real phones and
        spends real budget, and "the operator has not configured the lock yet" must not be
        indistinguishable from "this needs no lock".

        `compare_digest` rather than `==` so the comparison does not leak the token's prefix
        through timing. Cheap, and the alternative is a habit that eventually matters.
        """
        if s.api_token is None or not s.api_token.get_secret_value():
            raise HTTPException(
                status_code=503,
                detail="API_TOKEN is not configured; the dial endpoint is disabled",
            )
        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            presented.strip(), s.api_token.get_secret_value()
        ):
            raise HTTPException(status_code=401, detail="bad or missing bearer token")

    @app.post("/api/call")
    async def _place_call(request: Request):
        _require_auth(request)
        # Raw `Request` + hand-parsed body, matching `/answer` in `media.py`. The house style
        # here is no Pydantic request models, and one route inventing them would be the odd
        # one out for no gain on a single-field payload.
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — a malformed body is a 400, not a 500
            raise HTTPException(status_code=400, detail="expected a JSON body") from None
        # ONLY `to_number` is read. Any other key in the body is ignored, which is what makes
        # "the request cannot widen a gate" true by construction rather than by vigilance —
        # there is no code path from the payload to the registry, the window or the cap.
        to_number = str((payload or {}).get("to_number", "")).strip()
        if not is_indian_mobile(to_number):
            raise HTTPException(
                status_code=400,
                detail="to_number must be an Indian mobile in E.164, e.g. +919876543210",
            )

        # Allow-by-default, since 2026-08-04 (decisions.md). Read `DenylistRegistry`'s
        # docstring before changing this line — it is the weaker of the two postures.
        registry = DenylistRegistry(suppressed=numbers_from_config(s.dnd_numbers))
        ledger = SpendLedger(spend_ledger_path(s))
        verdict = precall_check(
            to_number,
            datetime.now(CALL_TIMEZONE),
            registry,
            spend=ledger,
            budget_inr=s.openai_budget_inr,
        )
        if not verdict.may_dial:
            # 409, not 400 or 500: the request was well-formed and nothing failed. A gate
            # refused it, and the reason string is the useful part.
            _log_api("dial refused: %s", verdict.reason)
            raise HTTPException(
                status_code=409, detail={"status": "blocked", "reason": verdict.reason}
            )

        # Can the CARRIER reach us? Checked here, before the cap, so a dead tunnel does not
        # burn an hour's dial quota discovering itself.
        #
        # Without this the operator's error is `502 carrier refused (HTTPStatusError)`, which
        # blames Vobiz for a stale line in `.env`. A dead `cloudflared` quick tunnel did
        # exactly that on 2026-08-04 AND again on 2026-08-05; the second one cost a live
        # session to rediagnose. `preflight.py` has the full account.
        reachable, why = await base_url_reachable(s.public_base_url)
        if not reachable:
            _log_api("dial refused: %s", why)
            raise HTTPException(status_code=503, detail={"status": "blocked", "reason": why})

        # AFTER the gates, so a refused call does not consume the hour's budget — and before
        # dialing, because the point is to stop the call, not to count it afterwards.
        cap = HourlyDialCap(s.redis_url.get_secret_value(), limit=s.max_calls_per_hour)
        if not await cap.take():
            _log_api("dial refused: hourly cap of %d reached", s.max_calls_per_hour)
            raise HTTPException(
                status_code=429,
                detail={
                    "status": "blocked",
                    "reason": f"cap:hourly ({s.max_calls_per_hour}/hour)",
                },
            )

        try:
            client = build_vobiz_client()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"cannot dial: {exc}") from exc

        status_store = CallStatusStore(
            s.redis_url.get_secret_value(), ttl=s.call_status_ttl_secs
        )
        try:
            fired = await trigger_outbound_call(
                OutboundLead(phone=to_number, branch="Vadodara"),
                store=RedisLeadStore(s.redis_url.get_secret_value()),
                client=client,
                from_number=s.vobiz_from_number.get_secret_value(),
                base_url=s.public_base_url.rstrip("/"),
                opener_store=OpenerStore(s.redis_url.get_secret_value()),
                render=lambda text: render_trimmed(s.sarvam_api_key.get_secret_value(), text),
            )
        except DialPrereqError as exc:
            # Our own store failed BEFORE the carrier was asked to do anything. 503, not
            # 502: blaming Vobiz for a Redis outage sent the operator to the wrong dashboard.
            _log_api("dial refused: %s", exc)
            raise HTTPException(
                status_code=503, detail={"status": "blocked", "reason": str(exc)}
            ) from exc
        except Exception as exc:
            # Vobiz answers a number it will not dial with a 400, which `create_call` raises.
            # Unhandled, that reached the operator as a bare 500 and told them nothing — 502
            # says the upstream refused, which is both true and actionable. The exception type
            # is safe to show; its message is not (it carries the account path).
            _log_api("dial failed upstream: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={
                    "status": "failed",
                    "reason": f"carrier refused ({type(exc).__name__})",
                },
            ) from exc
        # The token is an authority and the number is the lead's PII (docs/07): the uuid is
        # the only one of the three that may be logged or returned.
        _log_api("dial fired: request_uuid=%s", fired.request_uuid)
        await _put_dialing_guarded(
            status_store, fired.request_uuid, fired.lead_token, to_number
        )
        return {"request_uuid": fired.request_uuid, "status": "dialing"}

    @app.get("/api/call/{request_uuid}")
    async def _call_status(request_uuid: str, request: Request):
        _require_auth(request)
        store = CallStatusStore(s.redis_url.get_secret_value(), ttl=s.call_status_ttl_secs)
        record = await store.get(request_uuid)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown call")
        return {"request_uuid": request_uuid, **record}
