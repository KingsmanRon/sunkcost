"""Validate documents against the v1 JSON Schemas and Pydantic models (test and tooling use).

Needs the workspace dev dependencies ``jsonschema``, ``referencing`` and ``regress``.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache
from typing import Any

import regress
from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
from jsonschema.protocols import Validator
from pydantic import ValidationError as ModelError
from referencing import Registry, Resource

from sunkcost_spec import models
from sunkcost_spec.resources import load_json, schema_paths


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
def schemas() -> dict[str, dict[str, Any]]:
    return {p.name.removesuffix(".json"): load_json(p) for p in schema_paths()}


@cache
def registry() -> Registry[Any]:
    return Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas().values()
    )


def schema_errors(schema_name: str, doc: Any) -> list[str]:
    """Messages for every way ``doc`` fails the named schema (``"ingest-batch.v1"`` etc.)."""
    validator = EcmaValidator(
        schemas()[schema_name], registry=registry(), format_checker=FormatChecker()
    )
    return [e.message for e in validator.iter_errors(doc)]


def model_errors(schema_name: str, text: str) -> list[str]:
    """Messages from the matching Pydantic model, which also enforces the semantic rules."""
    try:
        models.DOCUMENT_MODELS[schema_name].model_validate_json(text)
    except ModelError as exc:
        return [str(e["msg"]) for e in exc.errors()]
    return []
