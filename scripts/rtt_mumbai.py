#!/usr/bin/env python3
"""Measure network RTT from this host to a Vobiz edge — gates the latency budget.

docs/02 budgets ~100ms for "Vobiz RTT" from the production origin to Vobiz's Mumbai
edge. This script times the TCP handshake (SYN->SYN/ACK) to a target host over N
samples and reports the median — a clean network-layer proxy for that leg.

    uv run python scripts/rtt_mumbai.py                 # default: api.vobiz.ai
    uv run python scripts/rtt_mumbai.py --host <mumbai-edge-host> --count 20

Notes:
  * The AUTHORITATIVE in-call figure is Vobiz Voice Insights (per-call RTT/jitter);
    run this alongside a real test call, don't treat it as a substitute.
  * Confirm your account's Mumbai edge host in the Vobiz console (Edge Locations)
    and pass it via --host. api.vobiz.ai is only a reachable default, not the
    media edge.
  * Run this FROM the production origin — RTT measured anywhere else is meaningless.
  * There is NO such host yet (docs/decisions.md, Deploy: UNDECIDED), so this gate cannot
    be closed today. Do not record a laptop number as if it were the answer.
"""

import argparse
import socket
import statistics
import time


def _tcp_rtt_ms(host: str, port: int, timeout: float) -> "float | None":
    addr = (host, port)
    start = time.perf_counter()
    try:
        with socket.create_connection(addr, timeout=timeout):
            return (time.perf_counter() - start) * 1000.0
    except OSError as exc:
        print(f"  connect failed: {exc}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="api.vobiz.ai")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()

    print(f"TCP-handshake RTT to {args.host}:{args.port} ({args.count} samples)")
    samples = []
    for i in range(args.count):
        rtt = _tcp_rtt_ms(args.host, args.port, args.timeout)
        if rtt is not None:
            samples.append(rtt)
            print(f"  [{i + 1:>2}] {rtt:6.1f} ms")
        time.sleep(0.2)

    if not samples:
        raise SystemExit("no successful connections — check host/network")

    median = statistics.median(samples)
    print(
        f"\nmedian={median:.1f} ms  min={min(samples):.1f}  max={max(samples):.1f}  "
        f"n={len(samples)}/{args.count}"
    )
    budget = 100.0
    verdict = "within" if median <= budget else "OVER"
    print(f"docs/02 budget ~{budget:.0f} ms → {verdict} budget")


if __name__ == "__main__":
    main()
