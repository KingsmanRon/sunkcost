"""Locations of the spec files (schemas, vectors, fixtures, prices) in the source tree."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SPEC_ROOT = Path(__file__).resolve().parent.parent

SCHEMAS_DIR = SPEC_ROOT / "schemas"
VECTORS_DIR = SPEC_ROOT / "vectors"
FIXTURES_DIR = SPEC_ROOT / "fixtures"
PRICES_PATH = SPEC_ROOT / "prices" / "prices.v1.json"
FEATURES_DOC = SPEC_ROOT / "features" / "features.v1.md"


def load_json(path: Path) -> Any:
    """Parse a spec JSON file, refusing duplicate keys (they make canonical forms ambiguous)."""

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate key {key!r} in {path}")
            out[key] = value
        return out

    with path.open(encoding="utf-8") as fh:
        return json.load(fh, object_pairs_hook=no_duplicates)


def schema_paths() -> list[Path]:
    return sorted(SCHEMAS_DIR.glob("*.json"))
