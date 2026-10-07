"""Intelligence layer (V1.1): turns raw memory into evidence that can be retrieved at decision time.

  predictions   directional / magnitude / timing accuracy and confidence calibration (50-59 … 90-100% buckets)
  rejections    what happened to trades NOT taken (same simulator as everything else), stats by rejection reason
  opportunity   per decision day: the selected trade vs the rejected alternatives (all simulated identically)
  benchmarks    every trade vs S&P 500 (SPY), Nasdaq-100 (QQQ) and its sector/industry ETF over the same window
  events        reactions across stocks, sectors, Nasdaq, VIX, 10Y yield, dollar, oil and gold at 1h/1d/1w/1m,
                plus a rule-based over-/under-reaction label
  companies     fundamentals (Yahoo, timestamped) and technical picture for priority companies
  patterns      groups (setup/strategy × regime) that meet the evidence standard: candidates vs validated
  experiments   baseline vs alternative arms, difference in mean R with a 95% CI; a suggestion, never an action
  scorecards    does the pre-trade score predict outcomes?
  similar       outcomes of trades and signals on days whose conditions were closest to today's
Every result carries n and an evidence level. Nothing here changes a rule.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from desk import learning, memory, regimes
from desk.common import DATA, JOURNAL, config, now_ny, universe

SNAPSHOTS = JOURNAL / "company_snapshots.json"
BUCKETS = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
EVENT_ASSETS = {"S&P 500 (SPY)": "SPY", "Nasdaq-100 (QQQ)": "QQQ", "VIX": "^VIX", "10Y yield": "^TNX",
                "Dollar (DXY)": "DX-Y.NYB", "Oil (WTI)": "CL=F", "Gold": "GC=F"}


def _ev(n, lo=None, hi=None, robust=True):
    return learning.evidence(n, lo, hi, robust)


# ------------------------------------------------------------------ predictions
def prediction_detail(ps: List[Dict], P: Dict[str, pd.DataFrame]) -> List[Dict]:
    """Add magnitude and timing accuracy to scored price predictions (directional accuracy is set by learning)."""
    c = P["close"]
    for p in ps:
        if p.get("kind", "price") != "price" or p.get("status") != "scored" or p.get("ticker") not in c:
            continue
        s = c[p["ticker"]].dropna()
        start = pd.Timestamp(p["created"][:10])
        sign = -1 if p["direction"] in ("DOWN", "UNDERPERFORM") else 1
        bench = c[p["benchmark"]].dropna() if p.get("benchmark") in c else None
        path = s[s.index > start] / p["start_price"] - 1
        if bench is not None and p.get("bench_start"):
            path = path - (bench.reindex(path.index) / p["bench_start"] - 1)
        path = sign * path
        val = sign * (p["relative_return"] if p.get("benchmark") else p["actual_return"])
        mag = p.get("magnitude")
        p["magnitude_ratio"] = float(val / mag) if mag else None
        h = int(p.get("horizon_days") or 0)
        within, after = path.iloc[:h], path.iloc[h:2 * h]
        thr = mag if mag else 0.0
        hit = within[within >= thr] if mag else within[within > 0]
        p["days_to_target_move"] = int(within.index.get_loc(hit.index[0]) + 1) if len(hit) else None
        if p["correct"] and (not mag or val >= mag):
            p["timing"] = "on time"
        elif len(hit):
            p["timing"] = "early (moved, then faded by the horizon)"
        elif len(after) >= h and ((after >= thr).any() if mag else (after > 0).any()):
            p["timing"] = "late (moved only after the horizon)"
        elif len(after) < h:
            p["timing"] = "pending (needs one more horizon of data)"
        else:
            p["timing"] = "never moved as predicted"
    memory.save_predictions(ps)
    return ps


def calibration(ps: List[Dict]) -> Dict[str, object]:
    sc = [p for p in ps if p.get("status") == "scored"]
    rows = []
    for lo, hi in BUCKETS:
        g = [p for p in sc if lo <= float(p.get("confidence_pct") or memory.CONFIDENCE_PROB[p.get("confidence", "medium")] * 100) < hi]
        n = len(g)
        acc = sum(bool(p["correct"]) for p in g) / n if n else None
        se = math.sqrt(acc * (1 - acc) / n) if n else None
        mid = (lo + min(hi, 100)) / 200
        flag = ""
        if n >= 10 and acc is not None:
            if acc + 1.96 * se < lo / 100:
                flag = "OVERCONFIDENT: hit rate is significantly below the stated confidence"
            elif acc - 1.96 * se > min(hi, 100) / 100:
                flag = "UNDERCONFIDENT: hit rate is significantly above the stated confidence"
        rows.append({"bucket": f"{lo}-{min(hi, 100) - 1 if hi <= 100 else 100}%", "n": n, "hit_rate": acc,
                     "ci95": [max(0, acc - 1.96 * se), min(1, acc + 1.96 * se)] if n else None, "expected": mid,
                     "flag": flag or ("" if n >= 10 else "needs ≥ 10 predictions in this bucket")})
    hi_g = [p for p in sc if float(p.get("confidence_pct") or 65) >= 75]
    lo_g = [p for p in sc if float(p.get("confidence_pct") or 65) < 65]
    mono = None
    if len(hi_g) >= 10 and len(lo_g) >= 10:
        mono = {"high_conf_hit": sum(p["correct"] for p in hi_g) / len(hi_g),
                "low_conf_hit": sum(p["correct"] for p in lo_g) / len(lo_g)}
        mono["confidence_is_informative"] = mono["high_conf_hit"] > mono["low_conf_hit"]
    acc = {"directional": None, "magnitude_met": None, "timing": {}}
    if sc:
        acc["directional"] = sum(bool(p["correct"]) for p in sc) / len(sc)
        m = [p for p in sc if p.get("magnitude_met") is not None]
        acc["magnitude_met"] = sum(bool(p["magnitude_met"]) for p in m) / len(m) if m else None
        ratios = [p["magnitude_ratio"] for p in sc if p.get("magnitude_ratio") is not None]
        acc["median_magnitude_ratio"] = float(np.median(ratios)) if ratios else None
        acc["timing"] = pd.Series([p.get("timing") for p in sc if p.get("timing")]).value_counts().to_dict()
    return {"buckets": rows, "high_vs_low": mono, "accuracy": acc, "scored": len(sc), "evidence": _ev(len(sc))}


# ------------------------------------------------------------------ rejected trades + opportunity cost
def _same_day(ts: str) -> bool:
    return bool(ts[11:16]) and ts[11:16] < "09:30"


def rejection_outcomes(P) -> pd.DataFrame:
    rs = memory.rejections()
    if not rs:
        return pd.DataFrame()
    out = []
    for r in rs:
        if r["instrument"] != "stock":
            out.append({**r, "status": "not simulated (option structures need historical chains)"})
            continue
        sim = learning.simulate(P, r["ticker"], r["direction"], r["stop"], r["target"], r["decided"][:10], _same_day(r["decided"]))
        pl = sim.get("r", sim.get("mark_r"))
        rec = {**r, **{f"sim_{k}": v for k, v in sim.items()}, "status": sim.get("status"),
               "hypothetical_r": pl, "hypothetical_pnl": pl * r["risk_usd"] if pl is not None and r.get("risk_usd") else None}
        if sim.get("status") == "resolved":
            rec["outcome_note"] = ("outcome agrees with the rejection (hypothetical loss/flat)" if pl <= 0.05 else
                                   "hypothetical gain: the rejection cost money, but judge the PROCESS (was the reason valid at the time?)")
        out.append(rec)
    return pd.DataFrame(out)


def rejection_stats(rj: pd.DataFrame) -> Dict[str, object]:
    if rj.empty or "hypothetical_r" not in rj:
        return {"n": 0}
    res = rj[rj["status"] == "resolved"].rename(columns={"hypothetical_r": "r"})
    out = {"total": len(rj), "resolved": len(res), "overall": learning.perf(res) if len(res) else {"n": 0},
           "by_reason": learning.group_perf(res, "reason") if len(res) else {}}
    if len(res):
        out["share_agreeing_with_rejection"] = float((res["r"] <= 0.05).mean())
    return out


def opportunity_cost(book: Dict, rj: pd.DataFrame, P) -> List[Dict]:
    """For each decision day with a taken trade and rejected alternatives: compare them, all simulated the same way."""
    days = {}
    for t in book["trades"]:
        if t["instrument"] != "stock":
            continue
        sim = learning.simulate(P, t["ticker"], t["direction"], t["stop"], t["target"], t["opened"][:10], _same_day(t["opened"]))
        days.setdefault(t["opened"][:10], {"selected": [], "alternatives": []})["selected"].append(
            {"label": f"#{t['id']} {t['ticker']}", "r": sim.get("r", sim.get("mark_r")), "status": sim.get("status"),
             "actual_r": t.get("r_multiple")})
    if len(rj):
        for r in rj.to_dict("records"):
            d = days.get(r["decided"][:10])
            if d is not None:
                d["alternatives"].append({"label": f"R{r['id']} {r['ticker']} ({r['reason']})", "r": r.get("hypothetical_r"),
                                          "status": r.get("status")})
    out = []
    for day, d in sorted(days.items()):
        if not d["alternatives"]:
            continue
        allc = d["selected"] + d["alternatives"]
        done = all(x["status"] == "resolved" for x in allc)
        best = max((x for x in allc if x["r"] is not None), key=lambda x: x["r"], default=None)
        out.append({"date": day, "selected": d["selected"], "alternatives": d["alternatives"], "all_resolved": done,
                    "best": best["label"] if best else None,
                    "selection_optimal": (best in d["selected"]) if (best and done) else None})
    return out


# ------------------------------------------------------------------ benchmarks
def benchmarks(book: Dict, P) -> Dict[str, Dict]:
    c = P["close"]
    uni = universe().set_index("ticker")
    out = {}
    for t in book["trades"]:
        und = (t.get("option") or {}).get("underlying") or t["ticker"]
        if und not in c:
            continue
        s = c[und].dropna()
        start = pd.Timestamp(t["opened"][:10])
        end = pd.Timestamp(t["closed"][:10]) if t.get("closed") else s.index[-1]
        base_i = s.index[s.index <= start]
        if not len(base_i):
            continue
        u_entry = t.get("underlying_entry") if t["instrument"] == "option" else t["entry_price"]
        u_end = float(s.loc[:end].iloc[-1])
        sign = -1 if t["direction"] == "SHORT" else 1
        if t["instrument"] == "stock":
            px_end = t.get("exit_price") if t.get("closed") else u_end
            trade_ret = sign * (px_end / t["entry_price"] - 1)
        else:
            px_end = t.get("exit_price") if t.get("closed") else t.get("last_mark", t["entry_price"])
            trade_ret = px_end / t["entry_price"] - 1
        rec = {"window": f"{start.date()} → {pd.Timestamp(end).date()}", "trade_return": float(trade_ret),
               "status": t["status"], "underlying_return": float(sign * (u_end / u_entry - 1)) if u_entry else None}
        etf = uni["sector_etf"].get(und)
        for name, sym in (("S&P 500 (SPY)", "SPY"), ("Nasdaq-100 (QQQ)", "QQQ"), (f"Sector/industry ({etf})", etf)):
            if sym and sym in c:
                b = c[sym].dropna()
                b0 = b.loc[:start].iloc[-1] if len(b.loc[:start]) else None
                if b0:
                    br = float(b.loc[:end].iloc[-1] / b0 - 1)
                    rec[name] = {"return": br, "excess": float(trade_ret - sign * br) if t["instrument"] == "stock" else None}
        if t["instrument"] == "option":
            rec["options_vs_underlying"] = "option return vs the underlying's move; see the counterfactual R in the trade note"
            alts = (t.get("option") or {}).get("alternatives") or []
            rec["alternatives"] = _alt_contracts(alts, start, end)
        out[str(t["id"])] = rec
    return out


def _alt_contracts(alts: List[Dict], start, end) -> List[Dict]:
    """Best effort: mark the alternative structures considered at entry (Yahoo keeps history only while listed)."""
    import yfinance as yf

    res = []
    for a in alts[:3]:
        try:
            h = yf.Ticker(a["contract"]).history(start=str(start.date()), end=str((pd.Timestamp(end) + pd.Timedelta(days=1)).date()))
            px = float(h["Close"].iloc[-1]) if len(h) else None
            if a.get("short_contract") and px is not None:
                hs = yf.Ticker(a["short_contract"]).history(start=str(start.date()), end=str((pd.Timestamp(end) + pd.Timedelta(days=1)).date()))
                px = px - float(hs["Close"].iloc[-1]) if len(hs) else None
            res.append({**a, "mark": px, "return": (px / a["debit"] - 1) if px is not None and a.get("debit") else None})
        except Exception:  # pragma: no cover - network / delisted
            res.append({**a, "mark": None, "return": None, "note": "price history unavailable"})
    return res


# ------------------------------------------------------------------ events
def event_detail(es: List[Dict], P) -> List[Dict]:
    c = P["close"]
    for e in es:
        names = {**{t: t for t in e.get("tickers", [])}, **{s: s for s in e.get("sectors", [])}, **EVENT_ASSETS}
        react = learning._bar_after(c, "SPY", e["date"], inclusive=not e.get("after_close"))
        if react is None:
            continue
        rx = {}
        for label, sym in names.items():
            if sym not in c:
                continue
            s = c[sym].dropna()
            prior = s.index[s.index < react]
            if not len(prior) or react not in s.index:
                continue
            base, pos = s.loc[prior[-1]], s.index.get_loc(react)
            if sym == "^TNX":                       # yield change in basis points (Yahoo sometimes quotes ×10)
                f = lambda a, b: float((a - b) * (100 if b < 20 else 10))
            elif sym == "^VIX":                     # VIX change in points
                f = lambda a, b: float(a - b)
            else:
                f = lambda a, b: float(a / b - 1)
            win = {"1d": 0, "1w": 4, "1m": 20}
            rx[label] = {k: (f(s.iloc[pos + n], base) if pos + n < len(s) else None) for k, n in win.items()}
            rx[label]["unit"] = "bp" if sym == "^TNX" else ("points" if sym == "^VIX" else "%")
        e.setdefault("reaction", {})["multi_asset"] = rx
        e["reaction"]["one_hour"] = e["reaction"].get("one_hour") or _one_hour(e)
        primary = (e.get("tickers") or ["SPY"])[0]
        r = rx.get(primary) or rx.get("S&P 500 (SPY)") or {}
        e["reaction"]["verdict"] = _reaction_verdict(r, primary, c)
    memory.save_events(es)
    return es


def _one_hour(e: Dict) -> Optional[Dict]:
    """SPY/QQQ/tickers 60 minutes after an intraday event (5-minute bars exist only for ~60 days)."""
    if not e.get("time") or e.get("after_close"):
        return None
    ts = pd.Timestamp(f"{e['date']} {e['time']}", tz="America/New_York")
    if (pd.Timestamp.now(tz="America/New_York") - ts).days > 55:
        return {"note": "intraday data no longer available"}
    import yfinance as yf

    out = {}
    for sym in ["SPY", "QQQ"] + e.get("tickers", []):
        try:
            h = yf.Ticker(sym).history(start=str(ts.date()), end=str((ts + pd.Timedelta(days=1)).date()), interval="5m")
            h.index = h.index.tz_convert("America/New_York")
            a, b = h.loc[:ts], h.loc[:ts + pd.Timedelta(minutes=60)]
            if len(a) and len(b) and b.index[-1] > a.index[-1]:
                out[sym] = float(b["Close"].iloc[-1] / a["Close"].iloc[-1] - 1)
        except Exception:  # pragma: no cover - network
            continue
    return out or {"note": "unavailable"}


def _reaction_verdict(r: Dict, sym: str, c: pd.DataFrame) -> str:
    """Rule: compare the 1-day move with the stock's typical daily move and with what followed over 1 month.
    Reversed by > half within a month → overreaction · continued to > 2× the first move → underreaction."""
    d1, m1 = r.get("1d"), r.get("1m")
    if d1 is None:
        return "pending"
    s = c[sym].dropna().pct_change() if sym in c else None
    sd = float(s.iloc[-260:].std()) if s is not None and len(s) > 30 else None
    size = f" ({abs(d1) / sd:.1f}× a typical day)" if sd else ""
    if m1 is None:
        return f"1-day move {d1:+.1%}{size}; 1-month follow-through pending"
    if np.sign(m1) != np.sign(d1) or abs(m1) < abs(d1) / 2:
        return f"OVERREACTION by rule: {d1:+.1%} day 1, {m1:+.1%} after a month{size}"
    if abs(m1) > 2 * abs(d1):
        return f"UNDERREACTION by rule: {d1:+.1%} day 1 continued to {m1:+.1%} after a month{size}"
    return f"PROPORTIONATE by rule: {d1:+.1%} day 1, {m1:+.1%} after a month{size}"


# ------------------------------------------------------------------ companies
def priority_tickers(book: Dict, sig: pd.DataFrame, watchlist: List[str]) -> List[str]:
    """Companies that deserve a living intelligence file (not every ticker the screener ever touched)."""
    pri = {(t.get("option") or {}).get("underlying") or t["ticker"] for t in book["trades"]}
    pri |= {p["ticker"] for p in memory.predictions() if p.get("ticker")}
    pri |= {x for e in memory.events() for x in e.get("tickers", [])}
    pri |= {r["ticker"] for r in memory.rejections()}
    pri |= set(watchlist)
    if len(sig):
        cnt = sig.groupby("ticker")["date"].nunique()
        pri |= set(cnt[cnt >= 3].index)
    etfs = set(universe().query("sector == 'ETF'")["ticker"]) | set(config()["sector_etfs"].values())
    return sorted(t for t in pri if t and t not in etfs)


def company_snapshots(tickers: List[str], P, refresh_days: int = 1) -> Dict[str, Dict]:
    from desk.analysis import atr, fundamentals, rsi, sma

    snaps = json.loads(SNAPSHOTS.read_text()) if SNAPSHOTS.exists() else {}
    c, h, l, v = P["close"], P["high"], P["low"], P["volume"]
    today = str(now_ny().date())
    for t in tickers:
        snap = snaps.get(t, {})
        if snap.get("fundamentals_date", "") < str((pd.Timestamp(today) - pd.Timedelta(days=refresh_days)).date()) or \
                snap.get("fundamentals_date") is None:
            try:
                f = fundamentals(t)
                snap["fundamentals"] = {k: v_ for k, v_ in f.items() if k != "recent_rating_changes"}
                snap["fundamentals_date"] = today
                snap["fundamentals_source"] = "Yahoo Finance (yfinance), retrieved " + now_ny().strftime("%Y-%m-%d %H:%M ET")
            except Exception:  # pragma: no cover - network
                pass
        if t in c and c[t].dropna().shape[0] > 210:
            s = c[t].dropna()
            hi, lo = h[t].reindex(s.index), l[t].reindex(s.index)
            spy = c["SPY"].reindex(s.index)
            last = float(s.iloc[-1])
            s20, s50, s200 = (float(sma(s, n).iloc[-1]) for n in (20, 50, 200))
            snap["technical"] = {
                "as_of": str(s.index[-1].date()), "price": last,
                "trend": ("uptrend" if last > s50 > s200 else "downtrend" if last < s50 < s200 else "mixed"),
                "vs_20d": last / s20 - 1, "vs_50d": last / s50 - 1, "vs_200d": last / s200 - 1,
                "support_20d_low": float(lo.iloc[-20:].min()), "support_55d_low": float(lo.iloc[-55:].min()),
                "resistance_20d_high": float(hi.iloc[-20:].max()), "resistance_52w_high": float(hi.iloc[-252:].max()),
                "rsi14": float(rsi(s.to_frame()).iloc[-1, 0]), "atr_pct": float(atr(hi, lo, s).iloc[-1] / last),
                "rs_3m_vs_spy": float((s.iloc[-1] / s.iloc[-64]) / (spy.iloc[-1] / spy.iloc[-64]) - 1),
                "volume_vs_50d": float(v[t].iloc[-1] / v[t].iloc[-51:-1].mean()) if t in v else None,
                "source": "Yahoo Finance daily bars"}
        opt = DATA / "options" / f"{t}.json"
        if opt.exists():
            snap["options"] = json.loads(opt.read_text())
        ivh = DATA / "iv_history.csv"
        if ivh.exists():
            iv = pd.read_csv(ivh)
            if "ticker" in iv:
                iv = iv[iv["ticker"] == t]
                if len(iv):
                    snap["iv_history"] = {"points": int(len(iv)), "last": iv.iloc[-1].to_dict()}
        snaps[t] = snap
    SNAPSHOTS.write_text(json.dumps(snaps, indent=1, default=str))
    return snaps


# ------------------------------------------------------------------ patterns
def patterns(bt: pd.DataFrame, ft: pd.DataFrame, closed: pd.DataFrame) -> List[Dict]:
    """Groups that meet the evidence standard. Two examples are never a pattern."""
    out = []

    def scan(df, source, a, b):
        if df is None or df.empty or a not in df or b not in df:
            return
        d = df.explode(a) if df[a].map(lambda x: isinstance(x, list)).any() else df
        for (x, y), g in d.groupby([a, b]):
            pf = learning.perf(g)
            if pf["n"] < learning.MIN_PRELIM or (pf.get("avg_r") or 0) <= 0:
                continue
            lo = (pf.get("ci95_mean_r") or [None])[0]
            status = "VALIDATED (evidence standard met)" if pf["evidence"].startswith("ESTABLISHED POSITIVE") else \
                "CANDIDATE (positive but not proven)"
            if source == "backtest" and status.startswith("VALIDATED"):
                status = "BACKTEST-SUPPORTED (needs forward confirmation before it counts)"
            by_t = g.groupby("ticker")["r"].agg(["mean", "count"])
            by_t = by_t[by_t["count"] >= 3].sort_values("mean")
            other = d[(d[a] == x) & (d[b] != y)]
            fail = {str(k): round(float(v), 3) for k, v in other.groupby(b)["r"].mean().items() if v < 0}
            out.append({"pattern": f"{x} × {y}", "dimension": f"{a} × {b}", "source": source, "status": status,
                        "n": pf["n"], "win_rate": pf["win_rate"], "avg_r": pf["avg_r"], "ci95_low": lo,
                        "avg_win_r": pf.get("avg_win_r"), "avg_loss_r": pf.get("avg_loss_r"),
                        "expected_value_r": pf["avg_r"], "tickers": pf["distinct_tickers"], "months": pf["months_covered"],
                        "best_securities": [f"{i} {r_['mean']:+.2f}R (n={int(r_['count'])})" for i, r_ in by_t.iloc[::-1][:3].iterrows()],
                        "worst_securities": [f"{i} {r_['mean']:+.2f}R (n={int(r_['count'])})" for i, r_ in by_t[:3].iterrows()],
                        "failure_conditions": fail, "evidence": pf["evidence"]})

    ind_bt = bt[(bt.get("status") == "resolved") & bt.get("independent", False)] if len(bt) else bt
    for b in ("trend", "risk", "volatility"):
        scan(ind_bt, "backtest", "setup_list", b)
    ind_ft = ft[(ft.get("status") == "resolved") & ft.get("independent", False)] if len(ft) else ft
    scan(ind_ft, "forward test", "setup_list", "regime_trend")
    scan(closed, "paper trades", "strategies", "regime_trend")
    scan(closed, "paper trades", "catalyst_category", "regime_trend")
    return sorted(out, key=lambda p: (-p["n"] * max(p["avg_r"], 0)))


# ------------------------------------------------------------------ experiments
def _filter(df: pd.DataFrame, spec: str) -> pd.DataFrame:
    """'setup=pullback;trend=Bull,Strong Bull;score>=7' → rows matching every clause (list values = any of)."""
    out = df
    for clause in [x.strip() for x in (spec or "").split(";") if x.strip()]:
        for op in (">=", "<=", "!=", "=", ">", "<"):
            if op in clause:
                k, v = [x.strip() for x in clause.split(op, 1)]
                break
        else:
            continue
        if k not in out:
            return out.iloc[0:0]
        col = out[k]
        if col.map(lambda x: isinstance(x, list)).any():
            out = out[col.map(lambda xs: any(str(x) in v.split(",") for x in xs))]
        elif op in ("=", "!="):
            m = col.astype(str).isin([x.strip() for x in v.split(",")])
            out = out[m if op == "=" else ~m]
        else:
            num = pd.to_numeric(col, errors="coerce")
            out = out[{">=": num >= float(v), "<=": num <= float(v), ">": num > float(v), "<": num < float(v)}[op]]
    return out


def evaluate_experiments(frames: Dict[str, pd.DataFrame]) -> List[Dict]:
    res = []
    for x in memory.experiments():
        df = frames.get(x["dataset"], pd.DataFrame())
        if len(df) and "independent" in df:
            df = df[df["independent"]]
        if len(df) and "status" in df:
            df = df[df["status"] == "resolved"]
        if len(df) and x["dataset"] in ("forward", "trades", "rejections") and "date" in df:
            df = df[df["date"].astype(str) >= x["created"][:10]]          # only data collected after the experiment began
        a, b = _filter(df, x["baseline_filter"]), _filter(df, x["alternative_filter"])
        pa, pb = learning.perf(a) if len(a) else {"n": 0}, learning.perf(b) if len(b) else {"n": 0}
        diff = ci = None
        if pa["n"] >= 2 and pb["n"] >= 2:
            diff = pb["avg_r"] - pa["avg_r"]
            se = math.sqrt(a["r"].var() / len(a) + b["r"].var() / len(b))
            ci = [diff - 1.96 * se, diff + 1.96 * se]
        need = x["min_n_per_arm"]
        if min(pa["n"], pb["n"]) < need:
            sug = f"Continue testing: {pa['n']} baseline / {pb['n']} alternative observations, need ≥ {need} each"
        elif ci and ci[0] > 0:
            sug = "Evidence favors the ALTERNATIVE (95% CI of the difference above 0). Check regimes/robustness, then propose a version to Samanyu."
        elif ci and ci[1] < 0:
            sug = "Evidence favors the BASELINE (alternative worse). Suggest: Keep baseline / Reject hypothesis."
        else:
            sug = "No significant difference at the required sample size. Suggest: Keep baseline (simpler) or continue testing."
        res.append({**x, "baseline": pa, "alternative": pb, "difference_mean_r": diff, "difference_ci95": ci, "suggestion": sug})
    return res


# ------------------------------------------------------------------ scorecards
def scorecard_eval(book: Dict, rj: pd.DataFrame, P) -> Dict[str, object]:
    cards = {c["id"]: c for c in memory.scorecards()}
    rows = []
    for t in book["trades"]:
        cid = t.get("scorecard_id")
        if cid in cards and t["status"] == "closed":
            rows.append({"score": cards[cid]["overall"], "r": t["r_multiple"], "kind": "trade"})
    if len(rj):
        for r in rj.to_dict("records"):
            if r.get("scorecard_id") in cards and r.get("status") == "resolved":
                rows.append({"score": cards[r["scorecard_id"]]["overall"], "r": r["hypothetical_r"], "kind": "rejected"})
    df = pd.DataFrame(rows)
    out = {"scorecards": len(cards), "with_outcomes": len(df), "evidence": _ev(len(df))}
    if len(df) >= 5:
        out["spearman_score_vs_r"] = float(df["score"].rank().corr(df["r"].rank()))
        df["bucket"] = pd.cut(df["score"], [0, 4, 6, 8, 10.01], labels=["0-4", "4-6", "6-8", "8-10"])
        out["by_bucket"] = {str(k): {"n": len(g), "avg_r": float(g["r"].mean())} for k, g in df.groupby("bucket", observed=True)}
    return out


# ------------------------------------------------------------------ similar conditions
def similar_conditions(bt: pd.DataFrame, ft: pd.DataFrame, closed: pd.DataFrame, k: int = 30) -> Dict[str, object]:
    h = regimes.history()
    if h.empty:
        return {}
    sim = regimes.similar_days(k=k, h=h)
    today = h.iloc[-1].to_dict()
    dates = set(sim["date"])
    out = {"today": {x: today.get(x) for x in ("date", "trend", "risk", "volatility", "vix", "breadth_50d", "tnx", "hy_spread",
                                                "dxy_1m", "leaders", "fed_signal")},
           "similar_days": sim[["date", "trend", "risk", "volatility", "distance"]].to_dict("records")}
    if len(bt):
        b = bt[bt["date"].isin(dates) & (bt["status"] == "resolved") & bt["independent"]]
        out["backtest_on_similar_days"] = {"overall": learning.perf(b) if len(b) else {"n": 0},
                                           "by_setup": learning.group_perf(b, "setup_list") if len(b) else {}}
        same = bt[(bt["trend"] == today.get("trend")) & (bt["risk"] == today.get("risk")) & (bt["status"] == "resolved") & bt["independent"]]
        out["backtest_same_regime_label"] = {"label": f"{today.get('trend')} / {today.get('risk')}",
                                             "by_setup": learning.group_perf(same, "setup_list") if len(same) else {}}
    if len(closed):
        out["paper_trades_same_regime"] = learning.perf(closed[closed.get("composite_trend") == today.get("trend")]) \
            if "composite_trend" in closed else {"n": 0}
    return out


# ------------------------------------------------------------------ errors
def error_stats() -> Dict[str, object]:
    es = memory.errors()
    if not es:
        return {"n": 0}
    df = pd.DataFrame(es)
    rep = df["error_type"].value_counts()
    return {"n": len(df), "by_type": rep.to_dict(), "by_status": df["status"].value_counts().to_dict(),
            "by_severity": df["severity"].value_counts().to_dict(),
            "repeated_types": {k: int(v) for k, v in rep[rep >= 3].items()},
            "note": "A type repeating ≥ 3 times is a candidate HYPOTHESIS about the process, not yet a rule change."}


def run(P, book: Dict, tf: pd.DataFrame, ft: pd.DataFrame, bt: pd.DataFrame, watchlist: List[str]) -> Dict[str, object]:
    ps = prediction_detail(memory.predictions(), P)
    es = event_detail(memory.events(), P)
    rj = rejection_outcomes(P)
    closed = tf[tf["status"] == "closed"] if len(tf) else tf
    sig = pd.read_csv(memory.SIGNALS) if memory.SIGNALS.exists() else pd.DataFrame()
    pri = priority_tickers(book, sig, watchlist)
    score = {c["id"]: c["overall"] for c in memory.scorecards()}
    ids = {t["id"]: t.get("scorecard_id") for t in book["trades"]}
    trades_f = closed.assign(date=closed["opened"].str[:10], score=closed["id"].map(lambda i: score.get(ids.get(i)))) if len(closed) else closed
    rej_f = rj.rename(columns={"hypothetical_r": "r"}).assign(date=rj["decided"].str[:10],
                                                              score=rj["scorecard_id"].map(lambda i: score.get(i))) if len(rj) else rj
    frames = {"backtest": bt, "forward": ft, "trades": trades_f, "rejections": rej_f}
    return {
        "calibration": calibration(ps),
        "rejections": rejection_stats(rj),
        "rejection_records": rj.replace({np.nan: None}).to_dict("records") if len(rj) else [],
        "opportunity_cost": opportunity_cost(book, rj, P),
        "benchmarks": benchmarks(book, P),
        "priority_companies": pri,
        "company_snapshots": company_snapshots(pri, P),
        "patterns": patterns(bt, ft, closed),
        "experiments": evaluate_experiments(frames),
        "scorecards": scorecard_eval(book, rj, P),
        "similar_conditions": similar_conditions(bt, ft, closed),
        "errors": error_stats(),
        "events_measured": sum(1 for e in es if (e.get("reaction") or {}).get("multi_asset")),
    }
