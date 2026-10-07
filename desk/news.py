"""News-ingestion pipeline: Google News topic feeds, CNBC, MarketWatch, Federal Reserve.

Each headline is tagged with:
  * categories (earnings, M&A, Fed, inflation, tariffs, geopolitics …) via keyword rules
  * tickers it mentions (company names / tickers from the universe)
  * a materiality score (0-10) = category weight + "price-moving" words + recency
The score is a triage tool: it decides what the analyst reads first, not what to trade.
"""
from __future__ import annotations

import html
import logging
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import feedparser
import pandas as pd

from desk.common import config, http_get, now_utc, universe

log = logging.getLogger(__name__)

CATEGORIES = {
    "earnings": r"\bearnings\b|\bEPS\b|quarterly results|beats? estimates|miss(es|ed)? estimates|revenue (rose|fell|beat|miss)",
    "guidance": r"guidance|outlook|forecast (cut|raise)|raises? (its )?(full-year|annual)|lowers? (its )?(full-year|annual)|warns",
    "m&a": r"acquir|merger|takeover|buyout|to buy |deal to|bid for|tender offer|spin-?off",
    "analyst": r"upgrade|downgrade|price target|initiat(es|ed) coverage|overweight|underweight|outperform",
    "insider": r"insider|form 4|director (buys|sells)|CEO (buys|sells)",
    "buyback": r"buyback|repurchase",
    "capital_raise": r"offering|raises? \$|share sale|convertible|dilut",
    "distress": r"bankrupt|chapter 11|default|going concern|delist|restructur",
    "accounting": r"restat|accounting|auditor|SEC probe|investigation|fraud|short seller",
    "ipo": r"\bIPO\b|initial public offering|debut",
    "fed": r"\bFed\b|Federal Reserve|FOMC|Powell|rate (cut|hike)|interest rates?|monetary policy",
    "inflation": r"inflation|\bCPI\b|\bPPI\b|\bPCE\b|prices rose",
    "jobs": r"payrolls|jobs report|unemployment|jobless claims|labor market|wage",
    "growth": r"\bGDP\b|retail sales|consumer (confidence|sentiment|spending)|\bISM\b|\bPMI\b|housing starts|recession",
    "tariffs_trade": r"tariff|trade war|trade deal|export control|import duty|sanction",
    "china_taiwan": r"China|Chinese|Beijing|Taiwan|Xi Jinping",
    "geopolitics": r"war|military|missile|attack|Ukraine|Russia|Israel|Iran|Gaza|Houthi|Red Sea|NATO|strike",
    "energy": r"\boil\b|crude|OPEC|natural gas|gasoline|Brent|WTI",
    "regulation": r"antitrust|FTC|DOJ|regulat|lawsuit|ruling|court|ban",
    "fiscal_politics": r"Congress|Senate|House|White House|executive order|Trump|budget|shutdown|tax bill|spending bill|election",
    "ai_semis": r"\bAI\b|artificial intelligence|semiconductor|chip|GPU|data center",
    "crypto": r"bitcoin|crypto|ether(eum)?",
}
WEIGHTS = {"earnings": 2.5, "guidance": 3, "m&a": 3, "analyst": 1.5, "insider": 1.5, "buyback": 1.5,
           "capital_raise": 2, "distress": 3, "accounting": 3, "ipo": 1, "fed": 2.5, "inflation": 2.5,
           "jobs": 2.5, "growth": 2, "tariffs_trade": 2.5, "china_taiwan": 1.5, "geopolitics": 2,
           "energy": 1.5, "regulation": 1.5, "fiscal_politics": 1, "ai_semis": 1, "crypto": 0.5}
