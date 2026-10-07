"""Point-in-time backtest of the screener: every setup it would have produced on every past day.

For each historical day d, the screener runs on data up to d only (no look-ahead), and each signal is simulated
exactly like the live forward test: entry at the next session's open, the screener's stop and target, stop
checked first, exit at the close after 20 trading days.

KNOWN BIASES (always shown next to results):
  * survivorship: today's universe (stocks that are large and liquid now) is used for the past;
  * no commissions, slippage or borrow costs; no earnings filter;
  * the screener code is the current version (see system_version), not what was run at the time.
Backtest evidence can motivate a HYPOTHESIS or EXPERIMENT; it never validates a change on its own, and it is never
mixed with paper-trade results.
Files: journal/backtest_raw.csv (signals, appended incrementally) · outcomes recomputed by `desk.py learn`.
"""
from __future__ import annotations

import logging
from typing import Dict

import numpy as np
import pandas as pd

from desk import memory
from desk.common import JOURNAL

RAW = JOURNAL / "backtest_raw.csv"
WINDOW = 300          # rows of history the screener needs (252-day high + 200-day average + margin)
log = logging.getLogger(__name__)


def generate(P: Dict[str, pd.DataFrame], max_new_days: int = 1000) -> int:
    """Run the screener on every day not yet in backtest_raw.csv. Returns the number of new signals."""
    from desk.analysis import market_regime, screen, sector_rotation

    c = P["close"]
    days = c["SPY"].dropna().index
    old = pd.read_csv(RAW) if RAW.exists() else pd.DataFrame()
    done = set(old["date"]) if len(old) else set()
    rows = []
    todo = [i for i in range(WINDOW, len(days)) if str(days[i].date()) not in done][:max_new_days]
    for n, i in enumerate(todo):
        d = days[i]
        sl = {f: P[f].loc[days[i - WINDOW]:d] for f in ("open", "high", "low", "close", "volume")}
        try:
            vix = float(c["^VIX"].loc[:d].dropna().iloc[-1]) if "^VIX" in c else 20.0
            vix3m = float(c["^VIX3M"].loc[:d].dropna().iloc[-1]) if "^VIX3M" in c else 0.0
            reg = market_regime(sl, vix, vix3m)["regime"]
            sec = sector_rotation(sl)
            s = screen(sl, sec, reg)
        except Exception as exc:  # pragma: no cover - data gaps on a given day
            log.warning("backtest %s skipped: %s", d.date(), exc)
            continue
        for r in s.itertuples():
            rows.append({"date": str(d.date()), "ticker": r.ticker, "direction": r.direction, "setups": "+".join(r.setups),
                         "entry_ref": round(r.entry, 4), "stop": round(r.stop, 4), "target": round(r.target, 4),
                         "reward_risk": round(r.reward_risk, 3), "score": r.score, "regime_rule": reg,
                         "sector": r.sector, "system_version": memory.current_version()})
    if rows or not RAW.exists():
        out = pd.concat([old, pd.DataFrame(rows)], ignore_index=True) if len(old) else pd.DataFrame(rows)
        out.to_csv(RAW, index=False)
    return len(rows)


def outcomes(P: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Simulate every backtest signal (entry at the next open after the signal day's close)."""
    from desk import learning, regimes

    if not RAW.exists():
        return pd.DataFrame()
    raw = pd.read_csv(RAW)
    res = [learning.simulate(P, s.ticker, s.direction, s.stop, s.target, s.date, same_day=False) for s in raw.itertuples()]
    out = pd.concat([raw, pd.DataFrame(res)], axis=1)
    h = regimes.history()
    if len(h):
        h = h.set_index("date")[["trend", "risk", "volatility"]]
        out = out.join(h, on="date")
    out["regime_trend"] = out.get("trend")
    out["month"] = out["date"].astype(str).str[:7]
    out["setup_list"] = out["setups"].astype(str).str.split("+")
    out["independent"] = learning.independent(out)
    return out
