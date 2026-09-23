"""Regular expressions shared by the JSON Schemas and the Pydantic models.

Each value is character-for-character the ``pattern`` of the matching ``$defs`` entry in
``schemas/common.v1.json`` (or the schema named in the key). ``tests/test_conformance.py`` fails if
they drift apart. The syntax is the common subset of ECMA-262 (JSON Schema), Python ``re`` and the
Rust ``regex`` crate that pydantic-core uses; ``$`` therefore means end of input.
"""

from __future__ import annotations

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
UUID_V7 = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
TIMESTAMP = (
    r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,9})?Z$"
)
DATE = r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$"
MONEY = r"^(0|[1-9][0-9]{0,10})(\.[0-9]{1,9})?$"
F64 = r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]{1,3})?$"
F64_NONNEG = r"^(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]{1,3})?$"
OPEN_UNIT_4DP = r"^0\.([1-9][0-9]{0,3}|0[1-9][0-9]{0,2}|00[1-9][0-9]?|000[1-9])$"
RATE_4DP = r"^(0(\.[0-9]{1,4})?|1(\.0{1,4})?)$"
TEXT = r"^[^\u0000]*$"
WORKLOAD_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
EXTERNAL_REF = r"^[^\u0000]*$"
OUTCOME_SOURCE = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"
KEY_ID = r"^[a-z0-9][a-z0-9-]{0,63}$"

# Patterns defined inline in a specific schema rather than in common.v1.json.
SDK_NAME = r"^[a-z0-9][a-z0-9._-]{0,63}$"
SDK_VERSION = r"^[0-9A-Za-z.+-]{1,64}$"
ARGS_HASH = r"^hmac-sha256:[0-9a-f]{64}$"
SIGNATURE_B64 = r"^[A-Za-z0-9+/]{86}==$"
METRIC_NAME = r"^[a-z][a-z0-9_]{0,63}$"
PRICE_PROVIDER = r"^[a-z0-9][a-z0-9-]{0,31}$"
PRICE_MODEL_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$"
HTTPS_URL = r"^https://[^\s]{1,500}$"

# $defs name in common.v1.json -> pattern.
COMMON_DEFS: dict[str, str] = {
    "uuid": UUID,
    "uuid_v7": UUID_V7,
    "timestamp": TIMESTAMP,
    "date": DATE,
    "money": MONEY,
    "f64": F64,
    "f64_nonneg": F64_NONNEG,
    "open_unit_4dp": OPEN_UNIT_4DP,
    "rate_4dp": RATE_4DP,
    "text": TEXT,
    "workload_name": WORKLOAD_NAME,
    "external_ref": EXTERNAL_REF,
    "outcome_source": OUTCOME_SOURCE,
    "key_id": KEY_ID,
}
