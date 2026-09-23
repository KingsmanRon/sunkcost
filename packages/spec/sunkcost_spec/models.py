"""Pydantic v2 models for the Sunkcost v1 wire contracts.

The models mirror ``schemas/*.v1.json``. ``tests/test_conformance.py`` checks that the schemas and
the models accept and reject the same fixtures, and that every valid fixture round-trips through
``model_dump(mode="json", exclude_unset=True)`` unchanged.

Decimal quantities (money, float64 values, rates) stay strings here so documents round-trip
exactly; convert with ``decimal.Decimal`` (money) or ``float`` (scores, model parameters) where
they are used.

A few rules cannot be written in JSON Schema (cross-field comparisons, finiteness, integers sent as
``1.0``). The models enforce them as well; ``fixtures/invalid-semantic`` covers them.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from sunkcost_spec import patterns as p

SCHEMA_VERSION = "1"

_TIMESTAMP_PARTS = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?Z$"
)


def _valid_timestamp(value: str) -> str:
    m = _TIMESTAMP_PARTS.match(value)
    if m is None:  # pragma: no cover - the pattern constraint runs first
        raise ValueError("not a UTC RFC 3339 timestamp")
    year, month, day, hour, minute, second = (int(g) for g in m.groups()[:6])
    datetime(year, month, day, hour, minute, second)  # raises ValueError for e.g. 30 February
    return value


def _valid_date(value: str) -> str:
    date.fromisoformat(value)
    return value


def _finite(value: str) -> str:
    if not math.isfinite(float(value)):
        raise ValueError("must be a finite float64")
    return value


def _positive(value: str) -> str:
    if not float(value) > 0.0:
        raise ValueError("must be > 0")
    return value


def _unit_interval(value: str) -> str:
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError("must be within [0, 1]")
    return value


def _unique(values: list[str]) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError("items must be unique")
    return values


def _pattern(regex: str) -> StringConstraints:
    return StringConstraints(pattern=regex)


Uuid = Annotated[str, _pattern(p.UUID)]
UuidV7 = Annotated[str, _pattern(p.UUID_V7)]
Timestamp = Annotated[str, _pattern(p.TIMESTAMP), AfterValidator(_valid_timestamp)]
Date = Annotated[str, _pattern(p.DATE), AfterValidator(_valid_date)]
Money = Annotated[str, _pattern(p.MONEY)]
F64 = Annotated[str, StringConstraints(pattern=p.F64, max_length=32), AfterValidator(_finite)]
F64NonNeg = Annotated[
    str, StringConstraints(pattern=p.F64_NONNEG, max_length=32), AfterValidator(_finite)
]
OpenUnit4 = Annotated[str, _pattern(p.OPEN_UNIT_4DP)]
Rate4 = Annotated[str, _pattern(p.RATE_4DP)]
Text = Annotated[str, StringConstraints(pattern=p.TEXT, min_length=1, max_length=256)]
WorkloadName = Annotated[str, _pattern(p.WORKLOAD_NAME)]
ExternalRef = Annotated[
    str, StringConstraints(pattern=p.EXTERNAL_REF, min_length=1, max_length=128)
]
OutcomeSource = Annotated[str, _pattern(p.OUTCOME_SOURCE)]
KeyId = Annotated[str, _pattern(p.KEY_ID)]
ArgsHash = Annotated[str, _pattern(p.ARGS_HASH)]
SignatureB64 = Annotated[str, _pattern(p.SIGNATURE_B64)]

Count = Annotated[int, Field(ge=0, le=1_000_000)]
SafeUint = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
BundleVersion = Annotated[int, Field(ge=1, le=2_147_483_647)]
Tokens = Annotated[int, Field(ge=0, le=1_000_000_000)]
LatencyMs = Annotated[int, Field(ge=0, le=86_400_000)]

Mode = Literal["shadow", "enforce"]
OutcomeValue = Literal["success", "failure", "abandoned"]
Action = Literal["CONTINUE", "ABORT"]
Reason = Literal[
    "no_bundle",
    "bundle_expired",
    "below_min_steps",
    "below_threshold",
    "above_threshold",
    "holdout",
    "shadow_mode",
    "override_shadow",
    "sdk_error",
]
FeatureName = Literal[
    "step_index",
    "log_cost",
    "log_tokens",
    "recent_cost_share",
    "repeat_action_rate",
    "max_identical_streak",
    "error_rate",
    "error_streak",
    "distinct_action_ratio",
    "cycle_rate",
    "context_growth",
    "log_elapsed_s",
]

WOULD_ABORT_REASONS: frozenset[str] = frozenset(
    {"above_threshold", "holdout", "shadow_mode", "override_shadow"}
)


class WireModel(BaseModel):
    """Strict, immutable base: no coercion, no unknown keys.

    Fields listed in ``_absent_not_null`` are optional but, when present, must not be null, which
    matches a JSON Schema property that is simply not ``required``.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    _absent_not_null: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _reject_explicit_null(self) -> WireModel:
        for name in self._absent_not_null:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} must be omitted, not null")
        return self


