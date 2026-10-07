"""Alternative data: SEC filings (8-K, insider Form 4) and social attention.

SEC EDGAR (free): live feeds of 8-K corporate events and Form 4 insider trades.
  Form 4 transaction code P = open-market PURCHASE (the informative one; insiders
  sell for many reasons, but buy with their own cash for one).
Social (free, public):
  * ApeWisdom: Reddit mention counts per ticker and rank 24h ago → attention spikes
  * StockTwits: trending symbols and bullish/bearish tags on recent messages
X/Twitter and Google Trends are NOT used: X requires a paid API, and the
unofficial Google Trends endpoints are unreliable.

Social signals are graded LOW / MEDIUM / HIGH significance and are never a
trade reason on their own.
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Dict, List

import feedparser
import pandas as pd

from desk.common import DATA, config, http_get, universe

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- SEC
def _cik_map() -> Dict[int, str]:
    cache = DATA / "sec_company_tickers.json"
    if not cache.exists():
        r = http_get("https://www.sec.gov/files/company_tickers.json", config()["data"]["sec_user_agent"])
        r.raise_for_status()
        DATA.mkdir(exist_ok=True)
        cache.write_bytes(r.content)
    raw = json.loads(cache.read_text())
    uni = set(universe()["ticker"].str.replace("-", "."))
    return {int(v["cik_str"]): v["ticker"].replace(".", "-") for v in raw.values()
            if v["ticker"] in uni or v["ticker"].replace(".", "-") in uni}


def sec_feed(kind: str) -> pd.DataFrame:
    """Recent 8-K or Form 4 filings for companies in the universe."""
    ua = config()["data"]["sec_user_agent"]
    url = config()["news"]["sec"]["eight_k" if kind == "8-K" else "form4"]
    try:
        r = http_get(url, ua)
        entries = feedparser.parse(r.content).entries
    except Exception as exc:  # pragma: no cover
        log.warning("SEC feed failed: %s", exc)
        return pd.DataFrame()
    ciks = _cik_map()
    rows = []
    for e in entries:
        m = re.search(r"\((\d{10})\)", e.get("title", ""))
        if not m:
            continue
        cik = int(m.group(1))
        if cik not in ciks:
            continue
        rows.append({"ticker": ciks[cik], "form": kind, "title": e.title, "updated": e.get("updated", ""),
                     "link": e.get("link", ""), "summary": re.sub("<[^>]+>", " ", e.get("summary", ""))[:300]})
    return pd.DataFrame(rows)


def form4_purchases(feed: pd.DataFrame, max_filings: int = 30) -> pd.DataFrame:
    """Parse Form 4 XML for open-market purchases (transaction code P) by universe companies."""
    ua = config()["data"]["sec_user_agent"]
    out = []
    for row in feed.drop_duplicates("link").head(max_filings).itertuples():
        try:
            folder = row.link.rsplit("/", 1)[0]
            items = http_get(folder + "/index.json", ua).json()["directory"]["item"]
            xml_name = next(i["name"] for i in items if i["name"].endswith(".xml"))
            root = ET.fromstring(http_get(f"{folder}/{xml_name}", ua).content)
            issuer = (root.findtext(".//issuer/issuerTradingSymbol") or "").upper().replace(".", "-")
            owner = root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or ""
            title = root.findtext(".//reportingOwnerRelationship/officerTitle") or (
                "Director" if root.findtext(".//reportingOwnerRelationship/isDirector") in ("1", "true") else "")
            for t in root.findall(".//nonDerivativeTransaction"):
                if t.findtext(".//transactionCoding/transactionCode") != "P":
                    continue
                sh = float(t.findtext(".//transactionShares/value") or 0)
                px = float(t.findtext(".//transactionPricePerShare/value") or 0)
                out.append({"ticker": issuer or row.ticker, "insider": owner, "title": title, "shares": sh,
                            "price": px, "value": sh * px, "date": t.findtext(".//transactionDate/value")})
        except Exception:
            continue
    return pd.DataFrame(out)


# ---------------------------------------------------------------- social
def reddit_attention(top: int = 40) -> pd.DataFrame:
    """ApeWisdom: Reddit mentions in the last 24h, rank change vs. 24h ago."""
    try:
        r = http_get("https://apewisdom.io/api/v1.0/filter/all-stocks/page/1")
        res = r.json().get("results", [])
    except Exception as exc:  # pragma: no cover
        log.warning("ApeWisdom unavailable: %s", exc)
        return pd.DataFrame()
    df = pd.DataFrame(res)[["rank", "ticker", "name", "mentions", "mentions_24h_ago", "rank_24h_ago", "upvotes"]].head(top)
    df["mentions"] = pd.to_numeric(df["mentions"], errors="coerce")
    df["mentions_24h_ago"] = pd.to_numeric(df["mentions_24h_ago"], errors="coerce")
    df["mention_change"] = (df["mentions"] / df["mentions_24h_ago"].replace(0, pd.NA) - 1).astype(float)
    df["in_universe"] = df["ticker"].isin(universe()["ticker"])
    df["significance"] = df.apply(_social_significance, axis=1)
    return df


def _social_significance(row) -> str:
    chg = row.get("mention_change")
    m = row.get("mentions") or 0
    if pd.notna(chg) and chg > 2 and m > 100:
        return "HIGH"
    if (pd.notna(chg) and chg > 0.75 and m > 40) or row.get("rank", 99) <= 5:
        return "MEDIUM"
    return "LOW"


def stocktwits_trending() -> List[str]:
    try:
        r = http_get("https://api.stocktwits.com/api/2/trending/symbols.json")
        return [s["symbol"] for s in r.json().get("symbols", [])]
    except Exception:  # pragma: no cover
        return []


def stocktwits_sentiment(ticker: str) -> Dict[str, object]:
    """Share of bullish vs. bearish tags in the latest ~30 messages."""
    try:
        r = http_get(f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json")
        msgs = r.json().get("messages", [])
    except Exception:  # pragma: no cover
        return {"ticker": ticker, "messages": 0}
    tags = [((m.get("entities") or {}).get("sentiment") or {}).get("basic") for m in msgs]
    bull, bear = tags.count("Bullish"), tags.count("Bearish")
    return {"ticker": ticker, "messages": len(msgs), "bullish": bull, "bearish": bear,
            "bull_ratio": bull / (bull + bear) if (bull + bear) else None}