# Local news from other countries is rarely tradable for a U.S. desk: down-weight it
NON_US = r"\bRBI\b|Sensex|Nifty|rupee|\bIndia|\bIndian\b|Pakistan|Nigeria|Kenya|Bangladesh|Sri Lanka|Philippine|Malaysia|Indonesia|Vietnam|\bASX\b|Ghana"
# Source quality: primary/major outlets first; aggregators and content farms down-weighted
TIER1 = r"Reuters|Bloomberg|Wall Street Journal|WSJ|Financial Times|\bFT\b|CNBC|cnbc_|MarketWatch|marketwatch_|Barron|Associated Press|\bAP\b|Federal Reserve|fed_|Axios|The Information|New York Times|Washington Post|Politico|Semafor"
TIER2 = r"Yahoo Finance|Investing\.com|Fox Business|CNN|Business Insider|Fortune|Forbes|Morningstar|Seeking Alpha|TipRanks|Benzinga|Kiplinger|Investopedia|TheStreet|Motley Fool|Zacks|Nasdaq|Stocktwits|TradingView|Proactive"
MOVERS = r"soar|plunge|surge|tumble|jump|sink|slump|rall(y|ies)|crash|record|halt|biggest|sharpest|unexpected|surprise|shock"


def _feed(url: str) -> List[Dict]:
    try:
        r = http_get(url)
        if r.status_code != 200:
            log.warning("feed %s → HTTP %s", url[:60], r.status_code)
            return []
        return feedparser.parse(r.content).entries
    except Exception as exc:  # pragma: no cover - network
        log.warning("feed failed %s: %s", url[:60], exc)
        return []


def _when(e) -> Optional[datetime]:
    for k in ("published_parsed", "updated_parsed"):
        if e.get(k):
            return datetime.fromtimestamp(timegm(e[k]), tz=timezone.utc)
    return None


def google_news_url(query: str, days: int = 1) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}+when:{days}d&hl=en-US&gl=US&ceid=US:en"


# Names that are ordinary words or collide with other headlines: match only on
# explicit ticker forms ($TGT, (TGT), NYSE: TGT) or an unambiguous alias.
AMBIGUOUS = {"DOW": None, "TGT": "Target Corp", "V": "Visa Inc", "XYZ": "Block Inc", "PGR": "Progressive Corp",
             "SO": "Southern Co", "D": "Dominion Energy", "MSTR": "MicroStrategy", "MPC": "Marathon Petroleum",
             "ARM": "Arm Holdings", "O": "Realty Income", "CAT": "Caterpillar", "GE": "GE Aerospace",
             "ETN": "Eaton", "MP": "MP Materials", "WM": "Waste Management", "CB": "Chubb", "C": "Citigroup",
             "T": "AT&T", "F": "Ford Motor", "MA": "Mastercard", "LIN": "Linde", "PM": "Philip Morris",
             "ALB": "Albemarle", "DE": "Deere", "UNP": "Union Pacific", "WELL": "Welltower", "NOW": "ServiceNow",
             "HD": "Home Depot", "LOW": "Lowe's", "CI": "Cigna", "COST": "Costco", "DG": "Dollar General",
             "KO": "Coca-Cola", "MO": "Altria", "CL": "Colgate", "GS": "Goldman Sachs", "MS": "Morgan Stanley",
             "BX": "Blackstone", "GD": "General Dynamics", "AMT": "American Tower", "PSA": "Public Storage",
             "HOOD": "Robinhood", "DELL": "Dell", "SNOW": "Snowflake", "SHOP": "Shopify", "SPOT": "Spotify"}


def _name_patterns() -> Dict[str, re.Pattern]:
    pats = {}
    for r in universe().itertuples():
        if r.sector == "ETF":
            continue
        t = re.escape(r.ticker)
        explicit = rf"\${t}\b|\({t}\)|(NYSE|NASDAQ|Nasdaq|NYSE American):\s?{t}\b"
        if r.ticker in AMBIGUOUS:
            alias = AMBIGUOUS[r.ticker]
            pats[r.ticker] = re.compile(explicit + (rf"|\b{re.escape(alias)}" if alias else ""))
            continue
        short = re.sub(r"\b(Inc\.?|Corp\.?|Holdings|Technologies|Group|Company|Companies|Co\.?)\b", "", r.name)
        short = re.sub(r"\(.*?\)", "", short).strip(" ,.")
        bare = rf"|\b{t}\b" if len(r.ticker) >= 4 else ""
        pats[r.ticker] = re.compile(rf"\b{re.escape(short)}\b|{explicit}{bare}")
    return pats


