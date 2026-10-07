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
CATALYSTS = ["Fed", "FOMC", "Inflation", "CPI", "PPI", "Employment", "GDP", "Elections", "Tariffs", "Legislation",
             "Regulation", "Geopolitics", "War", "Sanctions", "Oil", "Oil shock", "Banking", "Earnings",
             "Corporate announcement", "AI", "Technology", "Healthcare", "Consumer", "Supply chains", "Other", "None"]
MACRO_CATS = {"Fed", "FOMC", "Inflation", "CPI", "PPI", "Employment", "GDP", "Oil", "Oil shock", "Banking", "Supply chains"}
POLITICAL_CATS = {"Elections", "Tariffs", "Legislation", "Regulation", "Geopolitics", "War", "Sanctions"}
REJECTION_REASONS = ["Poor risk/reward", "Excessive IV", "Low liquidity", "Weak catalyst", "Market regime conflict",
                     "Position already too concentrated", "Risk limit", "Better opportunity elsewhere", "Earnings risk",
                     "News already priced in", "Technical confirmation missing", "Other"]
ERROR_TYPES = ["Incorrect interpretation of news", "Ignored contradictory evidence", "Overestimated catalyst",
               "Underestimated catalyst", "Incorrect market-regime classification", "Entered too early", "Entered too late",
               "Execution deviated from plan", "Poor options selection", "Excessive IV", "Excessive theta exposure",
               "Poor strike selection", "Poor expiration selection", "Position sizing error", "Confirmation bias",
               "Overconfidence", "Overreaction to social media", "Failed to recognize news was priced in", "Data error", "Other"]
STAGES = ["OBSERVATION", "HYPOTHESIS", "EXPERIMENT", "VALIDATED", "REJECTED"]
SCORE_FACTORS = ["catalyst", "fundamentals", "technical", "regime", "sector", "options", "liquidity", "risk_reward",
                 "timing", "contradictory"]
REJECTIONS = JOURNAL / "rejections.json"
ERRORS = JOURNAL / "errors.json"
EXPERIMENTS = JOURNAL / "experiments.json"
SCORECARDS = JOURNAL / "scorecards.json"
SETUP_TO_STRATEGY = {"breakout": "Breakout", "pullback": "Pullback", "rs_leader": "Momentum",
                     "oversold_uptrend": "Mean reversion", "breakdown": "Breakout", "news_mismatch": "News-driven",
                     "laggard_in_strong_sector": "Sector rotation"}
CONFIDENCE_PROB = {"low": 0.55, "medium": 0.65, "high": 0.75}   # default % when only a label is given
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
PARAM_KEYS = ("account", "risk", "screening", "options", "scorecard")


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


def conf_label(pct: float) -> str:
    return "low" if pct < 60 else "medium" if pct < 75 else "high"