def _check_decision(
    action: str, would_abort: bool, reason: str, score: str | None, threshold: str | None
) -> None:
    if action == "ABORT" and not (would_abort and reason == "above_threshold"):
        raise ValueError("ABORT requires would_abort = true and reason above_threshold")
    if would_abort != (reason in WOULD_ABORT_REASONS):
        raise ValueError(f"reason {reason!r} is inconsistent with would_abort = {would_abort}")
    if reason == "above_threshold" and action != "ABORT":
        raise ValueError("reason above_threshold requires action ABORT")
    if reason == "no_bundle" and (score is not None or threshold is not None):
        raise ValueError("no_bundle decisions carry no score or threshold")


# --- decision.v1 ------------------------------------------------------------------------------


class Decision(WireModel):
    schema_version: Literal["1"]
    action: Action
    mode: Mode
    score: F64 | None
    threshold: F64 | None
    bundle_id: Uuid | None
    bundle_version: BundleVersion | None
    would_abort: bool
    reason: Reason

    @model_validator(mode="after")
    def _invariants(self) -> Decision:
        _check_decision(self.action, self.would_abort, self.reason, self.score, self.threshold)
        if (self.bundle_id is None) != (self.bundle_version is None):
            raise ValueError("bundle_id and bundle_version are both null or both set")
        return self


class StepDecision(WireModel):
    action: Action
    score: F64 | None
    threshold: F64 | None
    would_abort: bool
    reason: Reason

    @model_validator(mode="after")
    def _invariants(self) -> StepDecision:
        _check_decision(self.action, self.would_abort, self.reason, self.score, self.threshold)
        return self


# --- ingest-batch.v1 --------------------------------------------------------------------------


class SdkInfo(WireModel):
    name: Annotated[str, _pattern(p.SDK_NAME)]
    version: Annotated[str, _pattern(p.SDK_VERSION)]


class RunStart(WireModel):
    type: Literal["run_start"]
    run_id: UuidV7
    workload: WorkloadName
    external_ref: ExternalRef | None
    started_at: Timestamp
    bundle_id: Uuid | None
    bundle_version: BundleVersion | None
    mode: Mode
    holdout: bool

    @model_validator(mode="after")
    def _bundle_pair(self) -> RunStart:
        if (self.bundle_id is None) != (self.bundle_version is None):
            raise ValueError("bundle_id and bundle_version are both null or both set")
        return self


class Step(WireModel):
    type: Literal["step"]
    run_id: UuidV7
    seq: Annotated[int, Field(ge=1, le=1_000_000)]
    ts: Timestamp
    kind: Literal["model", "tool"]
    model: Text | None
    tool_name: Text | None
    args_hash: ArgsHash | None
    input_tokens: Tokens
    output_tokens: Tokens
    cost_usd: Money | None
    error: bool
    latency_ms: LatencyMs | None
    decision: StepDecision | None


