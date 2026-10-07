"""Shared helpers: configuration, paths, timestamps, HTTP."""
from __future__ import annotations

import logging
import warnings
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yaml

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JOURNAL = ROOT / "journal"
REPORTS = ROOT / "reports"
NY = ZoneInfo("America/New_York")
log = logging.getLogger("desk")
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


@lru_cache(maxsize=1)
def config() -> Dict[str, Any]:
    with open(ROOT / "config" / "desk.yaml") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def universe() -> pd.DataFrame:
    return pd.read_csv(ROOT / "config" / "universe.csv")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_ny() -> datetime:
    return datetime.now(NY)


def stamp(ts: datetime | pd.Timestamp | None = None) -> str:
    """Human timestamp in New York time, e.g. '2026-10-07 09:45 ET'."""
    ts = ts or now_utc()
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(NY).strftime("%Y-%m-%d %H:%M ET")


def market_session(ts: datetime | None = None) -> str:
    """'pre-market', 'open', 'after-hours' or 'closed' (weekends; holidays not detected)."""
    t = (ts or now_utc()).astimezone(NY)
    if t.weekday() >= 5:
        return "closed (weekend)"
    hm = t.hour * 60 + t.minute
    if hm < 4 * 60:
        return "closed (overnight)"
    if hm < 9 * 60 + 30:
        return "pre-market"
    if hm < 16 * 60:
        return "open"
    if hm < 20 * 60:
        return "after-hours"
    return "closed (overnight)"


def http_get(url: str, ua: str | None = None, timeout: int = 20) -> requests.Response:
    return requests.get(url, headers={"User-Agent": ua or BROWSER_UA}, timeout=timeout)


def ensure_dirs() -> None:
    for d in (DATA, JOURNAL, REPORTS, DATA / "briefs"):
        d.mkdir(parents=True, exist_ok=True)
