from __future__ import annotations

import math


def sma(values: list[float], n: int, end: int | None = None) -> float | None:
    end = len(values) if end is None else end
    if end < n or n <= 0:
        return None
    window = values[end - n:end]
    return sum(window) / n


def ret(values: list[float], n: int) -> float | None:
    if len(values) <= n or values[-1 - n] <= 0:
        return None
    return values[-1] / values[-1 - n] - 1


def realized_vol(closes: list[float], n: int) -> float | None:
    """Annualized close-to-close volatility over the last n returns (decimal)."""
    if len(closes) < n + 1:
        return None
    window = closes[-(n + 1):]
    logs = [math.log(b / a) for a, b in zip(window, window[1:]) if a > 0 and b > 0]
    if len(logs) < n:
        return None
    mean = sum(logs) / len(logs)
    var = sum((x - mean) ** 2 for x in logs) / (len(logs) - 1)
    return math.sqrt(var) * math.sqrt(252)
