"""Market-regime database: a composite, multi-indicator classification for every trading day.

Each day is classified with data available up to that day only (point-in-time):
  TREND      Strong Bull · Bull · Neutral · Choppy · Bear · Strong Bear
             (S&P 500, Nasdaq-100, Russell 2000 trends; breadth; drawdown; 1-month momentum)
  RISK       Risk-On · Mixed · Risk-Off
             (credit spreads, VIX term structure, small caps vs large, dollar, gold vs stocks, sector leadership)
  VOLATILITY Low · Normal · High (VIX level and SPY 20-day realized volatility)
plus the raw conditions (yields, curve, dollar, credit, oil, gold, sector leaders, Fed proxy) so any day can be
compared with any other. No single indicator decides a label; every component is stored for audit.

FRED daily series are lagged one day (they publish after the close). Monthly economic releases are not used in the
daily composite because their release dates are not point-in-time in this data; they stay in the scan's Economy table.
Output: journal/regime_history.csv (rebuilt by `desk.py learn`; earlier days never change once computed).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from desk.common import DATA, JOURNAL, config, now_ny, universe

HISTORY = JOURNAL / "regime_history.csv"
PRICE_CACHE = DATA / "regime_daily.pkl"
MARKET = {"SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM", "VIX": "^VIX", "VIX3M": "^VIX3M", "DXY": "DX-Y.NYB",
          "OIL": "CL=F", "GOLD": "GC=F", "TNX": "^TNX"}
FRED_IDS = ["DFF", "DGS2", "DGS10", "T10Y2Y", "BAMLH0A0HYM2"]
DEFENSIVE = {"XLU", "XLP", "XLV", "XLRE"}
CYCLICAL = {"XLK", "SMH", "XLY", "XLI", "XLF", "XLB", "XLE", "IGV", "XHB", "KRE"}
FEATURES = ["spy_trend", "qqq_trend", "iwm_trend", "breadth_50d", "spy_drawdown", "spy_1m", "vix", "vix_term",
            "rv20", "tnx", "tnx_1m_bp", "curve_10y2y", "dxy_1m", "hy_spread", "hy_1m", "oil_1m", "gold_1m"]


def _trend(s: pd.Series) -> pd.Series:
    """+2 above a rising 50d that is above the 200d · +1 above 200d · -1 below 200d · -2 below a falling 50d < 200d."""
    s50, s200 = s.rolling(50).mean(), s.rolling(200).mean()
    rising, falling = s50 > s50.shift(10), s50 < s50.shift(10)
    out = pd.Series(np.where((s > s50) & (s50 > s200) & rising, 2,
                    np.where(s > s200, 1, np.where((s < s50) & (s50 < s200) & falling, -2, -1))), index=s.index, dtype=float)
    return out.where(s200.notna())


def load_prices(period: str = "3y", refresh: bool = False) -> Dict[str, pd.DataFrame]:
    """Daily bars for the market symbols, sector ETFs and the stock universe (cached for the day)."""
    from desk.market import download_daily

    if PRICE_CACHE.exists() and not refresh and \
            pd.Timestamp(PRICE_CACHE.stat().st_mtime, unit="s").date() == now_ny().date():
        return pd.read_pickle(PRICE_CACHE)
    tick = sorted(set(MARKET.values()) | set(config()["sector_etfs"].values()) | set(universe()["ticker"]))
    data = download_daily(tick, period)
    DATA.mkdir(exist_ok=True)
    pd.to_pickle(data, PRICE_CACHE)
    return data


def _fred(start: str) -> pd.DataFrame:
    from desk.macro import fetch_fred

    try:
        f = fetch_fred(FRED_IDS, start)
        f.index = pd.to_datetime(f.index)
        return f.sort_index().shift(1, freq="D")          # published after the close: usable the next day
    except Exception:  # pragma: no cover - network
        return pd.DataFrame(columns=FRED_IDS)


def build(P: Optional[Dict[str, pd.DataFrame]] = None) -> pd.DataFrame:
    P = P or load_prices()
    c = P["close"]
    m = {k: c[v] for k, v in MARKET.items() if v in c}
    idx = m["SPY"].dropna().index
    df = pd.DataFrame(index=idx)
    for k in ("SPY", "QQQ", "IWM"):
        df[f"{k.lower()}_trend"] = _trend(m[k].reindex(idx).ffill())
    spy = m["SPY"].reindex(idx)
    df["spy"] = spy
    df["spy_drawdown"] = spy / spy.rolling(252, min_periods=60).max() - 1
    df["spy_1m"] = spy / spy.shift(21) - 1
    df["rv20"] = spy.pct_change().rolling(20).std() * math.sqrt(252)
    stocks = [t for t in universe().query("sector != 'ETF'")["ticker"] if t in c]
    cs = c[stocks].reindex(idx)
    df["breadth_50d"] = (cs > cs.rolling(50).mean()).sum(axis=1) / cs.rolling(50).mean().notna().sum(axis=1).replace(0, np.nan)
    df["vix"] = m["VIX"].reindex(idx).ffill()
    df["vix_term"] = df["vix"] / m["VIX3M"].reindex(idx).ffill()
    tnx = m["TNX"].reindex(idx).ffill()
    tnx = tnx.where(tnx < 20, tnx / 10)
    df["tnx"], df["tnx_1m_bp"] = tnx, (tnx - tnx.shift(21)) * 100
    for k, col in (("DXY", "dxy_1m"), ("OIL", "oil_1m"), ("GOLD", "gold_1m")):
        s = m[k].reindex(idx).ffill()
        df[col] = s / s.shift(21) - 1
    df["iwm_vs_spy_1m"] = (m["IWM"].reindex(idx) / m["IWM"].reindex(idx).shift(21)) - (1 + df["spy_1m"])
    fr = _fred(str((idx[0] - pd.Timedelta(days=10)).date()))
    fr = fr.ffill().reindex(fr.index.union(idx)).ffill().reindex(idx)
    df["fed_funds"], df["two_year"] = fr.get("DFF"), fr.get("DGS2")
    df["curve_10y2y"], df["hy_spread"] = fr.get("T10Y2Y"), fr.get("BAMLH0A0HYM2")
    df["hy_1m"] = df["hy_spread"] - df["hy_spread"].shift(21)
    df["fed_proxy_2y_minus_ff"] = df["two_year"] - df["fed_funds"]
    # sector leadership: top 3 sector ETFs by 1-month return relative to SPY
    etfs = {v: k for k, v in config()["sector_etfs"].items() if v in c}
    rel = pd.DataFrame({e: c[e].reindex(idx) / c[e].reindex(idx).shift(21) - (1 + df["spy_1m"]) for e in etfs})
    df["leaders"] = rel.apply(lambda r: ",".join(r.dropna().sort_values(ascending=False).index[:3]), axis=1)
    df["laggards"] = rel.apply(lambda r: ",".join(r.dropna().sort_values().index[:3]), axis=1)
    lead_def = rel.apply(lambda r: sum(e in DEFENSIVE for e in r.dropna().sort_values(ascending=False).index[:3]), axis=1)
    lead_cyc = rel.apply(lambda r: sum(e in CYCLICAL for e in r.dropna().sort_values(ascending=False).index[:3]), axis=1)

    # ---- composite TREND
    tp = (1.5 * df["spy_trend"] + 0.75 * df["qqq_trend"] + 0.75 * df["iwm_trend"]
          + np.where(df["breadth_50d"] > 0.6, 1, np.where(df["breadth_50d"] < 0.4, -1, 0))
          + np.where(df["spy_1m"] > 0.02, 1, np.where(df["spy_1m"] < -0.03, -1, 0)))
    df["trend_points"] = tp
    df["trend"] = np.select([(df["spy_drawdown"] <= -0.20) | (tp <= -5), tp <= -1.5, tp < 0.5, tp < 2.5, tp < 5],
                            ["Strong Bear", "Bear", "Choppy", "Neutral", "Bull"], "Strong Bull")
    # ---- composite RISK
    rp = (np.where(df["hy_1m"] < -0.1, 1, np.where(df["hy_1m"] > 0.3, -1, 0))
          + np.where(df["vix_term"] < 0.9, 1, np.where(df["vix_term"] > 1.0, -2, 0))
          + np.where(df["iwm_vs_spy_1m"] > 0, 1, np.where(df["iwm_vs_spy_1m"] < -0.03, -1, 0))
          + np.where(df["dxy_1m"] > 0.02, -1, 0)
          + np.where((df["gold_1m"] > 0.05) & (df["spy_1m"] < 0), -1, 0)
          + np.where(lead_cyc >= 2, 1, np.where(lead_def >= 2, -1, 0)))
    df["risk_points"] = rp
    df["risk"] = np.select([rp >= 2, rp <= -2], ["Risk-On", "Risk-Off"], "Mixed")
    # ---- VOLATILITY
    df["volatility"] = np.select([(df["vix"] > 22) | (df["rv20"] > 0.20), (df["vix"] < 15) & (df["rv20"] < 0.12)],
                                 ["High Volatility", "Low Volatility"], "Normal Volatility")
    df["fed_signal"] = np.select([df["fed_proxy_2y_minus_ff"] > 0.25, df["fed_proxy_2y_minus_ff"] < -0.25],
                                 ["hikes priced", "cuts priced"], "on hold")
    df = df[df["spy_trend"].notna()].copy()
    df.index.name = "date"
    df.index = df.index.strftime("%Y-%m-%d")
    out = df.round(5).reset_index()
    out.to_csv(HISTORY, index=False)
    return out


def history() -> pd.DataFrame:
    return pd.read_csv(HISTORY) if HISTORY.exists() else pd.DataFrame()


def on(date: str, h: Optional[pd.DataFrame] = None) -> Dict[str, object]:
    """Composite regime in force on a date (the last classified day on or before it)."""
    h = history() if h is None else h
    if h.empty:
        return {}
    r = h[h["date"] <= str(date)[:10]]
    return r.iloc[-1].to_dict() if len(r) else {}


def similar_days(date: Optional[str] = None, k: int = 30, exclude_recent: int = 20, h: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """The k past days whose conditions were closest to `date` (z-scored features, Euclidean distance).

    Days within `exclude_recent` sessions of the target are excluded so overlapping windows don't count as 'similar'."""
    h = history() if h is None else h
    if h.empty:
        return h
    h = h.dropna(subset=["spy_trend"]).reset_index(drop=True)
    tgt = h[h["date"] <= (date or h["date"].iloc[-1])].index
    if not len(tgt):
        return pd.DataFrame()
    t = tgt[-1]
    X = h[FEATURES].astype(float)
    Z = (X - X.mean()) / X.std().replace(0, np.nan)
    Z = Z.fillna(0)
    d = np.sqrt(((Z - Z.loc[t]) ** 2).sum(axis=1))
    cand = h.loc[: max(t - exclude_recent, -1)].assign(distance=d)
    return cand.nsmallest(k, "distance")
