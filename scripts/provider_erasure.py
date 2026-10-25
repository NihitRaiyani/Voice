"""Preview-first operator erasure of an explicit known-resource manifest.

No Settings/.env, SQL, Redis or default account inventory is accessed. Remote GET
requires --verify; DELETE additionally requires --apply, digest and durable ledger.
"""

import argparse
import asyncio
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roma.providers.telephony.twilio.erasure import TwilioErasureProvider
from roma.services.provider_erasure import ErasureManifest, ProviderErasure


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid provider erasure command.")


def parse_args(argv=None):
    parser = _Parser(description="Preview/verify/apply bounded known-resource erasure")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--account-sid", default=os.environ.get("ROMA_ERASURE_TWILIO_ACCOUNT_SID")
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--verified-digest")
    parser.add_argument("--ledger", type=Path)
    return parser.parse_args(argv)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest keys are not allowed.")
        result[key] = value
    return result


def read_manifest(path: Path) -> ErasureManifest:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > 65536
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("Owner-only bounded regular manifest required.")
        with os.fdopen(fd, closefd=False) as stream:
            return ErasureManifest.parse(
                json.loads(stream.read(65537), object_pairs_hook=_unique_object)
            )
    finally:
        os.close(fd)


def build_provider(account_sid):
    key = os.environ.get("ROMA_ERASURE_TWILIO_API_KEY_SID", "")
    secret = os.environ.get("ROMA_ERASURE_TWILIO_API_KEY_SECRET", "")
    if not key or not secret:
        return None
    return TwilioErasureProvider(account_sid, key, secret)


async def run(args):
    manifest = read_manifest(args.manifest)
    service = ProviderErasure(args.account_sid)
    preview = service.preview(manifest)
    if args.apply:
        if args.ledger is None or args.verified_digest != manifest.digest:
            raise ValueError("Apply requires a matching verified digest and explicit ledger.")
    elif args.ledger is not None or args.verified_digest is not None:
        raise ValueError("Ledger and digest are only valid with explicit apply.")
    if not args.verify and not args.apply:
        return preview
    provider = build_provider(args.account_sid)
    try:
        service = ProviderErasure(args.account_sid, provider=provider)
        if args.verify:
            return await service.verify(manifest)
        return await service.apply(
            manifest, apply=True, verified_digest=args.verified_digest, ledger=args.ledger
        )
    finally:
        if provider is not None:
            await provider.aclose()


def main(argv=None):
    try:
        result = asyncio.run(run(parse_args(argv)))
        print(json.dumps(result, sort_keys=True))
        return (
            0
            if result["outcome"]
            in {"preview_only", "verified_known_resources", "known_resources_absent"}
            else 1
        )
    except Exception:  # noqa: BLE001 — never print paths, private input, keys or SDK details
        print(
            "Provider erasure failed; no completion is claimed. Review configuration and retry.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
