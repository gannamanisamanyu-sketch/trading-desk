"""Economic-data pipeline (FRED, free, no API key) and a Fed-path PROXY.

What FRED gives us: the latest released values of CPI, payrolls, claims, GDP,
retail sales, sentiment, housing, yields, credit spreads, breakevens.
What it does NOT give us: consensus forecasts or the release calendar. The
morning playbook (CLAUDE.md) fills those in with a web search, labeled as such.

Fed path: CME FedWatch probabilities are not freely available, so we use a proxy:
the 2-year Treasury yield minus the effective fed funds rate. The 2-year yield
reflects the market's expected average policy rate over two years:
    2Y - fed funds < -0.25%  → market expects CUTS
    2Y - fed funds > +0.25%  → market expects HIKES
    otherwise                 → roughly NO CHANGE
This is a direction-of-travel signal, NOT a meeting-by-meeting probability.
"""
from __future__ import annotations

import io
import logging
from typing import Dict

import numpy as np
import pandas as pd
import requests

from desk.common import http_get

log = logging.getLogger(__name__)

# series id → (label, transform) ; transform: level | yoy | mom | diff (change in level, e.g. payrolls)
SERIES: Dict[str, tuple] = {
    "CPIAUCSL": ("CPI inflation (YoY)", "yoy"),
    "CPILFESL": ("Core CPI inflation (YoY)", "yoy"),
    "PPIFIS": ("PPI final demand (YoY)", "yoy"),
    "PCEPILFE": ("Core PCE inflation (YoY)", "yoy"),
    "PAYEMS": ("Nonfarm payrolls (monthly change, thousands)", "diff"),
    "UNRATE": ("Unemployment rate", "level"),
    "ICSA": ("Initial jobless claims", "level"),
    "CES0500000003": ("Average hourly earnings (YoY)", "yoy"),
    "GDPC1": ("Real GDP (QoQ annualized)", "qoq_ann"),
    "RSAFS": ("Retail sales (MoM)", "mom"),
    "UMCSENT": ("UMich consumer sentiment", "level"),
    "HOUST": ("Housing starts (thousands, annualized)", "level"),
    "INDPRO": ("Industrial production (YoY)", "yoy"),
    "PCEC96": ("Real consumer spending (YoY)", "yoy"),
    "T5YIE": ("5-year breakeven inflation", "level"),
    "DFF": ("Effective fed funds rate", "level"),
    "DGS2": ("2-year Treasury yield", "level"),
    "DGS10": ("10-year Treasury yield", "level"),
    "T10Y2Y": ("Yield curve (10Y − 2Y)", "level"),
    "BAMLH0A0HYM2": ("High-yield credit spread (OAS)", "level"),
}


def _fetch_one(sid: str, start: str) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
    last_exc = None
    for _ in range(3):
        try:
            r = requests.get(url, timeout=25)  # FRED blocks browser-like and custom user agents; the default works
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
            s.index = pd.to_datetime(df.iloc[:, 0])
            return s.rename(sid)
        except Exception as exc:  # pragma: no cover - network
            last_exc = exc
    log.warning("FRED %s unavailable: %s", sid, last_exc)
    return pd.Series(dtype=float, name=sid)


def fetch_fred(ids, start: str = "2023-01-01") -> pd.DataFrame:
    """One small request per series (in parallel): FRED times out on large combined requests."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as ex:
        series = list(ex.map(lambda i: _fetch_one(i, start), ids))
    return pd.concat([s for s in series if len(s)], axis=1)


def _transform(s: pd.Series, how: str) -> pd.Series:
    s = s.dropna()
    if how == "yoy":
        freq = 52 if (s.index.to_series().diff().dt.days.median() or 30) < 10 else 12
        return s.pct_change(freq) * 100
    if how == "mom":
        return s.pct_change() * 100
    if how == "diff":
        return s.diff()
    if how == "qoq_ann":
        return ((s / s.shift(1)) ** 4 - 1) * 100
    return s


def economic_dashboard() -> pd.DataFrame:
    """Latest value, previous value and date for each series (after transformation)."""
    raw = fetch_fred(list(SERIES))
    rows = []
    for sid, (label, how) in SERIES.items():
        if sid not in raw:
            continue
        t = _transform(raw[sid], how).dropna()
        if t.empty:
            continue
        rows.append({"series": sid, "indicator": label, "latest": float(t.iloc[-1]),
                     "previous": float(t.iloc[-2]) if len(t) > 1 else np.nan,
                     "change": float(t.iloc[-1] - t.iloc[-2]) if len(t) > 1 else np.nan,
                     "as_of": str(t.index[-1].date())})
    return pd.DataFrame(rows)


def fed_path_proxy(dash: pd.DataFrame) -> Dict[str, object]:
    d = dash.set_index("series")["latest"]
    if "DGS2" not in d or "DFF" not in d:
        return {"signal": "unavailable"}
    gap = float(d["DGS2"] - d["DFF"])
    if gap < -0.25:
        sig = "CUTS priced (2Y below fed funds)"
    elif gap > 0.25:
        sig = "HIKES priced (2Y above fed funds)"
    else:
        sig = "roughly NO CHANGE priced"
    curve = float(d.get("T10Y2Y", np.nan))
    hy = float(d.get("BAMLH0A0HYM2", np.nan))
    return {"two_year_minus_fed_funds": gap, "signal": sig, "fed_funds": float(d["DFF"]),
            "two_year": float(d["DGS2"]), "curve_10y_2y": curve, "hy_spread": hy,
            "credit_stress": "elevated" if hy > 5 else ("normal" if hy > 3 else "tight / risk-on"),
            "note": "Proxy from 2Y yield vs. fed funds; not CME FedWatch meeting probabilities."}
