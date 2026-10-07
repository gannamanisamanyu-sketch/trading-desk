"""Analysis engine: market regime, sector rotation and the stock screener.

Everything here is rule-based and explainable. The screener narrows ~200 stocks
to a short list of SETUPS with entry, stop and target levels; the analyst
(Claude, following CLAUDE.md) then checks news, fundamentals and options before
anything is ranked as a trade idea.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from desk.common import config, universe

REGIMES = ["STRONG BULL", "BULL", "NEUTRAL", "CHOPPY", "BEAR", "PANIC / RISK-OFF"]


# ------------------------------------------------------------------ indicators
def sma(x, n):
    return x.rolling(n, min_periods=n).mean()


def rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def atr(high, low, close, n: int = 14) -> pd.DataFrame:
    prev = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - prev).abs(), (low - prev).abs()))
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


# ------------------------------------------------------------------ regime
def market_regime(data: Dict[str, pd.DataFrame], vix: float, vix3m: float) -> Dict[str, object]:
    """Classify the market into one of six regimes with the evidence for it.

    Inputs: SPY trend (vs. 50/200-day averages), drawdown from 52-week high,
    breadth (% of universe above its 50-day average), VIX level and VIX term
    structure (VIX above VIX3M = inverted = stress).
    """
    c = data["close"]
    spy = c["SPY"].dropna()
    s50, s200 = sma(spy, 50).iloc[-1], sma(spy, 200).iloc[-1]
    last = spy.iloc[-1]
    dd = last / spy.iloc[-252:].max() - 1
    stocks = [t for t in universe().query("sector != 'ETF'")["ticker"] if t in c]
    above50 = (c[stocks].iloc[-1] > sma(c[stocks], 50).iloc[-1]).mean()
    ret_1m = last / spy.iloc[-22] - 1
    inverted = bool(vix3m and vix > vix3m)
    pts = 0
    pts += 2 if last > s50 > s200 else (1 if last > s200 else -2)
    pts += 1 if above50 > 0.6 else (-1 if above50 < 0.4 else 0)
    pts += 1 if vix < 16 else (-1 if vix > 22 else 0)
    pts += -2 if inverted else 0
    pts += 1 if ret_1m > 0.02 else (-1 if ret_1m < -0.03 else 0)
    if vix > 30 or (inverted and dd < -0.08):
        regime = "PANIC / RISK-OFF"
    elif last < s200 and dd < -0.10:
        regime = "BEAR"
    elif pts >= 5:
        regime = "STRONG BULL"
    elif pts >= 3:
        regime = "BULL"
    elif pts >= 1:
        regime = "NEUTRAL"
    elif pts >= -1:
        regime = "CHOPPY"
    else:
        regime = "BEAR"
    return {"regime": regime, "score": pts, "spy": float(last), "spy_vs_50d": float(last / s50 - 1),
            "spy_vs_200d": float(last / s200 - 1), "drawdown_from_high": float(dd),
            "breadth_above_50d": float(above50), "spy_1m": float(ret_1m), "vix": vix, "vix3m": vix3m,
            "vix_term_inverted": inverted, "as_of": str(spy.index[-1].date())}


# ------------------------------------------------------------------ sectors
def sector_rotation(data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Relative strength of each sector ETF vs. SPY over 1 and 3 months, and its change."""
    c = data["close"]
    spy = c["SPY"]
    rows = []
    for name, etf in config()["sector_etfs"].items():
        if etf not in c:
            continue
        s = c[etf].dropna()
        if len(s) < 70:      # not enough history (e.g. download failed): skip rather than crash
            continue
        rs = s / spy.reindex(s.index)
        r1 = float(rs.iloc[-1] / rs.iloc[-22] - 1)
        r3 = float(rs.iloc[-1] / rs.iloc[-64] - 1)
        r1_prev = float(rs.iloc[-22] / rs.iloc[-43] - 1)
        rows.append({"sector": name, "etf": etf, "ret_1m": float(s.iloc[-1] / s.iloc[-22] - 1),
                     "rs_1m": r1, "rs_3m": r3, "momentum_change": r1 - r1_prev,
                     "above_50d": bool(s.iloc[-1] > sma(s, 50).iloc[-1])})
    df = pd.DataFrame(rows)
    df["strength"] = 0.6 * df["rs_1m"].rank(pct=True) + 0.4 * df["rs_3m"].rank(pct=True)
    return df.sort_values("strength", ascending=False).reset_index(drop=True)


# ------------------------------------------------------------------ screener
SETUP_INFO = {
    "breakout": "New 55-day high on ≥1.5× average volume, in a confirmed uptrend",
    "pullback": "Uptrend (above rising 50/200-day) pulled back to the 20/50-day average with RSI 35-50",
    "rs_leader": "Top-decile 3-month relative strength, within 5% of its 52-week high",
    "oversold_uptrend": "RSI < 30 while still above the 200-day average (mean-reversion bounce)",
    "breakdown": "New 55-day low on heavy volume below falling 50/200-day averages (short / put idea)",
    "laggard_in_strong_sector": "Sector ETF strong over 1 month but the stock has not followed (catch-up / mispricing check)",
}


