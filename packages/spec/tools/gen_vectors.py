"""Generate the golden vectors in packages/spec/vectors (and the signed bundle-response fixtures).

    uv run python packages/spec/tools/gen_vectors.py          # rewrite the files
    uv run python packages/spec/tools/gen_vectors.py --check  # fail if committed files differ

This is a deliberately plain reference implementation of features/features.v1.md, canonical JSON,
holdout buckets and argument hashing. It recomputes every feature from scratch for every prefix and
shares no code with sunkcost.core, so the core's incremental implementation is tested against an
independent one. Output is deterministic: fixed inputs and a fixed RNG seed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import math
import random
import re
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SPEC = Path(__file__).resolve().parent.parent
VECTORS = SPEC / "vectors"
FIXTURES = SPEC / "fixtures"

FEATURE_NAMES = [
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

# RFC 8032 section 7.1, TEST 1. The key pair is published in the RFC; it signs test vectors only
# and no SDK trusts its key_id by default.
TEST_KEY_ID = "rfc8032-test-1"
TEST_KEY_SEED_HEX = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
TEST_KEY_PUBLIC_HEX = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"

RUN_ID = "01a0cdb5-3500-7a3c-9e41-5b2f8c7d6e10"
STARTED_AT = "2026-09-23T10:00:00.000000Z"

Json = Any
Step = dict[str, Any]


# --- time -------------------------------------------------------------------------------------

_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def us(ts: str) -> int:
    """Integer microseconds since the epoch; digits beyond the sixth are truncated."""
    m = _TS.match(ts)
    if m is None:
        raise ValueError(ts)
    y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
    frac = int((m.group(7) or "").ljust(6, "0")[:6])
    days = (date(y, mo, d) - date(1970, 1, 1)).days
    return (days * 86400 + h * 3600 + mi * 60 + s) * 1_000_000 + frac


def ts_from_us(t: int, digits: int = 6) -> str:
    dt = _EPOCH + timedelta(microseconds=t)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    frac = f"{dt.microsecond:06d}"
    if digits == 0:
        return base + "Z"
    return f"{base}.{frac[:digits].ljust(digits, '0')}Z"


# --- features.v1, recomputed from scratch per prefix -------------------------------------------


def _action(step: Step) -> tuple[str, str, str]:
    name = step["tool_name"] if step["tool_name"] is not None else (step["model"] or "")
    return (step["kind"], name, step["args_hash"] or "")


def _state(step: Step) -> tuple[str, str]:
    kind, name, _ = _action(step)
    return (kind, name)


def features_at(started_at: str, steps: list[Step], k: int) -> list[float]:
    prefix = steps[:k]
    costs = [float(s["cost_usd"]) if s["cost_usd"] is not None else 0.0 for s in prefix]
    actions = [_action(s) for s in prefix]
    states = [_state(s) for s in prefix]

    total = 0.0
    for c in costs:
        total += c

    tokens = 0
    for s in prefix:
        tokens += s["input_tokens"] + s["output_tokens"]

    if k == 0:
        recent_share = 0.0
    else:
        window = 0.0
        for c in costs[max(1, k - 2) - 1 : k]:
            window += c
        recent_share = window / max(total, 1e-12)

    if k == 0:
        repeat_rate = 0.0
    else:
        repeats = sum(1 for i in range(max(1, k - 4), k + 1) if actions[i - 1] in actions[: i - 1])
        repeat_rate = float(repeats) / float(min(5, k))

    longest = 0
    for i in range(k):
        run = 1
        while i - run >= 0 and actions[i - run] == actions[i]:
            run += 1
        longest = max(longest, run)

    errors = sum(1 for s in prefix if s["error"])
    error_streak = 0
    for s in reversed(prefix):
        if not s["error"]:
            break
        error_streak += 1

    distinct = len(set(actions))

    if k < 2:
        cycle_rate = 0.0
    else:
        transitions = [(states[i - 2], states[i - 1]) for i in range(2, k + 1)]
        seen = sum(1 for idx, t in enumerate(transitions) if t in transitions[:idx])
        cycle_rate = float(seen) / float(k - 1)

    model_steps = [s for s in prefix if s["kind"] == "model"]
    if model_steps:
        context_growth = math.log1p(float(model_steps[-1]["input_tokens"])) - math.log1p(
            float(model_steps[0]["input_tokens"])
        )
    else:
        context_growth = 0.0

    if k == 0:
        log_elapsed = 0.0
    else:
        elapsed = float(us(prefix[-1]["ts"]) - us(started_at)) / 1000000.0
        log_elapsed = math.log1p(max(0.0, elapsed))

    return [
        float(k),
        math.log1p(total),
        math.log1p(float(tokens)),
        recent_share,
        repeat_rate,
        float(longest),
        float(errors) / float(max(k, 1)),
        float(error_streak),
        float(distinct) / float(max(k, 1)),
        cycle_rate,
        context_growth,
        log_elapsed,
    ]


def logistic_v1(model: dict[str, Any], x: list[float]) -> float:
    by_name = dict(zip(FEATURE_NAMES, x, strict=True))
    s = 0.0
    for name, w, mean, std in zip(
        model["features"], model["weights"], model["means"], model["stds"], strict=True
    ):
        z_j = (by_name[name] - float(mean)) / float(std)
        s = s + float(w) * z_j
    z = float(model["intercept"]) + s
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


# --- canonical JSON (RFC 8785) ------------------------------------------------------------------

MAX_SAFE_INT = 2**53 - 1
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class CanonicalError(ValueError):
    pass


def es_number(x: float) -> str:
    """ECMAScript Number::toString for a finite float64 (RFC 8785 section 3.2.2.3)."""
    if not math.isfinite(x):
        raise CanonicalError("non-finite number")
    if x == 0.0:
        return "0"
    if x < 0.0:
        return "-" + es_number(-x)
    _, digit_tuple, exp = Decimal(repr(x)).as_tuple()
    if not isinstance(exp, int):  # only for NaN/Infinity, excluded above
        raise CanonicalError("non-finite number")
    digits = list(digit_tuple)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    k = len(digits)
    n = exp + k
    s = "".join(str(d) for d in digits)
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + s
    e = n - 1
    exponent = ("+" if e >= 0 else "-") + str(abs(e))
    return (s if k == 1 else s[0] + "." + s[1:]) + "e" + exponent


def _canon_string(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif o < 0x20:
            out.append(f"\\u{o:04x}")
        elif 0xD800 <= o <= 0xDFFF:
            raise CanonicalError("lone surrogate")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _canon(value: Json, allow_floats: bool) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INT:
            if allow_floats:
                return es_number(float(value))
            raise CanonicalError("integer outside the IEEE-754 safe range")
        return str(value)
    if isinstance(value, float):
        if not allow_floats:
            raise CanonicalError("floats are not allowed in signed content")
        return es_number(value)
    if isinstance(value, str):
        return _canon_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_canon(v, allow_floats) for v in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalError("object keys must be strings")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be", "surrogatepass"))
        items = (_canon_string(key) + ":" + _canon(value[key], allow_floats) for key in keys)
        return "{" + ",".join(items) + "}"
    raise CanonicalError(f"unsupported type {type(value).__name__}")


def canonical(value: Json, *, allow_floats: bool = False) -> bytes:
    return _canon(value, allow_floats).encode("utf-8")


def _test_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(TEST_KEY_SEED_HEX))


def sign_b64(message: bytes) -> str:
    return base64.b64encode(_test_key().sign(message)).decode("ascii")


def rfc_public_key_b64() -> str:
    raw = _test_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if raw.hex() != TEST_KEY_PUBLIC_HEX:
        raise RuntimeError("RFC 8032 test key mismatch")
    return base64.b64encode(raw).decode("ascii")


# --- holdout and argument hashing ---------------------------------------------------------------


def holdout_bucket(run_id: str) -> int:
    return int(hashlib.sha256(run_id.encode("ascii")).hexdigest()[:8], 16) % 10_000


def is_holdout(run_id: str, rate: str) -> bool:
    return holdout_bucket(run_id) < int(Decimal(rate) * 10_000)


def args_hash(salt: str, args: Json) -> str:
    digest = hmac.new(salt.encode("utf-8"), canonical(args, allow_floats=True), hashlib.sha256)
    return "hmac-sha256:" + digest.hexdigest()


# --- scenarios ----------------------------------------------------------------------------------


def _h(label: str) -> str:
    return "hmac-sha256:" + hashlib.sha256(label.encode()).hexdigest()


class RunBuilder:
    def __init__(self, started_at: str = STARTED_AT) -> None:
        self.started_at = started_at
        self.t = us(started_at)
        self.steps: list[Step] = []

    def add(
        self,
        kind: str,
        *,
        model: str | None = None,
        tool: str | None = None,
        args: str | None = None,
        tin: int = 0,
        tout: int = 0,
        cost: str | None = "0",
        error: bool = False,
        dt_us: int = 500_000,
        ts: str | None = None,
    ) -> RunBuilder:
        self.t += dt_us
        self.steps.append(
            {
                "type": "step",
                "run_id": RUN_ID,
                "seq": len(self.steps) + 1,
                "ts": ts if ts is not None else ts_from_us(self.t),
                "kind": kind,
                "model": model,
                "tool_name": tool,
                "args_hash": args,
                "input_tokens": tin,
                "output_tokens": tout,
                "cost_usd": cost,
                "error": error,
                "latency_ms": dt_us // 1000,
                "decision": None,
            }
        )
        return self


FULL_MODEL: dict[str, Any] = {
    "type": "logistic_v1",
    "intercept": "-1.5",
    "features": FEATURE_NAMES,
    "weights": [
        "0.02",
        "0.8",
        "0.1",
        "-0.5",
        "1.2",
        "0.3",
        "1.5",
        "0.6",
        "-1.0",
        "0.9",
        "0.4",
        "0.2",
    ],
    "means": ["5.0", "0.05", "6.0", "0.4", "0.2", "1.5", "0.1", "0.3", "0.7", "0.2", "0.5", "2.0"],
    "stds": [
        "4.0",
        "0.05",
        "2.0",
        "0.3",
        "0.25",
        "1.2",
        "0.15",
        "0.8",
        "0.25",
        "0.25",
        "0.8",
        "1.5",
    ],
}

SUBSET_MODEL: dict[str, Any] = {
    "type": "logistic_v1",
    "intercept": "0.3183098861837907",
    "features": ["cycle_rate", "log_cost", "error_streak", "step_index"],
    "weights": ["2.718281828459045", "-1.4142135623730951", "0.5772156649015329", "1e-05"],
    "means": ["0.1", "0.0", "0.25", "10.0"],
    "stds": ["0.3", "1.0", "0.5", "7.5"],
}

# z = -800 + 400 k (+ a small error_streak term): exp underflows to exactly 0.0 at k = 0, gives a
# denormal-free tiny score at k = 1, 0.5-ish at k = 2 and saturates to exactly 1.0 from k = 3.
EXTREME_MODEL: dict[str, Any] = {
    "type": "logistic_v1",
    "intercept": "-800.0",
    "features": ["step_index", "error_streak"],
    "weights": ["400.0", "-0.125"],
    "means": ["0.0", "0.0"],
    "stds": ["1.0", "1.0"],
}


def scenarios() -> list[tuple[str, str, RunBuilder, dict[str, Any]]]:
    out: list[tuple[str, str, RunBuilder, dict[str, Any]]] = []

    out.append(("empty-run", "No steps: only the k = 0 check.", RunBuilder(), FULL_MODEL))

    out.append(
        (
            "single-model-step",
            "One model call with tokens and cost.",
            RunBuilder().add(
                "model",
                model="claude-sonnet-4-5",
                tin=812,
                tout=240,
                cost="0.006036",
                dt_us=930_000,
            ),
            FULL_MODEL,
        )
    )

    b = RunBuilder().add("model", model="gpt-5", tin=1200, tout=80, cost="0.0023", dt_us=1_200_000)
    for _ in range(6):
        b.add("tool", tool="search_kb", args=_h("q=refund"), cost="0.002", dt_us=300_000)
    b.add("model", model="gpt-5", tin=5400, tout=60, cost="0.00735", dt_us=1_500_000)
    out.append(
        ("tool-loop", "The same tool call repeated six times, then a model call.", b, FULL_MODEL)
    )

    b = RunBuilder()
    pattern = [False, True, False, True, True, False, True, True, True]
    for i, err in enumerate(pattern):
        b.add("tool", tool="run_tests", args=_h(f"attempt-{i % 3}"), cost="0.0001", error=err)
    out.append(("error-streaks", "Errors interleaved, ending in a streak of three.", b, FULL_MODEL))

    b = RunBuilder()
    for name in ["ls", "cat", "grep", "ls", "cat"]:
        b.add("tool", tool=name, args=_h(name + "-args"), cost="0")
    out.append(
        (
            "zero-cost-tools-only",
            "No model steps (context_growth 0) and zero total cost "
            "(recent_cost_share divides by 1e-12).",
            b,
            SUBSET_MODEL,
        )
    )

    b = (
        RunBuilder()
        .add("model", model=None, tin=10, tout=5, cost=None)
        .add("tool", tool=None, args=None, cost=None)
        .add("tool", tool=None, args=None, cost="0.5")
        .add("model", model=None, tin=10, tout=5, cost=None)
    )
    out.append(
        (
            "nulls",
            'Null model, tool_name, args_hash and cost_usd: names become "" and costs 0.',
            b,
            FULL_MODEL,
        )
    )

    b = RunBuilder()
    for tin in [1000, 0, 3000, 0, 0, 12000]:
        if tin:
            b.add("model", model="claude-opus-4-1", tin=tin, tout=100, cost="0.01")
        else:
            b.add("tool", tool="fetch_page", args=_h(f"page-{len(b.steps)}"), cost="0")
    out.append(
        (
            "context-growth",
            "context_growth follows the most recent model step, not step k, "
            "when tool steps are interleaved.",
            b,
            FULL_MODEL,
        )
    )

    b = RunBuilder(started_at="2026-09-23T10:00:00.5Z")
    b.add("model", model="m", tin=5, tout=5, cost="0.000000001", ts="2026-09-23T09:59:58.000000Z")
    b.add("model", model="m", tin=5, tout=5, cost="0.000000001", ts="2026-09-23T10:00:00.5000009Z")
    b.add(
        "model", model="m", tin=5, tout=5, cost="0.000000001", ts="2026-09-23T10:00:00.500001999Z"
    )
    b.add("model", model="m", tin=5, tout=5, cost="0.000000001", ts="2026-09-24T10:00:00.123Z")
    b.add("model", model="m", tin=5, tout=5, cost="0.000000001", ts="2027-09-23T10:00:00Z")
    out.append(
        (
            "timestamps",
            "A step before started_at (clamped to 0), sub-microsecond digits (truncated), "
            "1 and 3 fractional digits, day- and year-long gaps.",
            b,
            FULL_MODEL,
        )
    )

    b = RunBuilder()
    for i in range(8):
        b.add("tool", tool="plan" if i % 2 == 0 else "act", args=_h(f"arg-{i}"), cost="0.001")
    out.append(
        (
            "state-cycle",
            "Alternating states with distinct arguments: actions never repeat, transitions do.",
            b,
            SUBSET_MODEL,
        )
    )

    b = (
        RunBuilder()
        .add("model", model="gpt-5", tool="lookup", args=None, tin=50, tout=10, cost="0.0001")
        .add("tool", tool="lookup", args=None, cost="0")
        .add("tool", tool="lookup", args=_h("x"), cost="0")
        .add("tool", tool="ünïcødé-tool ✓", args=_h("x"), cost="0")
        .add("model", model="gpt-5", tin=60, tout=10, cost="0.0001")
    )
    out.append(
        (
            "names",
            "A model step with a tool_name (tool_name wins), same name with different kinds "
            "and args, non-ASCII names.",
            b,
            FULL_MODEL,
        )
    )

    b = RunBuilder()
    b.add("model", model="big", tin=1_000_000_000, tout=1_000_000_000, cost="99999999999.999999999")
    b.add("model", model="big", tin=1, tout=0, cost="0.000000001")
    b.add("tool", tool="t", args=_h("t"), cost="12345.678901234", dt_us=86_400_000_000)
    out.append(("large-values", "Schema-maximum tokens and money, a day-long step.", b, FULL_MODEL))

    b = RunBuilder()
    for _ in range(4):
        b.add("tool", tool="retry", args=_h("same"), cost="0", error=True)
    out.append(
        (
            "extreme-scores",
            "Scores that underflow toward 0 and saturate to 1.0 (both sigmoid branches).",
            b,
            EXTREME_MODEL,
        )
    )

    rng = random.Random(20260923)
    tools = ["search_kb", "fetch_page", "run_tests", "edit_file", "ls"]
    models = ["claude-sonnet-4-5", "gpt-5-mini"]
    b = RunBuilder()
    context = 800
    for _ in range(200):
        if rng.random() < 0.35:
            context += rng.randint(50, 900)
            b.add(
                "model",
                model=rng.choice(models),
                tin=context,
                tout=rng.randint(10, 600),
                cost=f"{rng.randint(0, 50_000) / 1_000_000:.6f}",
                error=rng.random() < 0.05,
                dt_us=rng.randint(200_000, 4_000_000),
            )
        else:
            tool = rng.choice(tools)
            b.add(
                "tool",
                tool=tool,
                args=_h(f"{tool}-{rng.randint(0, 6)}"),
                cost=f"{rng.randint(0, 3_000) / 1_000_000:.6f}" if rng.random() < 0.7 else None,
                error=rng.random() < 0.15,
                dt_us=rng.randint(20_000, 2_000_000),
            )
    out.append(
        (
            "random-200",
            "200 seeded pseudo-random steps (the SDK performance target length).",
            b,
            FULL_MODEL,
        )
    )
    return out


# --- file generation ----------------------------------------------------------------------------


def _dumps(doc: Json) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def feature_vectors() -> dict[Path, str]:
    files: dict[Path, str] = {}
    for name, description, run, model in scenarios():
        expected = []
        for k in range(len(run.steps) + 1):
            x = features_at(run.started_at, run.steps, k)
            expected.append(
                {"k": k, "features": [repr(v) for v in x], "score": repr(logistic_v1(model, x))}
            )
        doc = {
            "feature_spec": "features.v1",
            "name": name,
            "description": description,
            "feature_names": FEATURE_NAMES,
            "started_at": run.started_at,
            "steps": run.steps,
            "model": model,
            "expected": expected,
        }
        files[VECTORS / "features.v1" / f"{name}.json"] = _dumps(doc)
    return files


RFC8785_SORT_EXAMPLE = {
    "€": "Euro Sign",
    "\r": "Carriage Return",
    "דּ": "Hebrew Letter Dalet With Dagesh",
    "1": "One",
    "\U0001f600": "Emoji: Grinning Face",
    "\u0080": "Control",
    "ö": "Latin Small Letter O With Diaeresis",
}


def canonical_vectors() -> dict[Path, str]:
    files: dict[Path, str] = {}
    bundle_fixtures = {
        "bundle-enforce": FIXTURES / "valid" / "bundle.v1--enforce.json",
        "bundle-shadow-extremes": FIXTURES / "valid" / "bundle.v1--shadow-extremes.json",
    }
    cases: list[tuple[str, str, Json]] = [
        (
            "rfc8785-key-order",
            "RFC 8785 section 3.2.3 example: keys sort by UTF-16 code units, "
            "so U+1F600 sorts before U+FB33.",
            RFC8785_SORT_EXAMPLE,
        ),
        (
            "string-escapes",
            'Only " \\ and U+0000-U+001F are escaped (short forms where they exist, '
            "else lower-case \\u00xx); everything else is literal UTF-8.",
            {
                "controls": "\u0000\u0001\b\t\n\u000b\f\r\u001f",
                "quote_backslash_slash": '"\\/',
                "del_and_separators": "\u007f\u2028\u2029",
                "non_ascii": "é中𝄞😀",
            },
        ),
        (
            "structure",
            "Nesting, empty containers, literals and the integer range limits.",
            {
                "z": [],
                "a": {},
                "m": [True, False, None, 0, -1, 9007199254740991, -9007199254740991],
                "nested": {"b": [{"y": 1, "x": 2}], "a": "last-key-first"},
            },
        ),
    ]
    for name, path in bundle_fixtures.items():
        cases.append(
            (
                name,
                f"The signed payload of fixtures/valid/{path.name}.",
                json.loads(path.read_text()),
            )
        )
    public_key = rfc_public_key_b64()
    for name, description, value in cases:
        data = canonical(value)
        doc = {
            "name": name,
            "description": description,
            "input": value,
            "canonical_hex": data.hex(),
            "signature": {
                "key_id": TEST_KEY_ID,
                "public_key_b64": public_key,
                "signature_b64": sign_b64(data),
            },
        }
        files[VECTORS / "canonical-json" / f"{name}.json"] = _dumps(doc)

    rejects = [
        (
            "non-integral-number",
            '{"threshold":0.842106}',
            "floats are not allowed in signed content",
        ),
        ("unsafe-integer", '{"n":9007199254740992}', "integer outside +/-(2^53 - 1)"),
        ("unsafe-negative-integer", '{"n":-9007199254740992}', "integer outside +/-(2^53 - 1)"),
        ("lone-high-surrogate", '{"s":"\\ud800"}', "unpaired surrogate in a string"),
        ("lone-low-surrogate-key", '{"\\udc00":1}', "unpaired surrogate in a key"),
    ]
    for _, text, _ in rejects:
        try:
            canonical(json.loads(text))
        except CanonicalError:
            continue
        raise AssertionError(f"reference implementation accepted {text}")
    doc = {
        "description": (
            "JSON texts whose parsed value canonical_json must reject. (A float with a zero "
            "fraction such as 1.0 is not listed: JavaScript cannot distinguish it from 1 after "
            "parsing.)"
        ),
        "cases": [{"name": n, "json_text": t, "reason": r} for n, t, r in rejects],
    }
    files[VECTORS / "canonical-json" / "must-reject.json"] = _dumps(doc)
    return files


def signed_response_fixtures() -> dict[Path, str]:
    bundle = json.loads((FIXTURES / "valid" / "bundle.v1--enforce.json").read_text())
    signature = sign_b64(canonical(bundle))
    files: dict[Path, str] = {}
    for suffix, override in (("signed", None), ("override-shadow", "shadow")):
        doc = {
            "schema_version": "1",
            "bundle": bundle,
            "key_id": TEST_KEY_ID,
            "signature": signature,
            "override": override,
        }
        files[FIXTURES / "valid" / f"bundle-response.v1--{suffix}.json"] = _dumps(doc)
    return files


def _uuid7(rng: random.Random, ms: int) -> str:
    rand = rng.getrandbits(74)
    rand_a, rand_b = rand >> 62, rand & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def holdout_vectors() -> dict[Path, str]:
    rates = ["0", "0.0001", "0.05", "0.5", "0.9999", "1"]
    rng = random.Random(7)
    base_ms = 1_790_157_600_000  # 2026-09-23T10:00:00Z
    wanted: dict[int, str | None] = dict.fromkeys([0, 1, 499, 500, 4999, 5000, 9998, 9999])
    ordinary: list[str] = []
    i = 0
    while any(v is None for v in wanted.values()):
        run_id = _uuid7(rng, base_ms + i)
        i += 1
        bucket = holdout_bucket(run_id)
        if bucket in wanted and wanted[bucket] is None:
            wanted[bucket] = run_id
        elif len(ordinary) < 48:
            ordinary.append(run_id)
    run_ids = [r for r in wanted.values() if r is not None] + ordinary
    cases = [
        {
            "run_id": r,
            "sha256_hex_prefix": hashlib.sha256(r.encode("ascii")).hexdigest()[:8],
            "bucket": holdout_bucket(r),
            "holdout": [is_holdout(r, rate) for rate in rates],
        }
        for r in run_ids
    ]
    doc = {
        "description": (
            "bucket = uint32(first 8 hex digits of sha256(run_id as lower-case ASCII)) mod 10000; "
            "holdout = bucket < holdout_rate * 10000 (exact: rates have at most 4 decimals). "
            "holdout[i] corresponds to rates[i]."
        ),
        "rates": rates,
        "cases": cases,
    }
    return {VECTORS / "holdout.v1" / "buckets.json": _dumps(doc)}


def args_hash_vectors() -> dict[Path, str]:
    raw: list[tuple[str, str, Json]] = [
        ("object", "salt-1", {"query": "refund policy", "limit": 5}),
        ("same-object-other-salt", "salt-2", {"query": "refund policy", "limit": 5}),
        ("key-order-irrelevant", "salt-1", {"limit": 5, "query": "refund policy"}),
        (
            "floats",
            "salt-1",
            {
                "lat": 51.5,
                "lon": -0.1276,
                "tiny": 1e-7,
                "small": 1e-6,
                "big": 1e21,
                "whole": 3.0,
                "neg_zero": -0.0,
            },
        ),
        ("big-integer", "salt-1", {"id": 12345678901234567890}),
        ("array", "salt-1", ["a", 1, 2.5, None, True, False]),
        ("string", "salt-1", "raw string argument"),
        ("empty-object", "salt-1", {}),
        ("unicode", "sälT ✓", {"ключ": "значение", "emoji": "😀"}),
    ]
    cases = []
    for name, salt, args in raw:
        data = canonical(args, allow_floats=True)
        cases.append(
            {
                "name": name,
                "salt": salt,
                "args": args,
                "canonical_hex": data.hex(),
                "args_hash": args_hash(salt, args),
            }
        )
    doc = {
        "description": (
            "args_hash = 'hmac-sha256:' + hex(HMAC-SHA256(key = UTF-8(salt), "
            "message = JCS(args))), where JCS is RFC 8785 including its number serialization "
            "(tool arguments may contain floats; signed content may not)."
        ),
        "cases": cases,
    }
    return {VECTORS / "args-hash.v1" / "cases.json": _dumps(doc)}


GENERATORS: list[Callable[[], dict[Path, str]]] = [
    feature_vectors,
    canonical_vectors,
    signed_response_fixtures,
    holdout_vectors,
    args_hash_vectors,
]


def generate() -> dict[Path, str]:
    files: dict[Path, str] = {}
    for gen in GENERATORS:
        files.update(gen())
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="fail if committed files differ")
    args = parser.parse_args(argv)
    files = generate()
    stale = [p for p, text in files.items() if not p.exists() or p.read_text("utf-8") != text]
    managed_dirs = {p.parent for p in files if p.is_relative_to(VECTORS)}
    extra = [p for d in managed_dirs for p in d.glob("*.json") if p not in files]
    if args.check:
        for p in stale:
            print(f"stale: {p.relative_to(SPEC)}", file=sys.stderr)
        for p in extra:
            print(f"not generated: {p.relative_to(SPEC)}", file=sys.stderr)
        return 1 if stale or extra else 0
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(files[p], "utf-8")
    for p in extra:
        p.unlink()
    print(f"{len(files)} files, {len(stale)} written, {len(extra)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
