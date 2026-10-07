"""Market-data pipeline: daily OHLCV for the universe, cross-asset dashboard, quotes.

Source: Yahoo Finance via yfinance (free). Intraday quotes are typically delayed
~15 minutes; every value carries the timestamp of the bar/quote it came from.
"""
from __future__ import annotations

import logging
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

from desk.common import DATA, config, stamp, universe

log = logging.getLogger(__name__)


def download_daily(tickers: List[str], period: str = "1y") -> Dict[str, pd.DataFrame]:
    """Adjusted daily OHLCV as {field: DataFrame(date x ticker)}."""
    raw = yf.download(tickers, period=period, auto_adjust=True, progress=False, group_by="column", threads=True)
    if raw.empty:
        raise RuntimeError("No market data returned (network problem?)")
    out = {}
    for f in ("Open", "High", "Low", "Close", "Volume"):
        df = raw[f]
        if isinstance(df, pd.Series):
            df = df.to_frame(tickers[0])
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[f.lower()] = df
    return out


def load_universe_data(refresh: bool = True) -> Dict[str, pd.DataFrame]:
    cache = DATA / "universe_daily.pkl"
    if cache.exists() and not refresh:
        return pd.read_pickle(cache)
    tick = list(universe()["ticker"])
    extra = list(config()["sector_etfs"].values())
    data = download_daily(sorted(set(tick + extra + ["SPY"])), config()["data"]["history_period"])
    DATA.mkdir(exist_ok=True)
    pd.to_pickle(data, cache)
    return data


def cross_asset_snapshot() -> pd.DataFrame:
    """Level and 1-day / 5-day / 1-month change for indices, rates, FX, commodities, crypto."""
    ca = config()["cross_asset"]
    syms = list(ca.values())
    raw = yf.download(syms, period="3mo", auto_adjust=False, progress=False, group_by="column", threads=True)["Close"]
    raw.index = pd.to_datetime(raw.index)
    rows = []
    for name, sym in ca.items():
        s = raw[sym].dropna() if sym in raw else pd.Series(dtype=float)
        if s.empty:
            rows.append({"asset": name, "symbol": sym, "last": np.nan, "as_of": "unavailable"})
            continue
        last = float(s.iloc[-1])
        is_yield = sym in ("^TNX", "^FVX", "^IRX")
        if is_yield:
            last = last / (10 if sym in ("^TNX", "^FVX") and last > 20 else 1)  # Yahoo quotes some yields x10
        def chg(n):
            if len(s) <= n:
                return np.nan
            a, b = float(s.iloc[-1]), float(s.iloc[-1 - n])
            return (a - b) if is_yield else (a / b - 1)
        rows.append({"asset": name, "symbol": sym, "last": last, "chg_1d": chg(1), "chg_5d": chg(5),
                     "chg_1m": chg(21), "is_yield": is_yield, "as_of": str(s.index[-1].date())})
    return pd.DataFrame(rows)


def quote(tickers: List[str]) -> pd.DataFrame:
    """Latest available price per ticker with the time of that price (delayed, from Yahoo)."""
    rows = []
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            h = tk.history(period="1d", interval="1m", prepost=True)
            if h.empty:
                h = tk.history(period="5d")
            px, ts = float(h["Close"].iloc[-1]), h.index[-1]
            rows.append({"ticker": t, "price": px, "time": stamp(ts.to_pydatetime())})
        except Exception as exc:  # pragma: no cover - network
            rows.append({"ticker": t, "price": np.nan, "time": f"unavailable ({exc.__class__.__name__})"})
    return pd.DataFrame(rows)
