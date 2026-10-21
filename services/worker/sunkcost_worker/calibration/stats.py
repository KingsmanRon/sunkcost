"""Exact (Clopper–Pearson) binomial confidence bounds."""

from __future__ import annotations

from scipy.stats import beta


def _check(k: int, n: int, confidence: float) -> None:
    if n < 0 or not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")


def cp_upper(k: int, n: int, confidence: float) -> float:
    """One-sided upper bound U with P(Binomial(n, U) <= k) = 1 - confidence; 1.0 when k = n."""
    _check(k, n, confidence)
    if k == n:
        return 1.0
    return float(beta.ppf(confidence, k + 1, n - k))


def cp_lower(k: int, n: int, confidence: float) -> float:
    """One-sided lower bound L with P(Binomial(n, L) >= k) = 1 - confidence; 0.0 when k = 0."""
    _check(k, n, confidence)
    if k == 0:
        return 0.0
    return float(beta.ppf(1.0 - confidence, k, n - k + 1))


def cp_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Two-sided exact interval; each tail has (1 - confidence) / 2."""
    _check(k, n, confidence)
    if n == 0:
        return (0.0, 1.0)
    tail = (1.0 + confidence) / 2.0
    return (cp_lower(k, n, tail), cp_upper(k, n, tail))