def add_prediction(ticker: str, direction: str, days: int, confidence: str, reason: str, catalyst: str = "",
                   benchmark: str = "", magnitude: Optional[float] = None, category: str = "", strategy: str = "",
                   statement: str = "", regime: str = "", source: str = "manual", confidence_pct: Optional[float] = None,
                   supporting: str = "", contradicting: str = "", event_id: Optional[int] = None, kind: str = "price",
                   due: str = "", asset: str = "") -> Dict:
    """Record a prediction BEFORE its outcome is known.

    kind="price": about a ticker's price (anchored to a live quote now, scored automatically at the horizon).
    kind="event": about a non-price outcome (e.g. "CPI comes in above consensus"); resolved later with
                  `predict resolve` and a cited source.
    """
    from desk.market import quote

    pct = float(confidence_pct) if confidence_pct else CONFIDENCE_PROB.get((confidence or "medium").lower(), 0.65) * 100
    if not 50 <= pct <= 100:
        raise ValueError("--confidence-pct must be between 50 and 100 (below 50 means you predict the opposite)")
    if not reason.strip():
        raise ValueError("a prediction needs --reason")
    ps = predictions()
    created = now_ny()
    base = {"id": _next(ps), "created": created.strftime("%Y-%m-%d %H:%M"), "kind": kind, "confidence_pct": pct,
            "confidence": conf_label(pct), "reason": reason, "catalyst": catalyst, "supporting": supporting,
            "contradicting": contradicting, "category": check_choice(category, CATALYSTS, "category"),
            "strategy": check_choice(strategy, STRATEGIES, "strategy"), "event_id": event_id, "source": source,
            "system_version": current_version(), **regime_tags(regime), "status": "pending", "lesson": ""}
    if kind == "event":
        if not (statement and due):
            raise ValueError("event predictions need --statement and --due YYYY-MM-DD")
        rec = {**base, "ticker": None, "asset": asset or statement[:40], "statement": statement, "direction": direction.upper() or None,
               "benchmark": None, "horizon_days": None, "due": due, "magnitude": magnitude, "start_price": None}
    else:
        direction = direction.upper()
        if direction not in ("UP", "DOWN", "OUTPERFORM", "UNDERPERFORM"):
            raise ValueError("direction must be UP, DOWN, OUTPERFORM or UNDERPERFORM")
        if direction in ("OUTPERFORM", "UNDERPERFORM") and not benchmark:
            raise ValueError("relative predictions need --benchmark")
        tk = [ticker.upper()] + ([benchmark.upper()] if benchmark else [])
        q = quote(tk).set_index("ticker")
        if q["price"].isna().any():
            raise ValueError("could not get a live price to anchor the prediction; try again")
        due_d = (pd.Timestamp(created.date()) + pd.offsets.BDay(int(days))).date()
        rec = {**base, "ticker": ticker.upper(), "asset": ticker.upper(),
               "statement": statement or _statement(ticker.upper(), direction, days, benchmark, magnitude),
               "direction": direction, "benchmark": benchmark.upper() or None, "horizon_days": int(days), "due": str(due_d),
               "magnitude": magnitude, "start_price": float(q.loc[ticker.upper(), "price"]),
               "start_time": q.loc[ticker.upper(), "time"],
               "bench_start": float(q.loc[benchmark.upper(), "price"]) if benchmark else None}
    ps.append(rec)
    _save(PREDICTIONS, ps)
    return rec


def resolve_prediction(pid: int, correct: bool, actual: str, source: str, actual_magnitude: Optional[float] = None) -> Dict:
    """Resolve an event (non-price) prediction from a cited source."""
    ps = predictions()
    p = next(x for x in ps if x["id"] == pid)
    if p.get("kind") != "event":
        raise ValueError("price predictions are scored automatically by `desk.py learn`")
    if not source.strip():
        raise ValueError("resolving needs --source (where the actual result came from)")
    p.update({"status": "scored", "correct": bool(correct), "actual_result": actual, "actual_magnitude": actual_magnitude,
              "resolution_source": source, "scored_on": str(now_ny().date())})
    _save(PREDICTIONS, ps)
    return p