class RunEnd(WireModel):
    type: Literal["run_end"]
    run_id: UuidV7
    ended_at: Timestamp
    status: Literal["completed", "aborted", "errored"]
    total_cost_usd: Money
    steps: Count
    would_abort_step: Count | None
    cost_through_would_abort_usd: Money | None
    aborted_step: Count | None

    @model_validator(mode="after")
    def _summary(self) -> RunEnd:
        if self.would_abort_step is None:
            if self.cost_through_would_abort_usd is not None or self.aborted_step is not None:
                raise ValueError("without would_abort_step there is no cost-through or abort step")
            return self
        if self.cost_through_would_abort_usd is None:
            raise ValueError("would_abort_step requires cost_through_would_abort_usd")
        # Cross-field rules JSON Schema cannot express.
        if self.would_abort_step > self.steps:
            raise ValueError("would_abort_step exceeds steps")
        if self.aborted_step is not None and not (
            self.would_abort_step <= self.aborted_step <= self.steps
        ):
            raise ValueError("aborted_step must be within [would_abort_step, steps]")
        if Decimal(self.cost_through_would_abort_usd) > Decimal(self.total_cost_usd):
            raise ValueError("cost_through_would_abort_usd exceeds total_cost_usd")
        return self


class OutcomeEvent(WireModel):
    type: Literal["outcome"]
    run_id: UuidV7
    outcome: OutcomeValue
    source: OutcomeSource
    reported_at: Timestamp


Event = Annotated[RunStart | Step | RunEnd | OutcomeEvent, Field(discriminator="type")]


class IngestBatch(WireModel):
    schema_version: Literal["1"]
    sent_at: Timestamp
    sdk: SdkInfo
    dropped_events: SafeUint
    events: Annotated[list[Event], Field(min_length=1, max_length=1000)]


# --- outcome.v1 -------------------------------------------------------------------------------


class OutcomeBody(WireModel):
    """POST /v1/outcomes: addressed by run_id, or by (workload, external_ref), never both."""

    _absent_not_null = ("run_id", "workload", "external_ref", "reported_at")

    schema_version: Literal["1"]
    run_id: UuidV7 | None = None
    workload: WorkloadName | None = None
    external_ref: ExternalRef | None = None
    outcome: OutcomeValue
    source: OutcomeSource
    reported_at: Timestamp | None = None

    @model_validator(mode="after")
    def _addressing(self) -> OutcomeBody:
        by_ref = self.workload is not None or self.external_ref is not None
        if self.run_id is not None:
            if by_ref:
                raise ValueError("give run_id or (workload, external_ref), not both")
        elif self.workload is None or self.external_ref is None:
            raise ValueError("give run_id, or both workload and external_ref")
        return self


# --- bundle.v1 and bundle-response.v1 ---------------------------------------------------------


class LogisticV1(WireModel):
    type: Literal["logistic_v1"]
    intercept: F64
    features: Annotated[list[FeatureName], Field(min_length=1, max_length=12)]
    weights: Annotated[list[F64], Field(min_length=1, max_length=12)]
    means: Annotated[list[F64], Field(min_length=1, max_length=12)]
    stds: Annotated[
        list[Annotated[F64NonNeg, AfterValidator(_positive)]], Field(min_length=1, max_length=12)
    ]

    @model_validator(mode="after")
    def _shape(self) -> LogisticV1:
        _unique(list(self.features))
        n = len(self.features)
        if not len(self.weights) == len(self.means) == len(self.stds) == n:
            raise ValueError("features, weights, means and stds must have equal length")
        return self


class BundleDecision(WireModel):
    threshold: Annotated[F64NonNeg, AfterValidator(_unit_interval)]
    min_steps: Annotated[int, Field(ge=0, le=10_000)]
    target_recall: OpenUnit4
    delta: OpenUnit4


MetricValue = Annotated[SafeUint | F64, Field(union_mode="left_to_right")]
_METRIC_NAME = re.compile(p.METRIC_NAME.removeprefix("^").removesuffix("$"))


