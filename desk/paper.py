"""Paper-trading engine and portfolio tracker ($30,000 simulated account).

Every recommendation the desk makes is recorded here BEFORE anyone risks real
money: date, ticker, trade, entry, stop, target, size, reason, catalyst,
expected return, and later the actual result. The point is to find out which
setups actually work.

Mechanics (simplified, documented):
  * Stocks: each update checks the daily HIGH/LOW since entry. If the stop and the
    target are both touched on the same day, the STOP is assumed to hit first (conservative).
  * Options: historical option prices are not available for free, so options are
    marked at the CURRENT chain price each time `update` runs. If the underlying
    touched its stop/target, the option is closed at that day's mark. At expiry it
    is valued at intrinsic value.
  * No commissions; options are filled at the quoted price (mid or last).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from desk.common import JOURNAL, config, now_ny
from desk.risk import portfolio_check

LEDGER = JOURNAL / "ledger.json"
EQUITY = JOURNAL / "equity_history.csv"


# ------------------------------------------------------------------ storage
def load() -> Dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"starting_cash": config()["account"]["starting_cash"], "created": str(now_ny().date()), "trades": []}


def save(book: Dict) -> None:
    JOURNAL.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, indent=2, default=str))
    tmp.replace(LEDGER)


def _next_id(book: Dict) -> int:
    return max([t["id"] for t in book["trades"]], default=0) + 1


# ------------------------------------------------------------------ account
def account(book: Dict, marks: Optional[Dict[int, float]] = None) -> Dict[str, float]:
    """Cash, open market value, equity, open risk, options exposure."""
    start = book["starting_cash"]
    realized = sum(t.get("pnl", 0) for t in book["trades"] if t["status"] == "closed")
    cash = start + realized
    mv = unreal = risk = opt = 0.0
    for t in book["trades"]:
        if t["status"] != "open":
            continue
        cost = t["cost"]
        cash -= cost
        mark = (marks or {}).get(t["id"], t.get("last_mark", t["entry_price"]))
        value = _position_value(t, mark)
        mv += value
        unreal += value - cost   # short stock: value already includes (entry - mark) x qty
        risk += t["risk"]
        if t["instrument"] == "option":
            opt += t["risk"]
    equity = start + realized + unreal
    return {"starting_cash": start, "cash": cash, "open_market_value": mv, "realized_pnl": realized,
            "unrealized_pnl": unreal, "equity": equity, "open_risk": risk, "options_exposure": opt,
            "total_return": equity / start - 1}


def _position_value(t: Dict, mark: float) -> float:
    mult = 100 if t["instrument"] == "option" else 1
    if t["instrument"] == "stock" and t["direction"] == "SHORT":
        return t["cost"] + (t["entry_price"] - mark) * t["quantity"]   # collateral + P&L
    return mark * t["quantity"] * mult


# ------------------------------------------------------------------ open / close
def add_trade(ticker: str, instrument: str, direction: str, quantity: int, entry_price: float, stop: float,
              target: float, reason: str, catalyst: str, setup: str, regime: str, confidence: str,
              risk: float, expected_return: float, option: Optional[Dict] = None, notes: str = "",
              extra: Optional[Dict] = None) -> Dict:
    """Record a recommendation as an open paper position. Prices: stock price, or option debit per share."""
    book = load()
    mult = 100 if instrument == "option" else 1
    cost = entry_price * quantity * mult
    acct = account(book)
    if cost > acct["cash"] + 1e-6:
        raise ValueError(f"not enough paper cash: need ${cost:,.0f}, have ${acct['cash']:,.0f}")
    t = {"id": _next_id(book), "opened": now_ny().strftime("%Y-%m-%d %H:%M"), "ticker": ticker.upper(),
         "instrument": instrument, "direction": direction.upper(), "quantity": int(quantity),
         "entry_price": float(entry_price), "stop": float(stop), "target": float(target), "cost": float(cost),
         "risk": float(risk), "expected_return": float(expected_return), "reason": reason, "catalyst": catalyst,
         "setup": setup, "regime": regime, "confidence": confidence, "option": option or {}, "notes": notes,
         "status": "open", "last_mark": float(entry_price), **(extra or {})}
    issues = portfolio_check(acct["equity"], [x for x in book["trades"] if x["status"] == "open"] + [
        {**t, "market_value": cost}])
    if issues:
        raise ValueError("risk limits would be breached: " + "; ".join(issues))
    book["trades"].append(t)
    save(book)
    return t


def close_trade(book: Dict, t: Dict, exit_price: float, reason: str, when: Optional[str] = None) -> None:
    mult = 100 if t["instrument"] == "option" else 1
    if t["instrument"] == "stock" and t["direction"] == "SHORT":
        pnl = (t["entry_price"] - exit_price) * t["quantity"]
    else:
        pnl = (exit_price - t["entry_price"]) * t["quantity"] * mult
    t.update({"status": "closed", "exit_price": float(exit_price), "closed": when or now_ny().strftime("%Y-%m-%d %H:%M"),
              "exit_reason": reason, "pnl": float(pnl), "return_pct": float(pnl / t["cost"]) if t["cost"] else 0.0,
              "r_multiple": float(pnl / t["risk"]) if t["risk"] else 0.0,
              "holding_days": (pd.Timestamp(when or now_ny().date()) - pd.Timestamp(t["opened"][:10])).days})


def _option_mark(opt: Dict, spot_close: float) -> Optional[float]:
    """Current price of the position per share (long leg minus short leg)."""
    exp = opt.get("expiry")
    if exp and date.fromisoformat(exp) < now_ny().date():
        call = opt.get("type", "call") == "call"
        intr = lambda k: max(0.0, (spot_close - k) if call else (k - spot_close))
        return intr(opt["long_strike"]) - (intr(opt["short_strike"]) if opt.get("short_strike") else 0.0)
    try:
        tk = yf.Ticker(opt["underlying"])
        ch = tk.option_chain(exp)
        tab = pd.concat([ch.calls, ch.puts]).set_index("contractSymbol")

        def px(sym):
            r = tab.loc[sym]
            return (r.bid + r.ask) / 2 if r.bid > 0 and r.ask > 0 else r.lastPrice
        val = px(opt["contract"])
        if opt.get("short_contract"):
            val -= px(opt["short_contract"])
        return float(val)
    except Exception:
        return None


def update(book: Optional[Dict] = None) -> Dict:
    """Mark every open position; close those whose stop/target/expiry was reached. Append equity history."""
    book = book or load()
    open_trades = [t for t in book["trades"] if t["status"] == "open"]
    tickers = sorted({t["ticker"] for t in open_trades})
    events = []
    if tickers:
        hist = yf.download(tickers, period="3mo", auto_adjust=False, progress=False, group_by="column")
        for t in open_trades:
            h = hist["High"][t["ticker"]] if len(tickers) > 1 else hist["High"].squeeze()
            l = hist["Low"][t["ticker"]] if len(tickers) > 1 else hist["Low"].squeeze()
            c = hist["Close"][t["ticker"]] if len(tickers) > 1 else hist["Close"].squeeze()
            since = pd.Timestamp(t["opened"][:10])
            c = c.dropna()
            # stops/targets are checked from the day AFTER entry: the entry day's earlier
            # high/low happened before the position existed
            h, l = h[h.index > since].dropna(), l[l.index > since].dropna()
            if c.empty:
                continue
            hit = None
            for d in h.index:
                long_ = t["direction"] == "LONG"
                stop_hit = (l[d] <= t["stop"]) if long_ else (h[d] >= t["stop"])
                tgt_hit = (h[d] >= t["target"]) if long_ else (l[d] <= t["target"])
                if stop_hit:
                    hit = ("stop", d)
                    break
                if tgt_hit:
                    hit = ("target", d)
                    break
            spot = float(c.iloc[-1])
            if t["instrument"] == "stock":
                if hit:
                    close_trade(book, t, t["stop"] if hit[0] == "stop" else t["target"], hit[0], str(hit[1].date()))
                    events.append(f"#{t['id']} {t['ticker']} closed at {hit[0]}")
                else:
                    t["last_mark"] = spot
            else:
                mark = _option_mark(t["option"], spot)
                if mark is None:
                    continue
                t["last_mark"] = mark
                expired = date.fromisoformat(t["option"]["expiry"]) < now_ny().date()
                if hit or expired:
                    why = "expiry" if expired else f"underlying {hit[0]}"
                    close_trade(book, t, mark, why)
                    events.append(f"#{t['id']} {t['ticker']} option closed ({why}) at {mark:.2f}")
    save(book)
    acct = account(book)
    EQUITY.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([{"date": str(now_ny().date()), **{k: round(v, 2) for k, v in acct.items()}}])
    if EQUITY.exists():
        old = pd.read_csv(EQUITY)
        old = old[old["date"] != row["date"].iloc[0]]
        row = pd.concat([old, row])
    row.to_csv(EQUITY, index=False)
    return {"account": acct, "events": events}


# ------------------------------------------------------------------ statistics
def statistics(book: Optional[Dict] = None) -> Dict[str, object]:
    """Performance of CLOSED paper trades, overall and by setup / regime / instrument."""
    book = book or load()
    closed = pd.DataFrame([t for t in book["trades"] if t["status"] == "closed"])
    out: Dict[str, object] = {"closed_trades": int(len(closed)),
                              "open_trades": sum(t["status"] == "open" for t in book["trades"])}
    if closed.empty:
        out["note"] = "no closed trades yet"
        return out
    wins, losses = closed[closed.pnl > 0], closed[closed.pnl <= 0]
    gross_loss = -losses.pnl.sum()
    out.update({
        "win_rate": len(wins) / len(closed), "avg_win": float(wins.pnl.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.pnl.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.pnl.sum() / gross_loss) if gross_loss > 0 else math.inf,
        "expected_value_per_trade": float(closed.pnl.mean()), "avg_r_multiple": float(closed.r_multiple.mean()),
        "total_pnl": float(closed.pnl.sum()), "avg_holding_days": float(closed.holding_days.mean()),
    })
    if EQUITY.exists():
        eq = pd.read_csv(EQUITY)["equity"]
        out["max_drawdown"] = float((eq / eq.cummax() - 1).min())
        out["return"] = float(eq.iloc[-1] / book["starting_cash"] - 1)
        r = eq.pct_change().dropna()
        out["sharpe"] = float(r.mean() / r.std() * np.sqrt(252)) if len(r) >= 30 and r.std() > 0 else None
        if out["sharpe"] is None:
            out["sharpe_note"] = "needs ≥ 30 daily observations to be meaningful"
    for col in ("setup", "regime", "instrument", "direction"):
        g = closed.groupby(col)["pnl"]
        out[f"by_{col}"] = pd.DataFrame({"trades": g.size(), "win_rate": g.apply(lambda s: (s > 0).mean()),
                                         "total_pnl": g.sum(), "avg_pnl": g.mean()}).round(3).to_dict("index")
    n = len(closed)
    out["milestone"] = f"{n // 20 * 20}-trade review due" if n >= 20 and n % 20 == 0 else f"next review at {(n // 20 + 1) * 20} closed trades"
    return out