def update_prediction(pid: int, **fields) -> Dict:
    ps = predictions()
    p = next(x for x in ps if x["id"] == pid)
    p.update({k: v for k, v in fields.items() if v not in (None, "")})
    _save(PREDICTIONS, ps)
    return p


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
              expected_spy: str = "", after_close: bool = False, source: str = "", notes: str = "",
              what_happened: str = "", market_expected: str = "", actual_vs_expected: str = "", time: str = "",
              reliability: str = "", interpretation: str = "", second_order: str = "") -> Dict:
    cat = check_choice(category, CATALYSTS, "category")
    if expected_spy and expected_spy.upper() not in ("UP", "DOWN", "FLAT"):
        raise ValueError("--expected-spy must be UP, DOWN or FLAT")
    es = events()
    rec = {"id": _next(es), "date": date, "after_close": after_close, "title": title, "category": cat,
           "expected_impact": expected, "expected_spy": expected_spy.upper() or None,
           "tickers": [t.upper() for t in tickers if t], "sectors": [s.upper() for s in sectors if s],
           "source": source, "notes": notes, "options_impact": "", "lesson": "", "time": time or None,
           "what_happened": what_happened, "market_expected": market_expected, "actual_vs_expected": actual_vs_expected,
           "reliability": reliability, "interpretation": interpretation, "second_order": second_order,
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


# ------------------------------------------------------------------ rejected trades (learning from trades NOT taken)
def rejections() -> List[Dict]:
    return _load(REJECTIONS, [])


def add_rejection(ticker: str, direction: str, instrument: str, entry: float, stop: float, target: float, reason: str,
                  thesis: str, catalyst: str = "", quantity: Optional[int] = None, alternative: str = "",
                  strategy: str = "", scorecard_id: Optional[int] = None, event_id: Optional[int] = None,
                  notes: str = "", decided: str = "", regime: str = "") -> Dict:
    from desk import paper, risk

    reason = check_choice(reason, REJECTION_REASONS, "rejection reason")
    direction = direction.upper()
    if quantity is None and instrument == "stock":
        acct = paper.account(paper.load())
        quantity = risk.size_stock(acct["equity"], entry, stop, target, acct["open_risk"], direction).quantity
    rr = abs(target - entry) / abs(entry - stop) if entry != stop else None
    rs = rejections()
    rec = {"id": _next(rs), "decided": decided or now_ny().strftime("%Y-%m-%d %H:%M"), "ticker": ticker.upper(),
           "direction": direction, "instrument": instrument, "entry": float(entry), "stop": float(stop),
           "target": float(target), "quantity": quantity, "risk_usd": (quantity or 0) * abs(entry - stop),
           "expected_return": abs(target - entry) / entry * (1 if direction == "LONG" else 1), "reward_risk": rr,
           "catalyst": catalyst, "thesis": thesis, "reason": reason, "alternative": alternative,
           "strategy": check_choice(strategy, STRATEGIES, "strategy"), "scorecard_id": scorecard_id, "event_id": event_id,
           "notes": notes, "system_version": current_version(), **regime_tags(regime),
           "process_verdict": "", "lesson": ""}
    rs.append(rec)
    _save(REJECTIONS, rs)
    return rec


def update_rejection(rid: int, **fields) -> Dict:
    rs = rejections()
    r = next(x for x in rs if x["id"] == rid)
    r.update({k: v for k, v in fields.items() if v not in (None, "")})
    _save(REJECTIONS, rs)
    return r


# ------------------------------------------------------------------ analytical errors (process failures, not losses)
def errors() -> List[Dict]:
    return _load(ERRORS, [])


def add_error(subject: str, error_type: str, original_reasoning: str, what_happened: str, what_missed: str,
              root_cause: str, severity: str, lesson: str, proposed_correction: str = "", evidence: str = "",
              date: str = "", trade_id: Optional[int] = None, prediction_id: Optional[int] = None,
              event_id: Optional[int] = None, source: str = "") -> Dict:
    if severity.lower() not in ("low", "medium", "high"):
        raise ValueError("--severity must be low, medium or high")
    es = errors()
    rec = {"id": _next(es), "date": date or str(now_ny().date()), "recorded": now_ny().strftime("%Y-%m-%d %H:%M"),
           "subject": subject, "error_type": check_choice(error_type, ERROR_TYPES, "error type"),
           "original_reasoning": original_reasoning, "what_happened": what_happened, "what_missed": what_missed,
           "root_cause": root_cause, "severity": severity.lower(), "lesson": lesson,
           "proposed_correction": proposed_correction, "evidence": evidence or "single occurrence (n=1)",
           "status": "OBSERVATION", "trade_id": trade_id, "prediction_id": prediction_id, "event_id": event_id,
           "source": source, "system_version": current_version()}
    es.append(rec)
    _save(ERRORS, es)
    return rec


def update_error(eid: int, **fields) -> Dict:
    es = errors()
    e = next(x for x in es if x["id"] == eid)
    if fields.get("status"):
        fields["status"] = check_choice(fields["status"], STAGES, "status")
    e.update({k: v for k, v in fields.items() if v not in (None, "")})
    _save(ERRORS, es)
    return e


# ------------------------------------------------------------------ strategy experiments
def experiments() -> List[Dict]:
    return _load(EXPERIMENTS, [])


def add_experiment(name: str, hypothesis: str, reason: str, dataset: str, baseline: str, alternative: str,
                   min_n: int = 30, notes: str = "") -> Dict:
    if dataset not in ("backtest", "forward", "trades", "rejections"):
        raise ValueError("--dataset must be backtest, forward, trades or rejections")
    xs = experiments()
    rec = {"id": _next(xs), "name": name, "created": now_ny().strftime("%Y-%m-%d %H:%M"), "hypothesis": hypothesis,
           "reason": reason, "dataset": dataset, "baseline_filter": baseline, "alternative_filter": alternative,
           "metric": "R multiple", "min_n_per_arm": int(min_n), "status": "running", "decision": "",
           "decision_evidence": "", "notes": notes, "system_version": current_version()}
    xs.append(rec)
    _save(EXPERIMENTS, xs)
    return rec


def update_experiment(xid: int, **fields) -> Dict:
    xs = experiments()
    x = next(e for e in xs if e["id"] == xid)
    if fields.get("decision") and fields["decision"] not in ("Keep baseline", "Adopt alternative", "Continue testing",
                                                              "Reject hypothesis"):
        raise ValueError("decision must be: Keep baseline | Adopt alternative | Continue testing | Reject hypothesis")
    if fields.get("decision") == "Adopt alternative":
        fields.setdefault("notes", "")
        fields["notes"] += " Adopting requires Samanyu's approval and a new system version (desk.py version new)."
    if fields.get("decision") in ("Keep baseline", "Adopt alternative", "Reject hypothesis"):
        fields["status"] = "closed"
    x.update({k: v for k, v in fields.items() if v not in (None, "")})
    _save(EXPERIMENTS, xs)
    return x


# ------------------------------------------------------------------ decision scorecards (stored before outcomes)
def scorecards() -> List[Dict]:
    return _load(SCORECARDS, [])


def score_weights(strategy: str) -> Dict[str, float]:
    sc = config().get("scorecard") or {}
    w = dict(sc.get("default_weights") or {f: 1.0 for f in SCORE_FACTORS})
    w.update((sc.get("strategy_weights") or {}).get(strategy or "", {}))
    return w


def add_scorecard(ticker: str, direction: str, strategy: str, scores: Dict[str, float], notes: str = "") -> Dict:
    missing = [f for f in SCORE_FACTORS if scores.get(f) is None]
    if missing:
        raise ValueError("score every factor (0-10): missing " + ", ".join(missing))
    if any(not 0 <= v <= 10 for v in scores.values()):
        raise ValueError("scores must be between 0 and 10")
    strategy = check_choice(strategy, STRATEGIES, "strategy") or ""
    w = score_weights(strategy)
    overall = sum(scores[f] * w[f] for f in SCORE_FACTORS) / sum(w[f] for f in SCORE_FACTORS)
    gate = (config().get("scorecard") or {}).get("gate", {})
    capped = [f for f, lim in gate.items() if scores.get(f, 10) <= lim]
    if capped:
        overall = min(overall, float((config().get("scorecard") or {}).get("gate_cap", 4)))
    cs = scorecards()
    rec = {"id": _next(cs), "created": now_ny().strftime("%Y-%m-%d %H:%M"), "ticker": ticker.upper(),
           "direction": direction.upper(), "strategy": strategy, "scores": scores, "weights": w,
           "overall": round(overall, 2), "gated_by": capped, "notes": notes, "system_version": current_version(),
           **regime_tags()}
    cs.append(rec)
    _save(SCORECARDS, cs)
    return rec
