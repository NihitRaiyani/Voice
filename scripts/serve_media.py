#!/usr/bin/env python3
"""Step-1 live-test harness: serve the Media Streams /ws app on :8020 (docs/10).

Point uvicorn at `create_app` — NOT at `roma.telephony.media:build_media_app`. The
bare app factory never calls `configure_logging()`, so `roma.telephony` INFO lines —
including the teardown `media stream ended: ... inbound_frames=N` that PROVES audio-IN
— are dropped (Python's last-resort handler emits WARNING+ only). `create_app()`
configures logging (with secret redaction, docs/07) before building the app.

For the live audio test it runs with `auto_hang_up=False` (the callee hangs up)
and exposes a read-only `/debug/last-counter` route so audio-IN can be confirmed
from the live inbound-frame counter without waiting for pipeline teardown. This is
a TEST server, not the prod entrypoint.

  uvicorn scripts.serve_media:create_app --factory --host 127.0.0.1 --port 8020

127.0.0.1 is not incidental: /api/call rings real phones and spends real budget, and its
bearer token is inlined into the browser bundle rather than being a session. `BIND_HOST`
carries the same default for the __main__ path below.
"""

import uvicorn
from fastapi import FastAPI

from roma.config import require_reachable_base_url
from roma.logging_setup import configure_logging
from roma.telephony.media import build_media_app, sole_call
from roma.telephony.webapi import mount_web_api


def create_app() -> FastAPI:
    """uvicorn `--factory` target: logging configured first, then the /ws app + debug routes.

    The base-URL check lives here rather than in `build_media_app` because the offline tests
    construct that directly and localhost is right for them. What must never boot unreachable
    is the thing a carrier is about to call.
    """
    configure_logging()
    require_reachable_base_url()
    # `<Connect><Stream>` keeps Twilio attached to the bidirectional stream. Production turns
    # on Pipecat's REST hang-up so a finished pipeline cannot leave billed dead air behind.
    app = build_media_app(auto_hang_up=True)

    @app.get("/debug/last-counter")
    async def _last_counter():
        h = sole_call(app)
        c = h.counter if h else None
        return {
            "inbound_frames": getattr(c, "frame_count", None),
            "inbound_bytes": getattr(c, "byte_count", None),
        }

    @app.get("/debug/last-transcript")
    async def _last_transcript():
        h = sole_call(app)
        t = h.transcript if h else None
        return {
            "final_count": getattr(t, "final_count", None),
            "interim_count": getattr(t, "interim_count", None),
            "last_final": getattr(t, "last_final", None),
            "finals": getattr(t, "finals", None),
        }

    @app.get("/debug/last-spoken")
    async def _last_spoken():
        h = sole_call(app)
        p = h.pretts if h else None
        return {"last_spoken": getattr(p, "last_spoken", None)}

    @app.get("/debug/last-phase")
    async def _last_phase():
        h = sole_call(app)
        pc = h.phase_ctrl if h else None
        state = getattr(pc, "state", None)
        return {
            "phase": getattr(state, "phase", None),
            "turn_count": getattr(state, "turn_count", None),
            "won": getattr(pc, "won", None),
        }

    @app.get("/debug/endpoint-timing")
    async def _endpoint_timing():
        from roma.config import get_settings as _gs

        s = _gs()
        h = sole_call(app)
        t = h.transcript if h else None
        u = h.usage if h else None
        return {
            "vad_stop_secs": s.vad_stop_secs,
            "endpoint_default_secs": s.endpoint_default_secs,
            "endpoint_terminal_secs": s.endpoint_terminal_secs,
            "endpoint_continuation_secs": s.endpoint_continuation_secs,
            "backchannel_max_secs": s.backchannel_max_secs,
            "enable_barge_in": s.enable_barge_in,
            "final_count": getattr(t, "final_count", None),
            "ttfb": u.ttfb_summary() if u is not None else None,
        }

    mount_web_api(app)
    return app


if __name__ == "__main__":
    # Kept so `python scripts/serve_media.py` still works; the documented entry is the
    # uvicorn CLI above. log_config=None so uvicorn does not stomp configure_logging().
    from roma.config import get_settings as _gs

    uvicorn.run(create_app(), host=_gs().bind_host, port=8020, log_config=None)
