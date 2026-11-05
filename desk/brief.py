"""Morning scan: run every pipeline and write one timestamped data packet.

Output:
  reports/YYYY-MM-DD_scan.md     human-readable packet (read by the analyst before writing the report)
  data/briefs/YYYY-MM-DD.json    the same data, machine-readable

The scan contains FACTS and rule-based screens only. The judgment (which news
matters, what is priced in, which trades rank top 3) is written afterwards in
reports/YYYY-MM-DD_report.md following CLAUDE.md.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, List

import numpy as np
import pandas as pd

from desk import analysis as A
from desk import macro, market, news, paper, risk
from desk import signals_alt as S
from desk.common import REPORTS, DATA, ensure_dirs, market_session, now_ny, stamp

log = logging.getLogger(__name__)


def _safe(name: str, fn, gaps: List[str], default=None):
    t = time.time()
    try:
        out = fn()
        log.info("%s ok (%.1fs)", name, time.time() - t)
        return out
    except Exception as exc:
        log.warning("%s FAILED: %s", name, exc)
        gaps.append(f"{name}: unavailable ({exc.__class__.__name__}: {str(exc)[:80]})")
        return default


def pct(x, d=1):
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 100:+.{d}f}%"


def run_scan(refresh: bool = True) -> Dict[str, object]:
    ensure_dirs()
    gaps: List[str] = []
    t0 = time.time()
    session = market_session()
    market.DATA_GAPS.clear()
    data = _safe("universe prices", lambda: market.load_universe_data(refresh), gaps)
    gaps.extend(market.DATA_GAPS)
    cross = _safe("cross-asset", market.cross_asset_snapshot, gaps, pd.DataFrame())
    vix = float(cross.set_index("symbol").loc["^VIX", "last"]) if len(cross) else np.nan
    vix3m = float(cross.set_index("symbol").loc["^VIX3M", "last"]) if len(cross) else np.nan
    regime = _safe("market regime", lambda: A.market_regime(data, vix, vix3m), gaps, {"regime": "UNKNOWN"}) if data else {"regime": "UNKNOWN"}
    sectors = _safe("sector rotation", lambda: A.sector_rotation(data), gaps, pd.DataFrame()) if data else pd.DataFrame()
    eco = _safe("FRED economic data", macro.economic_dashboard, gaps, pd.DataFrame())
    fed = macro.fed_path_proxy(eco) if len(eco) else {"signal": "unavailable"}
    headlines = _safe("news feeds", news.collect_news, gaps, pd.DataFrame())
    eightk = _safe("SEC 8-K feed", lambda: S.sec_feed("8-K"), gaps, pd.DataFrame())
    f4 = _safe("SEC Form 4 feed", lambda: S.sec_feed("4"), gaps, pd.DataFrame())
    buys = _safe("insider purchases", lambda: S.form4_purchases(f4) if len(f4) else pd.DataFrame(), gaps, pd.DataFrame())
    reddit = _safe("Reddit attention (ApeWisdom)", S.reddit_attention, gaps, pd.DataFrame())
    st_trend = _safe("StockTwits trending", S.stocktwits_trending, gaps, [])
    setups = _safe("screener", lambda: A.screen(data, sectors, regime["regime"]), gaps, pd.DataFrame()) if data and len(sectors) else pd.DataFrame()
    book_update = _safe("paper portfolio update", paper.update, gaps, {"account": paper.account(paper.load()), "events": []})
    acct = book_update["account"]

    # News vs. price: tickers in high-scoring news whose stock barely moved (or moved against the news)
    mismatch = pd.DataFrame()
    if len(headlines) and data:
        c = data["close"]
        rows = []
        for _, h in headlines[headlines["score"] >= 4].iterrows():
            for t in h["tickers"]:
                if t in c:
                    rows.append({"ticker": t, "headline": h["title"], "score": h["score"],
                                 "chg_1d": float(c[t].iloc[-1] / c[t].iloc[-2] - 1), "categories": ", ".join(h["categories"])})
        mismatch = pd.DataFrame(rows).drop_duplicates("ticker") if rows else pd.DataFrame()

    # Size each setup with the risk engine and pull fundamentals + earnings dates for the top names
    sized = []
    # The score favors longs in bullish regimes, so a plain top-N hides every short: always include them,
    # both for the report's bull/bear comparison and so the forward test collects short signals (experiment X1).
    if len(setups):
        shorts = setups[setups["direction"] == "SHORT"].head(10)
        pick = pd.concat([setups.head(15), shorts]).drop_duplicates("ticker")
    else:
        pick = setups
    for _, s in pick.iterrows():
        sz = risk.size_stock(acct["equity"], s["entry"], s["stop"], s["target"], acct["open_risk"], s["direction"])
        f = A.fundamentals(s["ticker"]) if (len(sized) < 10 or s["direction"] == "SHORT") else {}
        sized.append({**s.to_dict(), "shares": sz.quantity, "invested": sz.invested, "risk_$": sz.risk,
                      "risk_pct": sz.risk_pct, "profit_at_target": sz.potential_profit, "binding_limit": sz.binding_limit,
                      "next_earnings": f.get("next_earnings"), "fwd_pe": f.get("forwardPE"),
                      "rev_growth": f.get("revenueGrowth"), "short_pct_float": f.get("shortPercentOfFloat"),
                      "analyst_target": f.get("targetMeanPrice"), "analyst_rating": f.get("recommendationMean")})
    sized = pd.DataFrame(sized)

    packet = {"generated": stamp(), "session": session, "regime": regime, "fed_proxy": fed,
              "account": acct, "paper_events": book_update["events"], "data_gaps": gaps,
              "runtime_s": round(time.time() - t0, 1)}
    _write(packet, cross, eco, sectors, headlines, eightk, buys, reddit, st_trend, sized, mismatch)
    return {**packet, "cross": cross, "economy": eco, "sectors": sectors, "news": headlines, "setups": sized,
            "mismatch": mismatch, "eightk": eightk, "insider_buys": buys, "reddit": reddit, "stocktwits": st_trend}


def _md_table(df: pd.DataFrame, cols: List[str], fmt: Dict[str, str] | None = None, n: int = 20) -> str:
    if df is None or len(df) == 0:
        return "_none_\n"
    d = df[cols].head(n).copy()
    for c, f in (fmt or {}).items():
        if c in d:
            d[c] = d[c].map(lambda x, f=f: (f.format(x) if x is not None and x == x else "n/a"))
    return d.to_markdown(index=False) + "\n"


def _write(packet, cross, eco, sectors, headlines, eightk, buys, reddit, st_trend, sized, mismatch) -> None:
    day = str(now_ny().date())
    rg = packet["regime"]
    L = [f"# Morning scan: {day}", f"Generated **{packet['generated']}** · market session: **{packet['session']}** · "
         f"runtime {packet['runtime_s']}s", "",
         "> Data packet only: facts and rule-based screens. Quotes are delayed (Yahoo Finance); every table shows its as-of date.", ""]
    if packet["data_gaps"]:
        L += ["## ⚠️ Data gaps", *[f"- {g}" for g in packet["data_gaps"]], ""]
    L += ["## Market regime (rule-based)",
          f"**{rg.get('regime')}** (score {rg.get('score')}) · SPY {rg.get('spy', float('nan')):,.2f} "
          f"({pct(rg.get('spy_vs_50d'))} vs 50d, {pct(rg.get('spy_vs_200d'))} vs 200d) · drawdown from high {pct(rg.get('drawdown_from_high'))} · "
          f"breadth {rg.get('breadth_above_50d', float('nan')):.0%} of universe above 50d · VIX {rg.get('vix', float('nan')):.2f} vs VIX3M "
          f"{rg.get('vix3m', float('nan')):.2f} ({'INVERTED = stress' if rg.get('vix_term_inverted') else 'normal contango'}) · as of {rg.get('as_of')}", ""]
    L += ["## Cross-asset", _md_table(cross.assign(
        last_fmt=[f"{v:.2f}%" if y else f"{v:,.2f}" for v, y in zip(cross["last"], cross["is_yield"])],
        d1=[(f"{v * 100:+.0f}bp" if y else pct(v)) for v, y in zip(cross["chg_1d"], cross["is_yield"])],
        d5=[(f"{v * 100:+.0f}bp" if y else pct(v)) for v, y in zip(cross["chg_5d"], cross["is_yield"])],
        m1=[(f"{v * 100:+.0f}bp" if y else pct(v)) for v, y in zip(cross["chg_1m"], cross["is_yield"])]),
        ["asset", "last_fmt", "d1", "d5", "m1", "as_of"]) if len(cross) else "_unavailable_\n"]
    fed = packet["fed_proxy"]
    L += ["## Economy & Fed (FRED: latest RELEASED values; no consensus forecasts)",
          _md_table(eco, ["indicator", "latest", "previous", "as_of"], {"latest": "{:,.2f}", "previous": "{:,.2f}"}, 25),
          f"**Fed path proxy:** {fed.get('signal')} · 2Y − fed funds = {fed.get('two_year_minus_fed_funds', float('nan')):+.2f}pp · "
          f"curve 10Y−2Y {fed.get('curve_10y_2y', float('nan')):+.2f} · HY spread {fed.get('hy_spread', float('nan')):.2f}% ({fed.get('credit_stress')}). "
          f"_{fed.get('note', '')}_", ""]
    if len(sectors):
        s = sectors.copy()
        L += ["## Sector rotation (relative strength vs SPY)",
              _md_table(s, ["sector", "etf", "ret_1m", "rs_1m", "rs_3m", "momentum_change", "above_50d"],
                        {"ret_1m": "{:+.1%}", "rs_1m": "{:+.1%}", "rs_3m": "{:+.1%}", "momentum_change": "{:+.1%}"}, 20),
              f"Gaining momentum: {', '.join(s.nlargest(3, 'momentum_change')['sector'])} · "
              f"Losing momentum: {', '.join(s.nsmallest(3, 'momentum_change')['sector'])}", ""]
    if len(headlines):
        h = headlines.head(30).copy()
        h["time"] = h["time_utc"].map(stamp)
        h["tags"] = h["categories"].map(", ".join)
        h["tick"] = h["tickers"].map(", ".join)
        L += ["## Top headlines (triage score 0-10)", _md_table(h, ["score", "time", "publisher", "tags", "tick", "title"], n=30)]
    L += ["## News vs. price (tickers in material news; check if the move matches the news)",
          _md_table(mismatch, ["ticker", "chg_1d", "score", "categories", "headline"], {"chg_1d": "{:+.1%}"}, 15)]
    L += ["## SEC filings (universe companies)", "**8-K corporate events:**",
          _md_table(eightk.drop_duplicates("ticker") if len(eightk) else eightk, ["ticker", "updated", "title"], n=15),
          "**Insider open-market purchases (Form 4, code P):**",
          _md_table(buys, ["ticker", "insider", "title", "shares", "price", "value", "date"], {"value": "${:,.0f}", "price": "${:,.2f}"}, 10)]
    L += ["## Social attention (one input among many)",
          _md_table(reddit, ["rank", "ticker", "mentions", "mention_change", "significance", "in_universe"],
                    {"mention_change": "{:+.0%}"}, 15),
          f"StockTwits trending: {', '.join(st_trend[:20]) if st_trend else 'unavailable'}", ""]
    L += ["## Screener setups (rule-based, sized with the risk engine)",
          "Setups: " + "; ".join(f"**{k}**: {v}" for k, v in A.SETUP_INFO.items()), "",
          _md_table(sized, ["ticker", "direction", "setups", "price", "entry", "stop", "target", "reward_risk", "shares",
                            "risk_$", "chg_1d", "rs_3m_vs_spy", "next_earnings", "fwd_pe", "analyst_target", "score"],
                    {"price": "{:,.2f}", "entry": "{:,.2f}", "stop": "{:,.2f}", "target": "{:,.2f}", "reward_risk": "{:.1f}",
                     "risk_$": "${:,.0f}", "chg_1d": "{:+.1%}", "rs_3m_vs_spy": "{:+.1%}", "fwd_pe": "{:.1f}",
                     "analyst_target": "{:,.2f}", "score": "{:.2f}"}, 25)]
    a = packet["account"]
    L += ["## Paper portfolio ($30,000 start)",
          f"Equity **${a['equity']:,.2f}** ({pct(a['total_return'], 2)}) · cash ${a['cash']:,.2f} · open risk ${a['open_risk']:,.0f} "
          f"({a['open_risk'] / a['equity']:.1%}) · options exposure ${a['options_exposure']:,.0f}",
          *(f"- {e}" for e in packet["paper_events"]), ""]
    out = REPORTS / f"{day}_scan.md"
    if out.exists():   # never overwrite the morning scan that the day's report cites
        out = REPORTS / f"{day}_scan_{now_ny().strftime('%H%M')}.md"
    out.write_text("\n".join(L))
    packet["scan_file"] = str(out.relative_to(REPORTS.parent))
    js = {**packet, "setups": sized.to_dict("records") if len(sized) else [],
          "sectors": sectors.to_dict("records") if len(sectors) else []}
    (DATA / "briefs" / f"{day}.json").write_text(json.dumps(js, indent=2, default=str))