class BundleMetrics(BaseModel):
    """Required metrics plus up to 64 named extras, each an integer or a float64 string."""

    model_config = ConfigDict(extra="allow", strict=True, frozen=True)

    __pydantic_extra__: dict[str, MetricValue] = Field(init=False)

    n_train: SafeUint
    n_calib_success: SafeUint
    test_false_abort_rate: F64NonNeg
    test_savings_share: F64NonNeg
    auroc: F64NonNeg

    @model_validator(mode="after")
    def _names(self) -> BundleMetrics:
        extra = self.__pydantic_extra__ or {}
        if len(type(self).model_fields) + len(extra) > 64:
            raise ValueError("at most 64 metrics")
        for name in extra:
            if _METRIC_NAME.fullmatch(name) is None:
                raise ValueError(f"invalid metric name {name!r}")
        return self


class Bundle(WireModel):
    schema_version: Literal["1"]
    bundle_id: Uuid
    org_id: Uuid
    project_id: Uuid
    workload_id: Uuid
    workload: WorkloadName
    version: BundleVersion
    feature_spec: Literal["features.v1"]
    mode: Mode
    holdout_rate: Rate4
    model: LogisticV1
    decision: BundleDecision
    metrics: BundleMetrics
    issued_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def _validity_window(self) -> Bundle:
        if parse_timestamp_us(self.expires_at) <= parse_timestamp_us(self.issued_at):
            raise ValueError("expires_at must be after issued_at")
        return self


class BundleResponse(WireModel):
    schema_version: Literal["1"]
    bundle: Bundle
    key_id: KeyId
    signature: SignatureB64
    override: Literal["shadow"] | None


# --- prices.v1 --------------------------------------------------------------------------------


class PriceTier(WireModel):
    above_input_tokens: Annotated[int, Field(ge=1, le=1_000_000_000)]
    input: Money
    output: Money


class Price(WireModel):
    _absent_not_null = ("notes",)

    effective_from: Date
    input: Money
    output: Money
    tiers: list[PriceTier]
    source_url: Annotated[str, _pattern(p.HTTPS_URL)]
    retrieved_at: Date
    notes: Annotated[str, StringConstraints(max_length=500)] | None = None


class ModelPrices(WireModel):
    provider: Annotated[str, _pattern(p.PRICE_PROVIDER)]
    model: Annotated[str, _pattern(p.PRICE_MODEL_ID)]
    aliases: Annotated[list[Annotated[str, _pattern(p.PRICE_MODEL_ID)]], AfterValidator(_unique)]
    prices: Annotated[list[Price], Field(min_length=1)]


class PriceTable(WireModel):
    schema_version: Literal["1"]
    currency: Literal["USD"]
    unit: Literal["per_1m_tokens"]
    models: Annotated[list[ModelPrices], Field(min_length=1)]


# --- helpers ----------------------------------------------------------------------------------

_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


def parse_timestamp_us(value: str) -> int:
    """Integer microseconds since the Unix epoch; fractional digits beyond 6 are truncated."""
    m = _TIMESTAMP_PARTS.match(value)
    if m is None:
        raise ValueError(f"not a UTC RFC 3339 timestamp: {value!r}")
    year, month, day, hour, minute, second = (int(g) for g in m.groups()[:6])
    frac = (m.group(7) or "").ljust(6, "0")[:6]
    days = date(year, month, day).toordinal() - _EPOCH_ORDINAL
    return ((days * 86_400 + hour * 3_600 + minute * 60 + second) * 1_000_000) + int(frac)


DOCUMENT_MODELS: dict[str, type[BaseModel]] = {
    "ingest-batch.v1": IngestBatch,
    "outcome.v1": OutcomeBody,
    "bundle.v1": Bundle,
    "bundle-response.v1": BundleResponse,
    "decision.v1": Decision,
    "prices.v1": PriceTable,
}


def dump(model: BaseModel) -> Any:
    """The JSON-compatible form that round-trips the document the model was parsed from."""
    return model.model_dump(mode="json", exclude_unset=True)
