"""Option models. Black-Scholes Greeks (ported from Option-floor's greeks.ts, plus rho)
and a Cox-Ross-Rubinstein binomial American value with a convergence check.
No dividend data is wired in v1, so both use zero dividends."""
from __future__ import annotations

import math


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_call(spot: float, strike: float, years: float, iv: float, rate: float) -> float:
    if years <= 0 or iv <= 0:
        return max(0.0, spot - strike)
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / (iv * root)
    d2 = d1 - iv * root
    return spot * _cdf(d1) - strike * math.exp(-rate * years) * _cdf(d2)


def call_greeks(spot: float, strike: float, days: float, iv: float, rate: float) -> dict | None:
    """Greeks per one 100-share contract, in dollars. theta per calendar day, vega and rho per 1 point."""
    if not (spot > 0 and strike > 0 and iv > 0 and days >= 1):
        return None
    years = days / 365
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / (iv * root)
    d2 = d1 - iv * root
    disc = math.exp(-rate * years)
    delta = _cdf(d1)
    gamma = _pdf(d1) / (spot * iv * root)
    theta_year = -(spot * _pdf(d1) * iv) / (2 * root) - rate * strike * disc * _cdf(d2)
    vega = spot * _pdf(d1) * root
    rho = strike * years * disc * _cdf(d2)
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 5),
        "deltaDollarsPer1": round(delta * 100, 2),
        "thetaDollarsPerDay": round(theta_year / 365 * 100, 2),
        "vegaDollarsPerPoint": round(vega * 0.01 * 100, 2),
        "rhoDollarsPerPoint": round(rho * 0.01 * 100, 2),
    }


def crr_american_call(spot: float, strike: float, years: float, iv: float, rate: float, steps: int) -> float:
    if years <= 0 or iv <= 0:
        return max(0.0, spot - strike)
    dt = years / steps
    up = math.exp(iv * math.sqrt(dt))
    down = 1 / up
    growth = math.exp(rate * dt)
    p = (growth - down) / (up - down)
    disc = 1 / growth
    values = [max(0.0, spot * up ** j * down ** (steps - j) - strike) for j in range(steps + 1)]
    for i in range(steps - 1, -1, -1):
        for j in range(i + 1):
            cont = disc * (p * values[j + 1] + (1 - p) * values[j])
            values[j] = max(cont, spot * up ** j * down ** (i - j) - strike)
    return values[0]


def american_call(spot: float, strike: float, days: float, iv: float, rate: float, tol: float = 0.005) -> dict:
    """Value per share. Converged when 400 and 800 steps agree within tol dollars per share."""
    years = max(days, 0) / 365
    iv = max(iv, 0.01)
    a = crr_american_call(spot, strike, years, iv, rate, 400)
    b = crr_american_call(spot, strike, years, iv, rate, 800)
    return {"value": b, "converged": abs(a - b) <= tol, "diff": abs(a - b)}
