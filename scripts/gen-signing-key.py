#!/usr/bin/env python3
"""Generate a bundle-signing key (Ed25519). Run by the owner, offline (CLAUDE.md, §9).

    python3 scripts/gen-signing-key.py --key-id sc-2026-10

Prints the private seed for the worker's BUNDLE_SIGNING_KEY secret and the public key for the SDKs'
pinned DEFAULT_TRUSTED_KEYS. Nothing is written to disk. Put the seed straight into the platform's
secret store, never into a file in this repository.
"""

from __future__ import annotations

import argparse
import base64
import re
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Sunkcost bundle-signing key.")
    parser.add_argument(
        "--key-id", required=True, help="e.g. sc-2026-10 (lower case, digits, dashes)"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.key_id):
        parser.error("key id must match ^[a-z0-9][a-z0-9-]{0,63}$")
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    print("# Worker secrets (set them in the platform's secret store, not in a file):")
    print(f"BUNDLE_SIGNING_KEY_ID={args.key_id}")
    print(f"BUNDLE_SIGNING_KEY={base64.b64encode(seed).decode()}")
    print()
    print("# SDK pin (packages/sdk-python/sunkcost/keys.py, and the TypeScript SDK in P6):")
    print(f'"{args.key_id}": "{base64.b64encode(public).decode()}",')
    print(
        "\nRotation (§9): add the new public key to the SDK defaults, release the SDKs, switch the",
        "worker, and keep the old key trusted for 30 days.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
