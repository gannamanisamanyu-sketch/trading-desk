"""Long-term memory datasets: predictions, events, screener signals, regime log and system versions.

Everything here is a plain JSON/CSV file under journal/ (committed to git, mirrored into Obsidian).
Outcomes are never typed in by hand: `desk.learning` measures them from market data.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Optional

import pandas as pd

from desk.common import DATA, JOURNAL, config, now_ny

PREDICTIONS = JOURNAL / "predictions.json"
EVENTS = JOURNAL / "events.json"
VERSIONS = JOURNAL / "system_versions.json"
SIGNALS = JOURNAL / "signals.csv"
REGIME_LOG = JOURNAL / "regime_log.csv"

STRATEGIES = ["Momentum", "Breakout", "Pullback", "Mean reversion", "Earnings", "Macro", "News-driven", "Political",
              "Geopolitical", "Sector rotation", "Catalyst", "Options directional", "Options spread", "Volatility"]
CATALYSTS = ["Fed", "Inflation", "Employment", "Elections", "Tariffs", "Regulation", "Geopolitics", "War", "Oil",
             "Earnings", "AI", "Technology", "Banking", "Healthcare", "Consumer", "Supply chains", "None"]
MACRO_CATS = {"Fed", "Inflation", "Employment", "Oil", "Banking", "Supply chains"}
POLITICAL_CATS = {"Elections", "Tariffs", "Regulation", "Geopolitics", "War"}
SETUP_TO_STRATEGY = {"breakout": "Breakout", "pullback": "Pullback", "rs_leader": "Momentum",
                     "oversold_uptrend": "Mean reversion", "breakdown": "Breakout", "news_mismatch": "News-driven",
                     "laggard_in_strong_sector": "Sector rotation"}
CONFIDENCE_PROB = {"low": 0.55, "medium": 0.65, "high": 0.75}   # implied hit rate used for calibration/Brier
TREND_MAP = {"STRONG BULL": "BULL", "BULL": "BULL", "NEUTRAL": "SIDEWAYS", "CHOPPY": "SIDEWAYS", "BEAR": "BEAR",
             "PANIC / RISK-OFF": "BEAR"}


# ------------------------------------------------------------------ generic json store
def _load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _save(path, obj) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


def _next(items: List[Dict]) -> int:
    return max([x["id"] for x in items], default=0) + 1


def check_choice(value: Optional[str], allowed: List[str], what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    m = {a.lower(): a for a in allowed}
    if value.lower() not in m:
        raise ValueError(f"unknown {what} '{value}'. Use one of: {', '.join(allowed)}")
    return m[value.lower()]


# ------------------------------------------------------------------ regime tags
def latest_brief() -> Optional[Dict]:
    files = sorted((DATA / "briefs").glob("*.json"))
    return json.loads(files[-1].read_text()) if files else None


def regime_tags(analyst_regime: str = "", brief: Optional[Dict] = None) -> Dict[str, object]:
    """Trend / volatility / risk tags for the moment a trade or prediction is recorded.

    Trend uses the analyst's regime when given (it reflects the full report), else the rule-based scan regime.
    """
    brief = brief or latest_brief() or {}
    r = brief.get("regime") or {}
    label = (analyst_regime or r.get("regime") or "").upper()
    vix = r.get("vix")
    vol = None if vix is None else ("LOW VOLATILITY" if vix < 16 else "HIGH VOLATILITY" if vix > 22 else "NORMAL VOLATILITY")
    if r.get("vix_term_inverted") or label in ("BEAR", "PANIC / RISK-OFF") or (vix or 0) > 25:
        risk = "RISK-OFF"
    elif r.get("spy_vs_50d", 0) > 0 and r.get("breadth_above_50d", 0) >= 0.5 and (vix or 99) < 20:
        risk = "RISK-ON"
    else:
        risk = "MIXED"
    return {"regime_trend": TREND_MAP.get(label), "regime_vol": vol, "regime_risk": risk,
            "regime_rule": r.get("regime"), "regime_source": brief.get("generated")}


# ------------------------------------------------------------------ system versions
PARAM_KEYS = ("account", "risk", "screening", "options")


def param_snapshot() -> Dict:
    cfg = config()
    return {k: cfg.get(k) for k in PARAM_KEYS}


def _hash(d: Dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:12]


def versions() -> List[Dict]:
    return _load(VERSIONS, [])


def current_version() -> str:
    return str((config().get("system") or {}).get("version", "1.0"))


def version_check() -> Dict[str, object]:
    """Compare the live config with the snapshot stored for the current version. Any difference = undocumented change."""
    cur = current_version()
    v = next((x for x in versions() if x["version"] == cur), None)
    if v is None:
        return {"ok": False, "version": cur, "problem": f"version {cur} is not documented in journal/system_versions.json"}
    now = param_snapshot()
    diffs = []
    for k in PARAM_KEYS:
        a, b = v["params"].get(k) or {}, now.get(k) or {}
        for kk in sorted(set(a) | set(b)):
            if a.get(kk) != b.get(kk):
                diffs.append(f"{k}.{kk}: {a.get(kk)} → {b.get(kk)}")
    return {"ok": not diffs, "version": cur, "diffs": diffs,
            "problem": "UNDOCUMENTED PARAMETER CHANGE since this version was recorded" if diffs else ""}


def new_version(version: str, name: str, changes: str, reason: str, evidence: str, expected_effect: str,
                approved_by: str) -> Dict:
    vs = versions()
    if any(x["version"] == version for x in vs):
        raise ValueError(f"version {version} already exists; versions are never overwritten")
    for f, val in (("changes", changes), ("reason", reason), ("evidence", evidence), ("expected effect", expected_effect),
                   ("approved by", approved_by)):
        if not val.strip():
            raise ValueError(f"a new version needs '{f}' documented")
    rec = {"version": version, "name": name, "date": str(now_ny().date()), "changes": changes, "reason": reason,
           "evidence": evidence, "expected_effect": expected_effect, "approved_by": approved_by,
           "previous": current_version(), "params": param_snapshot()}
    rec["params_hash"] = _hash(rec["params"])
    vs.append(rec)
    _save(VERSIONS, vs)
    return rec


# ------------------------------------------------------------------ predictions
def predictions() -> List[Dict]:
    return _load(PREDICTIONS, [])


def add_prediction(ticker: str, direction: str, days: int, confidence: str, reason: str, catalyst: str = "",
                   benchmark: str = "", magnitude: Optional[float] = None, category: str = "", strategy: str = "",
                   statement: str = "", regime: str = "", source: str = "manual") -> Dict:
    from desk.market import quote

    direction = direction.upper()
    if direction not in ("UP", "DOWN", "OUTPERFORM", "UNDERPERFORM"):
        raise ValueError("direction must be UP, DOWN, OUTPERFORM or UNDERPERFORM")
    if direction in ("OUTPERFORM", "UNDERPERFORM") and not benchmark:
        raise ValueError("relative predictions need --benchmark")
    if confidence.lower() not in CONFIDENCE_PROB:
        raise ValueError("confidence must be low, medium or high")
    tk = [ticker.upper()] + ([benchmark.upper()] if benchmark else [])
    q = quote(tk).set_index("ticker")
    if q["price"].isna().any():
        raise ValueError("could not get a live price to anchor the prediction; try again")
    created = now_ny()
    due = (pd.Timestamp(created.date()) + pd.offsets.BDay(int(days))).date()
    ps = predictions()
    rec = {"id": _next(ps), "created": created.strftime("%Y-%m-%d %H:%M"), "ticker": ticker.upper(),
           "statement": statement or _statement(ticker.upper(), direction, days, benchmark, magnitude),
           "direction": direction, "benchmark": benchmark.upper() or None, "horizon_days": int(days),
           "due": str(due), "magnitude": magnitude, "confidence": confidence.lower(), "reason": reason,
           "catalyst": catalyst, "category": check_choice(category, CATALYSTS, "category"),
           "strategy": check_choice(strategy, STRATEGIES, "strategy"), "source": source,
           "start_price": float(q.loc[ticker.upper(), "price"]), "start_time": q.loc[ticker.upper(), "time"],
           "bench_start": float(q.loc[benchmark.upper(), "price"]) if benchmark else None,
           "system_version": current_version(), **regime_tags(regime), "status": "pending"}
    ps.append(rec)
    _save(PREDICTIONS, ps)
    return rec


def _statement(t, d, n, b, m) -> str:
    mag = f" by at least {m:.0%}" if m else ""
    return {"UP": f"{t} rises{mag} over the next {n} trading days",
            "DOWN": f"{t} falls{mag} over the next {n} trading days",
            "OUTPERFORM": f"{t} outperforms {b}{mag} over the next {n} trading days",
            "UNDERPERFORM": f"{t} underperforms {b}{mag} over the next {n} trading days"}[d]


def save_predictions(ps: List[Dict]) -> None:
    _save(PREDICTIONS, ps)


# ------------------------------------------------------------------ events (news-reaction database)
def events() -> List[Dict]:
    return _load(EVENTS, [])


def add_event(title: str, date: str, category: str, expected: str, tickers: List[str], sectors: List[str],
              expected_spy: str = "", after_close: bool = False, source: str = "", notes: str = "") -> Dict:
    cat = check_choice(category, CATALYSTS, "category")
    if expected_spy and expected_spy.upper() not in ("UP", "DOWN", "FLAT"):
        raise ValueError("--expected-spy must be UP, DOWN or FLAT")
    es = events()
    rec = {"id": _next(es), "date": date, "after_close": after_close, "title": title, "category": cat,
           "expected_impact": expected, "expected_spy": expected_spy.upper() or None,
           "tickers": [t.upper() for t in tickers if t], "sectors": [s.upper() for s in sectors if s],
           "source": source, "notes": notes, "options_impact": "", "lesson": "",
           "recorded": now_ny().strftime("%Y-%m-%d %H:%M"), **regime_tags()}
    es.append(rec)
    _save(EVENTS, es)
    return rec


def update_event(eid: int, **fields) -> Dict:
    es = events()
    e = next(x for x in es if x["id"] == eid)
    e.update({k: v for k, v in fields.items() if v})
    _save(EVENTS, es)
    return e


def save_events(es: List[Dict]) -> None:
    _save(EVENTS, es)


# ------------------------------------------------------------------ signals + regime log (written at each scan)
def record_scan(out: Dict) -> int:
    """Append today's screener signals (first sighting per day/ticker/direction) and the regime snapshot."""
    gen = out.get("generated", "")
    day = gen[:10]
    sess = out.get("session", "")
    s = out.get("setups")
    n_new = 0
    if s is not None and len(s):
        rows = pd.DataFrame({"date": day, "generated": gen, "session": sess, "ticker": s["ticker"],
                             "direction": s["direction"], "setups": s["setups"].map(lambda x: "+".join(x)),
                             "entry_ref": s["entry"].round(4), "stop": s["stop"].round(4), "target": s["target"].round(4),
                             "reward_risk": s["reward_risk"].round(3), "score": s["score"],
                             "regime_rule": (out.get("regime") or {}).get("regime"),
                             "system_version": current_version()})
        old = pd.read_csv(SIGNALS) if SIGNALS.exists() else pd.DataFrame(columns=rows.columns)
        key = lambda d: d["date"].astype(str) + "|" + d["ticker"] + "|" + d["direction"]
        rows = rows[~key(rows).isin(set(key(old)))] if len(old) else rows
        n_new = len(rows)
        if n_new:
            pd.concat([old, rows], ignore_index=True).to_csv(SIGNALS, index=False)
    r = out.get("regime") or {}
    if r.get("regime"):
        row = {"date": day, "generated": gen, "regime_rule": r.get("regime"), "score": r.get("score"),
               "spy": r.get("spy"), "vix": r.get("vix"), "vix3m": r.get("vix3m"),
               "vix_term_inverted": r.get("vix_term_inverted"), "breadth_above_50d": r.get("breadth_above_50d"),
               "spy_vs_50d": r.get("spy_vs_50d"), "spy_vs_200d": r.get("spy_vs_200d"),
               "fed_signal": (out.get("fed_proxy") or {}).get("signal")}
        log = pd.read_csv(REGIME_LOG) if REGIME_LOG.exists() else pd.DataFrame(columns=list(row))
        log = pd.concat([log[log["date"].astype(str) != day], pd.DataFrame([row])], ignore_index=True)
        log.sort_values("date").to_csv(REGIME_LOG, index=False)
    return n_new