_PATS: Optional[Dict[str, re.Pattern]] = None


def tag(title: str) -> Dict[str, object]:
    global _PATS
    if _PATS is None:
        _PATS = _name_patterns()
    cats = [c for c, p in CATEGORIES.items() if re.search(p, title, re.I)]
    ticks = [t for t, p in _PATS.items() if p.search(title)]
    return {"categories": cats, "tickers": ticks}


def source_weight(publisher: str) -> float:
    if re.search(TIER1, publisher or "", re.I):
        return 1.3
    if re.search(TIER2, publisher or "", re.I):
        return 1.0
    if re.search(r"[^\x00-\x7F]", publisher or ""):   # non-English outlet name
        return 0.4
    return 0.7


def score(title: str, cats: List[str], tickers: List[str], age_hours: float, publisher: str = "") -> float:
    s = sum(WEIGHTS.get(c, 0) for c in cats)
    if re.search(MOVERS, title, re.I):
        s += 2
    if tickers:
        s += 1
    if re.search(NON_US, title):
        s *= 0.3
    s *= source_weight(publisher)
    s *= max(0.35, 1 - age_hours / 48)       # fade with age
    return round(min(10.0, s), 2)


def collect_news(max_age_hours: Optional[int] = None) -> pd.DataFrame:
    cfg = config()["news"]
    max_age = max_age_hours or cfg["max_age_hours"]
    cutoff = now_utc() - timedelta(hours=max_age)
    feeds = {f"google:{k}": google_news_url(q) for k, q in cfg["google_news_topics"].items()}
    feeds.update(cfg["rss"])
    rows = []
    for src, url in feeds.items():
        for e in _feed(url):
            when = _when(e)
            if when is None or when < cutoff:
                continue
            title = html.unescape(e.get("title", "")).strip()
            publisher = ""
            if src.startswith("google:") and " - " in title:
                title, publisher = title.rsplit(" - ", 1)
            rows.append({"time_utc": when, "title": title, "publisher": publisher or src.split(":")[0],
                         "feed": src, "link": e.get("link", "")})
    if not rows:
        return pd.DataFrame(columns=["time_utc", "title", "publisher", "feed", "link", "categories", "tickers", "score"])
    df = pd.DataFrame(rows)
    df["key"] = df["title"].str.lower().str.replace(r"[^a-z0-9 ]", "", regex=True).str[:80]
    df = df.sort_values("time_utc").drop_duplicates("key", keep="first")
    tags = df["title"].apply(tag)
    df["categories"] = tags.apply(lambda t: t["categories"])
    df["tickers"] = tags.apply(lambda t: t["tickers"])
    age = (now_utc() - df["time_utc"]).dt.total_seconds() / 3600
    df["score"] = [score(t, c, k, a, p) for t, c, k, a, p in zip(df["title"], df["categories"], df["tickers"], age, df["publisher"])]
    return df.drop(columns="key").sort_values("score", ascending=False).reset_index(drop=True)


def ticker_news(ticker: str, name: str = "", days: int = 3, limit: int = 15) -> pd.DataFrame:
    q = f'"{name}" OR {ticker} stock' if name else f"{ticker} stock"
    rows = []
    for e in _feed(google_news_url(q, days))[:limit]:
        title = html.unescape(e.get("title", ""))
        pub = ""
        if " - " in title:
            title, pub = title.rsplit(" - ", 1)
        rows.append({"time_utc": _when(e), "title": title, "publisher": pub, "link": e.get("link", "")})
    return pd.DataFrame(rows)
