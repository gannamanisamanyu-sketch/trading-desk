"""Risk engine: position sizing and hard limits.

Limits (config/desk.yaml):
  * risk per trade        ≤ 1% of equity  (loss if the stop is hit, or the full premium for options)
  * total open risk       ≤ 5% of equity
  * options exposure      ≤ 15% of equity (sum of premiums/debits at risk)
  * single position size  ≤ 15% of equity (market value)
Sizing always takes the SMALLEST size allowed by every limit. If that size is
zero, the trade is rejected with the reason.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from desk.common import config


@dataclass
class SizeResult:
    ok: bool
    quantity: int                 # shares or contracts
    invested: float               # $ committed (stock cost or option premium)
    risk: float                   # $ lost if stop / max loss is hit
    risk_pct: float
    potential_profit: float
    reward_risk: float
    binding_limit: str
    notes: List[str] = field(default_factory=list)


def _limits(equity: float) -> Dict[str, float]:
    rc = config()["risk"]
    return {"per_trade": rc["max_risk_per_trade"] * equity, "total": rc["max_total_risk"] * equity,
            "options": rc["max_options_exposure"] * equity, "single": rc["max_single_stock"] * equity}


def size_stock(equity: float, entry: float, stop: float, target: float, open_risk: float = 0.0,
               direction: str = "LONG") -> SizeResult:
    lim = _limits(equity)
    per_share = abs(entry - stop)
    if per_share <= 0:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, "invalid stop", ["stop must differ from entry"])
    if direction == "LONG" and stop >= entry or direction == "SHORT" and stop <= entry:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, "invalid stop", ["stop is on the wrong side of entry"])
    caps = {
        "1% risk per trade": math.floor(lim["per_trade"] / per_share),
        "15% max position": math.floor(lim["single"] / entry),
        "5% total portfolio risk": math.floor(max(0.0, lim["total"] - open_risk) / per_share),
    }
    binding = min(caps, key=caps.get)
    qty = max(0, caps[binding])
    risk = qty * per_share
    profit = qty * abs(target - entry)
    notes = []
    if qty == 0:
        notes.append(f"rejected: {binding} leaves no room")
    return SizeResult(qty > 0, qty, qty * entry, risk, risk / equity, profit,
                      abs(target - entry) / per_share, binding, notes)


def size_option(equity: float, max_loss_per_contract: float, profit_at_target_per_contract: float,
                open_risk: float = 0.0, options_exposure: float = 0.0) -> SizeResult:
    lim = _limits(equity)
    if max_loss_per_contract <= 0:
        return SizeResult(False, 0, 0, 0, 0, 0, 0, "invalid", ["max loss must be positive"])
    caps = {
        "1% risk per trade": math.floor(lim["per_trade"] / max_loss_per_contract),
        "15% options exposure": math.floor(max(0.0, lim["options"] - options_exposure) / max_loss_per_contract),
        "5% total portfolio risk": math.floor(max(0.0, lim["total"] - open_risk) / max_loss_per_contract),
    }
    binding = min(caps, key=caps.get)
    qty = max(0, caps[binding])
    notes = []
    if qty == 0:
        notes.append(f"rejected: one contract (${max_loss_per_contract:,.0f} max loss) exceeds the {binding} limit "
                     f"(${lim['per_trade']:,.0f}); use a narrower spread or a cheaper strike")
    risk = qty * max_loss_per_contract
    return SizeResult(qty > 0, qty, risk, risk, risk / equity, qty * profit_at_target_per_contract,
                      profit_at_target_per_contract / max_loss_per_contract, binding, notes)


def portfolio_check(equity: float, positions: List[Dict]) -> List[str]:
    """Return any limit breaches for the current open positions."""
    lim = _limits(equity)
    issues = []
    total_risk = sum(p.get("risk", 0) for p in positions)
    opt = sum(p.get("risk", 0) for p in positions if p.get("instrument") == "option")
    if total_risk > lim["total"] + 1e-6:
        issues.append(f"total open risk ${total_risk:,.0f} > 5% limit ${lim['total']:,.0f}")
    if opt > lim["options"] + 1e-6:
        issues.append(f"options exposure ${opt:,.0f} > 15% limit ${lim['options']:,.0f}")
    for p in positions:
        if p.get("instrument") == "stock" and p.get("market_value", 0) > lim["single"] * 1.05:
            issues.append(f"{p['ticker']} position ${p['market_value']:,.0f} > 15% limit ${lim['single']:,.0f}")
        if p.get("risk", 0) > lim["per_trade"] * 1.05:
            issues.append(f"{p['ticker']} risk ${p['risk']:,.0f} > 1% limit ${lim['per_trade']:,.0f}")
    return issues
