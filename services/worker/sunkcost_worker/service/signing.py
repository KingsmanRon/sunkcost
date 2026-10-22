"""Bundle signing: Ed25519 over ``canonical_json(bundle)`` (ARCHITECTURE.md §6.2, §9).

The private key exists only in the worker's environment.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from sunkcost.core import canonical_json


class Signer:
    def __init__(self, seed_b64: str, key_id: str) -> None:
        try:
            seed = base64.b64decode(seed_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("BUNDLE_SIGNING_KEY must be base64") from None
        if len(seed) != 32:
            raise ValueError("BUNDLE_SIGNING_KEY must be a 32-byte Ed25519 seed")
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self.key_id = key_id

    @property
    def public_key_b64(self) -> str:
        raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")

    def sign(self, bundle: Mapping[str, Any]) -> str:
        """Standard base64 of the 64-byte signature over the canonical bytes (no floats allowed)."""
        return base64.b64encode(self._key.sign(canonical_json(dict(bundle)))).decode("ascii")
