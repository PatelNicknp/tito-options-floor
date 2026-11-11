"""Dollar cap math shared with the web page (docs/js/logic.js mirrors this)."""
from __future__ import annotations

import math


def effective_cap(cap: float, cash: float) -> float:
    return max(0.0, min(cap, cash))


def max_entry_ask(cap: float, fee_per_contract: float, qty: int, cash: float) -> float | None:
    """X = (cap - open fee - close fee) / (100 * qty), rounded down to the cent. None if nothing fits."""
    if qty < 1:
        return None
    room = effective_cap(cap, cash) - 2 * fee_per_contract * qty
    if room <= 0:
        return None
    x = math.floor(room / (100 * qty) * 100 + 1e-9) / 100
    return x if x > 0 else None


def max_entry_net_debit(cap: float, fee_per_contract: float, qty: int, cash: float, legs: int = 2) -> float | None:
    """Max net debit per share when opening (and later closing) a multi-leg position.

    Each leg pays an open fee and a close fee, so two legs use 4 × fee × qty against the cap.
    For one leg this matches max_entry_ask.
    """
    if qty < 1 or legs < 1:
        return None
    room = effective_cap(cap, cash) - 2 * fee_per_contract * qty * legs
    if room <= 0:
        return None
    x = math.floor(room / (100 * qty) * 100 + 1e-9) / 100
    return x if x > 0 else None
