"""The JSON Schemas and the Pydantic models accept and reject exactly the same documents."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any, get_args
from urllib.parse import urlsplit

import pytest
import regress
from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
from jsonschema.protocols import Validator
from pydantic import BaseModel
from pydantic import ValidationError as ModelError
from referencing import Registry, Resource

from sunkcost_spec import models, patterns
from sunkcost_spec.resources import FIXTURES_DIR, PRICES_PATH, SCHEMAS_DIR, load_json, schema_paths

ID_BASE = "https://raw.githubusercontent.com/KingsmanRon/sunkcost/main/packages/spec/schemas/"
DOCUMENT_SCHEMAS = sorted(models.DOCUMENT_MODELS)


# JSON Schema patterns are ECMA-262 regular expressions. Python's re.search lets "$" match before a
# trailing newline, so validate "pattern" with an ECMA-262 engine instead.
@cache
def _ecma(pattern: str) -> regress.Regex:
    return regress.Regex(pattern)


def _ecma_pattern(
    validator: Validator, pattern: str, instance: Any, schema: dict[str, Any]
) -> Iterator[ValidationError]:
    if validator.is_type(instance, "string") and _ecma(pattern).find(instance) is None:
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


EcmaValidator: type[Validator] = validators.extend(  # type: ignore[no-untyped-call]
    Draft202012Validator, {"pattern": _ecma_pattern}
)


@cache
def _schemas() -> dict[str, dict[str, Any]]:
    return {p.name.removesuffix(".json"): load_json(p) for p in schema_paths()}


@cache
def _registry() -> Registry[Any]:
    return Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in _schemas().values()
    )


def schema_errors(schema_name: str, doc: Any) -> list[str]:
    validator = EcmaValidator(
        _schemas()[schema_name], registry=_registry(), format_checker=FormatChecker()
    )
    return [e.message for e in validator.iter_errors(doc)]


def model_errors(schema_name: str, text: str) -> list[str]:
    try:
        models.DOCUMENT_MODELS[schema_name].model_validate_json(text)
    except ModelError as exc:
        return [str(e["msg"]) for e in exc.errors()]
    return []


def _fixtures(kind: str) -> list[Path]:
    return sorted((FIXTURES_DIR / kind).glob("*.json"))


def _schema_of(fixture: Path) -> str:
    return fixture.name.split("--", 1)[0]


# --- the schemas themselves ---------------------------------------------------------------------


@pytest.mark.parametrize("path", schema_paths(), ids=lambda p: p.name)
def test_schema_is_valid_2020_12(path: Path) -> None:
    schema = load_json(path)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == ID_BASE + path.name


@pytest.mark.parametrize("name", DOCUMENT_SCHEMAS)
def test_document_schema_pins_schema_version(name: str) -> None:
    schema = _schemas()[name]
    assert schema["properties"]["schema_version"] == {"const": "1"}
    assert "schema_version" in schema["required"]
    assert schema["additionalProperties"] is False


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


@pytest.mark.parametrize("name", sorted(_schemas()))
def test_every_ref_resolves(name: str) -> None:
    schema = _schemas()[name]
    resolver = _registry().resolver(base_uri=schema["$id"])
    refs = list(_refs(schema))
    for ref in refs:
        resolver.lookup(ref)  # raises if unresolvable


def _pointer(doc: Any, *path: str) -> Any:
    for part in path:
        doc = doc[part]
    return doc


def test_patterns_match_schemas() -> None:
    common = _schemas()["common.v1"]["$defs"]
    assert set(patterns.COMMON_DEFS) <= set(common)
    for name, pattern in patterns.COMMON_DEFS.items():
        assert common[name]["pattern"] == pattern, name
    s = _schemas()
    inline = {
        patterns.SDK_NAME: (
            "ingest-batch.v1",
            "properties",
            "sdk",
            "properties",
            "name",
            "pattern",
        ),
        patterns.SDK_VERSION: (
            "ingest-batch.v1",
            "properties",
            "sdk",
            "properties",
            "version",
            "pattern",
        ),
        patterns.ARGS_HASH: (
            "ingest-batch.v1",
            "$defs",
            "step",
            "properties",
            "args_hash",
            "anyOf",
            "1",
            "pattern",
        ),
        patterns.SIGNATURE_B64: ("bundle-response.v1", "properties", "signature", "pattern"),
        patterns.METRIC_NAME: (
            "bundle.v1",
            "properties",
            "metrics",
            "propertyNames",
            "pattern",
        ),
        patterns.PRICE_PROVIDER: (
            "prices.v1",
            "$defs",
            "model",
            "properties",
            "provider",
            "pattern",
        ),
        patterns.PRICE_MODEL_ID: ("prices.v1", "$defs", "model_id", "pattern"),
        patterns.HTTPS_URL: ("prices.v1", "$defs", "price", "properties", "source_url", "pattern"),
    }
    for pattern, (schema_name, *path) in inline.items():
        node: Any = s[schema_name]
        for part in path:
            node = node[int(part)] if isinstance(node, list) else node[part]
        assert node == pattern, (schema_name, path)


def test_feature_names_match() -> None:
    enum = _schemas()["common.v1"]["$defs"]["feature_name"]["enum"]
    assert tuple(enum) == get_args(models.FeatureName)


# --- fixtures -----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", _fixtures("valid"), ids=lambda p: p.name)
def test_valid_fixture(path: Path) -> None:
    name = _schema_of(path)
    text = path.read_text("utf-8")
    doc = json.loads(text)
    assert schema_errors(name, doc) == []
    parsed = models.DOCUMENT_MODELS[name].model_validate_json(text)
    assert models.dump(parsed) == doc, "round trip changed the document"


@pytest.mark.parametrize("path", _fixtures("invalid"), ids=lambda p: p.name)
def test_invalid_fixture_rejected_by_both(path: Path) -> None:
    name = _schema_of(path)
    text = path.read_text("utf-8")
    assert schema_errors(name, json.loads(text)), "schema accepted an invalid document"
    assert model_errors(name, text), "model accepted an invalid document"


@pytest.mark.parametrize("path", _fixtures("invalid-semantic"), ids=lambda p: p.name)
def test_semantic_fixture_rejected_by_model_only(path: Path) -> None:
    name = _schema_of(path)
    text = path.read_text("utf-8")
    assert schema_errors(name, json.loads(text)) == [], "belongs in fixtures/invalid"
    assert model_errors(name, text), "model accepted an invalid document"


@pytest.mark.parametrize("name", DOCUMENT_SCHEMAS)
def test_every_document_schema_has_fixtures(name: str) -> None:
    for kind in ("valid", "invalid"):
        assert any(_schema_of(p) == name for p in _fixtures(kind)), (name, kind)


def test_fixture_names_refer_to_schemas() -> None:
    for kind in ("valid", "invalid", "invalid-semantic"):
        for path in _fixtures(kind):
            assert _schema_of(path) in models.DOCUMENT_MODELS, path.name


# --- limits and parser behaviour that fixtures cannot show ---------------------------------------


def _shadow_batch() -> dict[str, Any]:
    doc: dict[str, Any] = load_json(FIXTURES_DIR / "valid" / "ingest-batch.v1--shadow-run.json")
    return doc


def test_batch_event_limit() -> None:
    doc = _shadow_batch()
    step = doc["events"][1]
    doc["events"] = [copy.deepcopy(step) | {"seq": i + 1} for i in range(1000)]
    assert schema_errors("ingest-batch.v1", doc) == []
    assert model_errors("ingest-batch.v1", json.dumps(doc)) == []
    doc["events"].append(copy.deepcopy(step) | {"seq": 1001})
    assert schema_errors("ingest-batch.v1", doc)
    assert model_errors("ingest-batch.v1", json.dumps(doc))


def test_model_rejects_lone_surrogates() -> None:
    # JSON Schema has no way to say "well-formed Unicode". The model's JSON parser refuses unpaired
    # surrogates, which Postgres could not store anyway.
    text = json.dumps(_shadow_batch()).replace('"search_kb"', '"search\\ud800kb"', 1)
    assert "\\ud800" in text
    assert model_errors("ingest-batch.v1", text)


def test_model_is_strict_about_types() -> None:
    doc = _shadow_batch()
    doc["events"][1]["input_tokens"] = "812"
    assert model_errors("ingest-batch.v1", json.dumps(doc))
    assert schema_errors("ingest-batch.v1", doc)


@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        ("1970-01-01T00:00:00Z", 0),
        ("1970-01-01T00:00:01.5Z", 1_500_000),
        ("2026-09-23T10:00:00.000001Z", 1_790_157_600_000_001),
        ("2026-09-23T10:00:00.123456789Z", 1_790_157_600_123_456),
        ("2024-02-29T23:59:59.999999Z", 1_709_251_199_999_999),
    ],
)
def test_parse_timestamp_us(ts: str, expected: int) -> None:
    assert models.parse_timestamp_us(ts) == expected


# --- the real price table -----------------------------------------------------------------------

OFFICIAL_PRICE_HOSTS = {
    "anthropic": {
        "www.anthropic.com",
        "docs.anthropic.com",
        "docs.claude.com",
        "platform.claude.com",
        "claude.com",
    },
    "openai": {"openai.com", "platform.openai.com"},
    "google": {"ai.google.dev", "cloud.google.com"},
}


def test_price_table_is_valid_and_sourced() -> None:
    text = PRICES_PATH.read_text("utf-8")
    assert schema_errors("prices.v1", json.loads(text)) == []
    table = models.PriceTable.model_validate_json(text)
    seen: set[str] = set()
    for entry in table.models:
        for name in (entry.model, *entry.aliases):
            assert name not in seen, f"{name} matches two price entries"
            seen.add(name)
        hosts = OFFICIAL_PRICE_HOSTS.get(entry.provider)
        assert hosts is not None, f"no official pricing host listed for {entry.provider}"
        for price in entry.prices:
            assert urlsplit(price.source_url).hostname in hosts, price.source_url
            assert date.fromisoformat(price.retrieved_at) <= date.today()


def test_document_models_cover_schemas() -> None:
    names = {p.name.removesuffix(".json") for p in SCHEMAS_DIR.glob("*.json")} - {"common.v1"}
    assert names == set(models.DOCUMENT_MODELS)
    for model in models.DOCUMENT_MODELS.values():
        assert issubclass(model, BaseModel)
