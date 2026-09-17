#!/usr/bin/env python3
"""Place ONE live Vobiz test call into the `<Stream>` /ws spine (docs/10 Step 1).

The callee must be on the StubRegistry consented allowlist. Test numbers ONLY — never a
real lead (HANDOFF landmine).

Vobiz fetches `<base>/answer` when the callee picks up and Roma returns XML pointing at
`<base>/ws`, so the base must be publicly reachable over HTTPS — a cloudflared tunnel in dev,
a publicly reachable origin in prod.

  uv run python scripts/place_test_call.py +9198XXXXXXXX --lead-name Nihit --city Surat
  # base defaults to Settings.public_base_url (.env); override with --base-url

WITHOUT `--lead-name`/`--city`/`--segment` this dials a BARE `/answer` with no lead token,
and `media.py` keys call direction on the presence of that token — so a bare call is an
INBOUND-shaped call. Roma greets "Hello ji" instead of the lead's name and re-introduces
herself, because from her side nobody dialled anybody. That is not a bug in the greeting;
it is this script being pointed at the wrong path. Call e7671f22 was lost to it.

Pass any lead detail and the call goes out through `trigger_outbound_call`, which writes
the record to Redis under a fresh token BEFORE dialling and hands Vobiz `/answer?lead=…`.
"""

import argparse
import asyncio
import sys
from datetime import datetime

from roma.config import get_settings
from roma.dialer.dnd import StubRegistry
from roma.dialer.window import (
    CALL_TIMEZONE,
    CALL_WINDOW_END_HOUR,
    CALL_WINDOW_START_HOUR,
    in_calling_window,
)
from roma.postcall.paths import spend_ledger_path
from roma.spend import SpendLedger
from roma.telephony.dialer import build_vobiz_client, place_call, precall_check


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("callee", help="Verified Caller ID, E.164 (e.g. +9198XXXXXXXX)")
    ap.add_argument(
        "--base-url",
        default=None,
        help="override PUBLIC_BASE_URL (e.g. https://<sub>.trycloudflare.com)",
    )
    ap.add_argument(
        "--ignore-window",
        action="store_true",
        help=(
            "dial outside the 09:00-21:00 IST calling window. YOUR OWN verified test "
            "handset only — never a lead."
        ),
    )
    ap.add_argument("--lead-name", default="", help="lead's name; makes the call OUTBOUND")
    ap.add_argument("--city", default="", help="lead's city; makes the call OUTBOUND")
    ap.add_argument("--segment", default="", help="lead segment; makes the call OUTBOUND")
    ap.add_argument("--branch", default="Vadodara", help="branch named in the pitch")
    args = ap.parse_args()

    s = get_settings()
    base = (args.base_url or s.public_base_url).rstrip("/")
    answer_url = f"{base}/answer"
    registry = StubRegistry(consented={args.callee})
    now = datetime.now(CALL_TIMEZONE)

    outbound = bool(args.lead_name or args.city or args.segment)

    if args.ignore_window and not in_calling_window(now):
        print(
            f"WARNING: {now:%H:%M} IST is outside the "
            f"{CALL_WINDOW_START_HOUR:02d}:00-{CALL_WINDOW_END_HOUR:02d}:00 calling window. "
            "Dialing anyway (--ignore-window). Test handsets only.",
            file=sys.stderr,
        )
        now = now.replace(hour=12, minute=0)

    # Outbound dialing is the ONE feature that needs the account REST credentials. Report
    # that as a missing capability, not as a crash — the media server and the whole inbound
    # path run fine without them, and a traceback here reads like a broken build.
    try:
        client = build_vobiz_client()
    except RuntimeError as exc:
        print(f"cannot dial: {exc}", file=sys.stderr)
        return 2

    ledger = SpendLedger(spend_ledger_path(s))

    if outbound:
        # `trigger_outbound_call` dials directly, so the window/DND/budget gates that
        # `place_call` normally applies would be skipped on this path. Run them here — the
        # gate is the reason a script may dial at all, and minting a lead is no exemption.
        verdict = precall_check(
            args.callee, now, registry, spend=ledger, budget_inr=s.openai_budget_inr
        )
        if not verdict.may_dial:
            print(f"dialed=False reason={verdict.reason}")
            return 1

        from roma.dialer.leadstore import OutboundLead, RedisLeadStore
        from roma.dialer.openerstore import OpenerStore, render_opener_audio
        from roma.dialer.trigger import trigger_outbound_call

        lead = OutboundLead(
            phone=args.callee,
            lead_name=args.lead_name,
            city=args.city,
            branch=args.branch,
            segment=args.segment,
        )
        store = RedisLeadStore(s.redis_url.get_secret_value())
        opener_store = OpenerStore(s.redis_url.get_secret_value())

        async def _render(text: str) -> bytes:
            """Roma's opener as 8kHz PCM16, screened before it becomes bytes.

            Reuses `make_filler_clips.render` so the opener is in the SAME voice as the
            fillers — a clip rendered by a second code path is how two Romas end up on one
            call. `render_opener_audio` applies `safe_output` (`dialer.openerstore`), which
            is the only place this line can still be screened: after this it is audio.
            """
            import aiohttp
            from make_filler_clips import render as _tts
            from make_filler_clips import trim_silence

            async with aiohttp.ClientSession() as session:
                key = s.sarvam_api_key.get_secret_value()

                async def _synth(safe: str) -> bytes:
                    return trim_silence(await _tts(session, key, safe))

                return await render_opener_audio(text, synth=_synth)

        fired = asyncio.run(
            trigger_outbound_call(
                lead,
                store=store,
                client=client,
                from_number=s.vobiz_from_number.get_secret_value(),
                base_url=base,
                opener_store=opener_store,
                render=_render,
            )
        )
        # Never the token: it is an authority (logging_setup.LeadTokenFilter).
        print(f"dialed=True reason=ok call_id={fired.request_uuid} direction=OUTBOUND")
        print(f"answer_url={base}/answer?lead=***")
        print(
            f"openai spend: ₹{ledger.spent_inr:.2f} of ₹{s.openai_budget_inr:.2f} "
            f"(₹{ledger.remaining_inr(s.openai_budget_inr):.2f} left)"
        )
        return 0

    print(
        "WARNING: no lead detail given, so this is an INBOUND-shaped call — Roma will greet "
        '"Hello ji" and re-introduce herself. Pass --lead-name/--city for an outbound test.',
        file=sys.stderr,
    )
    result = place_call(
        args.callee,
        now,
        registry,
        client,
        s.vobiz_from_number.get_secret_value(),
        answer_url,
        spend=ledger,
        budget_inr=s.openai_budget_inr,
    )
    print(f"dialed={result.dialed} reason={result.reason} call_id={result.call_sid}")
    print(f"answer_url={answer_url}")
    print(
        f"openai spend: ₹{ledger.spent_inr:.2f} of ₹{s.openai_budget_inr:.2f} "
        f"(₹{ledger.remaining_inr(s.openai_budget_inr):.2f} left)"
    )
    if result.reason == "block:budget":
        print(
            "The testing budget is spent. Raise OPENAI_BUDGET_INR *and* the OpenAI "
            "dashboard hard limit — the dashboard is the real ceiling (docs/02).",
            file=sys.stderr,
        )
    return 0 if result.dialed else 1


if __name__ == "__main__":
    raise SystemExit(main())