def screen(data: Dict[str, pd.DataFrame], sectors: pd.DataFrame, regime: str) -> pd.DataFrame:
    cfg = config()["screening"]
    c, h, l, v = data["close"], data["high"], data["low"], data["volume"]
    uni = universe().set_index("ticker")
    stocks = [t for t in uni.index if t in c and uni.loc[t, "sector"] != "ETF"]
    c, h, l, v = c[stocks], h[stocks], l[stocks], v[stocks]
    last = c.iloc[-1]
    s20, s50, s200 = sma(c, 20).iloc[-1], sma(c, 50).iloc[-1], sma(c, 200).iloc[-1]
    s50_prev = sma(c, 50).iloc[-11]
    a = atr(h, l, c).iloc[-1]
    r = rsi(c).iloc[-1]
    vol_ratio = v.iloc[-1] / v.iloc[-51:-1].mean()
    dollar_vol = (c * v).iloc[-50:].mean()
    hi55, lo55 = c.iloc[-56:-1].max(), c.iloc[-56:-1].min()
    hi252 = c.iloc[-252:].max()
    spy = data["close"]["SPY"]
    rs3 = (c.iloc[-1] / c.iloc[-64]) / (spy.iloc[-1] / spy.iloc[-64]) - 1
    ret1 = c.iloc[-1] / c.iloc[-2] - 1
    ret1m = c.iloc[-1] / c.iloc[-22] - 1
    sec_strength = sectors.set_index("etf")["strength"]
    sec_ret1m = sectors.set_index("etf")["ret_1m"]
    rows = []
    for t in stocks:
        if not np.isfinite(last[t]) or last[t] < cfg["min_price"] or dollar_vol[t] < cfg["min_dollar_volume"]:
            continue
        up = last[t] > s50[t] > s200[t] and s50[t] > s50_prev[t]
        down = last[t] < s50[t] < s200[t]
        setups = []
        if up and last[t] >= hi55[t] and vol_ratio[t] >= 1.5:
            setups.append("breakout")
        if up and abs(last[t] - min(s20[t], s50[t])) <= a[t] and 35 <= r[t] <= 50:
            setups.append("pullback")
        if rs3[t] >= rs3.quantile(0.9) and last[t] >= 0.95 * hi252[t]:
            setups.append("rs_leader")
        if r[t] < 30 and last[t] > s200[t]:
            setups.append("oversold_uptrend")
        if down and last[t] <= lo55[t] and vol_ratio[t] >= 1.5:
            setups.append("breakdown")
        etf = uni.loc[t, "sector_etf"]
        if etf in sec_ret1m and sec_ret1m[etf] > 0.04 and ret1m[t] < sec_ret1m[etf] - 0.06 and last[t] > s200[t]:
            setups.append("laggard_in_strong_sector")
        if not setups:
            continue
        direction = "SHORT" if setups == ["breakdown"] else "LONG"
        stop_dist = cfg["atr_stop_multiple"] * a[t]
        if direction == "LONG":
            entry, stop = last[t], last[t] - stop_dist
            target = max(entry + cfg["min_reward_risk"] * stop_dist, hi252[t] if hi252[t] > entry * 1.02 else 0)
        else:
            entry, stop = last[t], last[t] + stop_dist
            target = entry - cfg["min_reward_risk"] * stop_dist
        rr = abs(target - entry) / stop_dist
        # composite score: trend + relative strength + volume + sector + regime fit
        trend = 1.0 if up else (-1.0 if down else 0.0)
        regime_fit = {"STRONG BULL": 1, "BULL": 0.7, "NEUTRAL": 0.3, "CHOPPY": -0.2, "BEAR": -0.7,
                      "PANIC / RISK-OFF": -1}[regime] * (1 if direction == "LONG" else -1)
        sc = (2 * trend * (1 if direction == "LONG" else -1) + 3 * float(rs3.rank(pct=True)[t] - 0.5) * (1 if direction == "LONG" else -1)
              + min(vol_ratio[t], 3) / 3 + 2 * float(sec_strength.get(etf, 0.5) - 0.5) * (1 if direction == "LONG" else -1)
              + 1.5 * regime_fit + 0.5 * len(setups))
        rows.append({"ticker": t, "name": uni.loc[t, "name"], "sector": uni.loc[t, "sector"], "direction": direction,
                     "setups": setups, "price": float(last[t]), "entry": float(entry), "stop": float(stop),
                     "target": float(target), "reward_risk": float(rr), "atr_pct": float(a[t] / last[t]),
                     "rsi": float(r[t]), "rs_3m_vs_spy": float(rs3[t]), "vol_ratio": float(vol_ratio[t]),
                     "chg_1d": float(ret1[t]), "chg_1m": float(ret1m[t]), "pct_from_52w_high": float(last[t] / hi252[t] - 1),
                     "score": round(float(sc), 3), "as_of": str(c.index[-1].date())})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[df["reward_risk"] >= cfg["min_reward_risk"] - 1e-9]
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def fundamentals(ticker: str) -> Dict[str, object]:
    """Current fundamental snapshot from Yahoo Finance (point-in-time = today, fine for live use)."""
    import yfinance as yf

    tk = yf.Ticker(ticker)
    try:
        info = tk.info
    except Exception:
        info = {}
    keys = ["revenueGrowth", "earningsGrowth", "grossMargins", "operatingMargins", "freeCashflow", "totalCash",
            "totalDebt", "debtToEquity", "trailingPE", "forwardPE", "pegRatio", "priceToSalesTrailing12Months",
            "recommendationMean", "numberOfAnalystOpinions", "targetMeanPrice", "heldPercentInstitutions",
            "heldPercentInsiders", "shortPercentOfFloat", "shortRatio", "marketCap", "beta"]
    out = {k: info.get(k) for k in keys}
    try:
        cal = tk.calendar or {}
        ed = cal.get("Earnings Date")
        out["next_earnings"] = str(ed[0]) if isinstance(ed, list) and ed else (str(ed) if ed else None)
        out["eps_estimate"] = cal.get("Earnings Average")
    except Exception:
        out["next_earnings"] = None
    try:
        ue = tk.upgrades_downgrades
        if ue is not None and len(ue):
            ue = ue.reset_index().head(5)
            out["recent_rating_changes"] = ue.to_dict("records")
    except Exception:
        pass
    return out
