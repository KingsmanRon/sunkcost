"""Default trusted bundle-signing keys, pinned by ``key_id`` (§9).

Empty until the owner generates the production key offline (``scripts/gen-signing-key.py``) and
adds its public half here before the first release. Until then a client trusts only the keys
passed as ``trusted_keys``. The public RFC 8032 test key used by the spec vectors must never be
listed here; ``tests/sdk/test_keys.py`` enforces that.
"""

from __future__ import annotations

DEFAULT_TRUSTED_KEYS: dict[str, str] = {}
