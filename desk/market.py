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


def _batch(tickers: List[str], period: str) -> pd.DataFrame:
    return yf.download(tickers, period=period, auto_adjust=True, progress=False, group_by="column", threads=True)


def download_daily(tickers: List[str], period: str = "1y", chunk: int = 60, retries: int = 3) -> Dict[str, pd.DataFrame]:
    """Adjusted daily OHLCV as {field: DataFrame(date x ticker)}.

    Downloads in chunks, then retries any ticker that came back empty one at a time
    with a pause: Yahoo rate-limits shared cloud IPs (e.g. GitHub Actions) under load.
    """
    import time

    frames = []
    for i in range(0, len(tickers), chunk):
        raw = _batch(tickers[i:i + chunk], period)
        if not raw.empty:
            frames.append(raw)
        time.sleep(1)
    if not frames:
        raise RuntimeError("No market data returned (network problem or rate limit)")
    raw = pd.concat(frames, axis=1)
    for attempt in range(retries):
        close = raw["Close"] if "Close" in raw else pd.DataFrame()
        missing = [t for t in tickers if t not in close or close[t].dropna().empty]
        if not missing:
            break
        log.warning("retrying %d tickers after empty download: %s", len(missing), missing[:10])
        time.sleep(5 * (attempt + 1))
        for t in missing:
            one = _batch([t], period)
            if not one.empty:
                raw = raw.drop(columns=[c for c in raw.columns if c[1] == t], errors="ignore")
                if not isinstance(one.columns, pd.MultiIndex):
                    one.columns = pd.MultiIndex.from_product([one.columns, [t]])
                raw = pd.concat([raw, one], axis=1)
            time.sleep(1)
    out = {}
    for f in ("Open", "High", "Low", "Close", "Volume"):
        df = raw[f]
        if isinstance(df, pd.Series):
            df = df.to_frame(tickers[0])
        df = df.loc[:, ~df.columns.duplicated()]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[f.lower()] = df.sort_index()
    still = [t for t in tickers if t not in out["close"] or out["close"][t].dropna().empty]
    if still:
        log.warning("no data after retries for: %s", still)
    return repair_last_bar(out, tickers)


DATA_GAPS: List[str] = []


def repair_last_bar(out: Dict[str, pd.DataFrame], tickers: List[str], chunk: int = 80) -> Dict[str, pd.DataFrame]:
    """Yahoo sometimes returns the latest daily bar with volume but blank prices (seen 2026-11-02).

    Rebuild that day from 5-minute bars; if that fails, drop the row and record a data gap, so a blank
    row can never masquerade as 'every stock missing' (which silently empties the screener and breadth)."""
    c = out["close"]
    if c.empty:
        return out
    last = c.index[-1]
    if c.iloc[-1].isna().mean() < 0.5:
        return out
    day = last.date()
    rebuilt = {f: {} for f in ("open", "high", "low", "close", "volume")}
    names = [t for t in tickers if t in c.columns]
    for i in range(0, len(names), chunk):
        try:
            raw = yf.download(names[i:i + chunk], period="5d", interval="5m", auto_adjust=True, progress=False,
                              group_by="column", threads=True, prepost=False)
        except Exception:  # pragma: no cover - network
            continue
        if raw.empty:
            continue
        idx = raw.index.tz_convert("America/New_York") if raw.index.tz is not None else raw.index
        raw.index = idx
        today = raw[[d.date() == day for d in raw.index]]
        for t in names[i:i + chunk]:
            try:
                cl = today["Close"][t].dropna()
                if cl.empty:
                    continue
                rebuilt["open"][t] = float(today["Open"][t].dropna().iloc[0])
                rebuilt["high"][t] = float(today["High"][t].max())
                rebuilt["low"][t] = float(today["Low"][t].min())
                rebuilt["close"][t] = float(cl.iloc[-1])
                rebuilt["volume"][t] = float(today["Volume"][t].sum())
            except Exception:
                continue
    got = len(rebuilt["close"])
    if got >= 0.8 * len(names):
        for f in ("open", "high", "low", "close"):
            for t, v in rebuilt[f].items():
                out[f].loc[last, t] = v
        vol = out["volume"]                    # Yahoo's daily volume was intact; keep it, fill only true gaps
        for t, v in rebuilt["volume"].items():
            if pd.isna(vol.loc[last, t]) or vol.loc[last, t] == 0:
                vol.loc[last, t] = v
        msg = (f"Yahoo daily bar for {day} had blank prices; rebuilt from 5-minute bars for {got}/{len(names)} "
               f"tickers (regular session only); Yahoo's daily volume kept")
    else:
        for f in out:
            out[f] = out[f].iloc[:-1]
        msg = f"Yahoo daily bar for {day} had blank prices and could not be rebuilt; using data through the prior day"
    log.warning(msg)
    DATA_GAPS.append(msg)
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
