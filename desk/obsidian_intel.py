"""Vault rendering for the V1.1 intelligence layer. Extends the existing notes; adds only what was missing.

New notes:   02 - Trades/Rejected Trades · 09 - Strategy Research/{Error Database, Pattern Database, Screener Backtest}
             09 - Strategy Research/Experiments/X<n> <name> · 10 - Performance/Decision Scorecards
             06 - Macro/Events/<event> and 07 - Politics & Geopolitics/Events/<event> (one note per event)
             04 - Companies/Sectors/<sector>
Extended:    Prediction Accuracy (database cards + calibration), Regime History / Regime Playbook / Macro Log
             (composite regime database), company notes (intelligence sections for priority companies),
             Trading Constitution + Daily Workflow (appended sections), weekly/monthly notes (intelligence/audit).
Auto content stays between desk:auto markers; manual sections are created once and never overwritten.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

import pandas as pd

from desk import memory, regimes
from desk.common import config, now_ny, universe
from desk.obsidian import (PERF_HEAD, _di, _front, _m, _n, _once, _p, _perf_rows, _pp, _r, _slug, _table, _trade_name,
                           _upsert)

ERROR_PENDING = "<!-- error-review:pending -->"


# ------------------------------------------------------------------ naming
def event_note(e: Dict) -> str:
    t = e["title"].replace(":", ".").replace("$", "")
    t = re.sub(r"[\\/*?\"<>|#^\[\]]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 60:
        t = t[:60].rsplit(" ", 1)[0]
    return f"{e['date']} {t.rstrip(' ,;-.')}"


def event_folder(e: Dict) -> str:
    return "07 - Politics & Geopolitics/Events" if e["category"] in memory.POLITICAL_CATS else "06 - Macro/Events"


def sector_note(name: str) -> str:
    return f"Sector - {name.replace('/', '&')}"


def _append_once(path: Path, marker: str, text: str) -> bool:
    """Append a section to an existing manual note once (never rewrites existing text)."""
    if not path.exists():
        return False
    s = path.read_text(encoding="utf-8")
    if marker in s:
        return False
    path.write_text(s.rstrip() + "\n\n" + text.strip() + "\n", encoding="utf-8")
    return True


def _insert_before_auto(path: Path, marker: str, text: str) -> bool:
    """Insert a manual section just before the AUTO block (once)."""
    from desk.obsidian import START

    if not path.exists():
        return False
    s = path.read_text(encoding="utf-8")
    if marker in s or START not in s:
        return False
    i = s.index(START)
    path.write_text(s[:i] + text.strip() + "\n\n" + s[i:], encoding="utf-8")
    return True


# ------------------------------------------------------------------ static additions (appended once)
CONSTITUTION_ADD = """
## 8. Data quality: FACT · INTERPRETATION · PREDICTION (added V1.1)
Every important item carries a date, a source, a timestamp when relevant and a reliability note when useful. The
three kinds of statements are never mixed in one sentence:
- **FACT**: observed and sourced. *CPI rose 0.4% m/m (BLS, 08:30 ET).*
- **INTERPRETATION**: what the fact probably means, with a confidence. *Raises the odds of higher-for-longer rates (medium).*
- **PREDICTION**: a testable call with horizon and confidence, recorded with `desk.py predict add` **before** the outcome.
  *Growth underperforms value over the next 5 sessions (60%).*
Event notes, daily notes and company files use these three headings.

## 9. Evidence from the backtest, rejected trades and scorecards (added V1.1)
- **Backtest** results (point-in-time screener replay) carry survivorship and no-cost biases. They can justify a
  HYPOTHESIS or EXPERIMENT, never a VALIDATED change on their own. A pattern needs forward/paper confirmation.
- Overlapping signals (same ticker and direction while an earlier one is still running) are counted once.
- A **rejected trade that later went up is not automatically a mistake**: judge whether the rejection reason was
  valid with what was known at the time. Hypothetical outcomes use the same simulator as the backtest.
- **Scorecards** are recorded before the outcome. Their weights are judgment-based until experiment data shows the
  score predicts results.
- An analytical **error** is a process failure, not a losing trade. One error never changes the strategy. Repeated
  error types (≥ 3) become a HYPOTHESIS about the process.
"""

WORKFLOW_ADD = """
## V1.1 intelligence layer: what Claude reads and writes
**Before the report:** `desk.py context --tickers <shortlist> --categories <today's event types>` gives the memory
review in priority order: (1) composite regime + outcomes on the most similar past days · (2) recent predictions and
calibration · (3) similar past events · (4) similar trades · (5) company files · (6) recent mistakes · (7) patterns
relevant to today's regime · (8) running experiments · (9) options learning · (10) exposure. It retrieves only
what's relevant, not the whole vault. Then open the linked company, event and sector notes as needed.

**While deciding, recorded BEFORE the outcome is known:**
- `desk.py score add TICKER ...` for every serious candidate (10 factors, strategy-weighted) → pass `--scorecard N`
- `desk.py predict add ... --confidence-pct 65 --supporting "..." --contradicting "..."` (price calls), or
  `--kind event --statement "..." --due YYYY-MM-DD` (non-price calls like "CPI above consensus")
- `desk.py reject add TICKER --entry --stop --target --reason "..." --thesis "..." --alternative "..."` for every
  serious candidate that was analyzed but not taken
- `desk.py event add ... --what-happened --market-expected --time HH:MM --interpretation` for material events;
  link trades with `--event-ids`

**End of day:** `desk.py learn` measures everything; then `desk.py error add ...` for each process failure
found in the self-critique, `desk.py predict update --id N --lesson`, `desk.py reject update --id N --verdict --lesson`.
"""

TEMPLATES_ADD = {
    "02 - Trades/Trade Templates/Rejected Trade Template.md": """
# Rejected trade (record with `desk.py reject add`; the outcome is simulated automatically)
Date · Ticker · Direction · Instrument · Proposed entry / stop / target · Position size · Expected return · R:R
Catalyst · Thesis · **Reason rejected** (Poor risk/reward · Excessive IV · Low liquidity · Weak catalyst · Market regime
conflict · Position already too concentrated · Risk limit · Better opportunity elsewhere · Earnings risk · News already
priced in · Technical confirmation missing) · Alternative selected
Later (auto): what happened afterward · hypothetical P/L
Later (Claude): was the rejection correct **given what was known then**? (`--verdict`) · lesson
""",
    "00 - System/Templates/Error Template.md": """
# Analytical error (record with `desk.py error add`)
Date · Ticker/event · Original reasoning · What happened · What was missed · Root cause · Severity (low/medium/high)
Lesson · Proposed correction · Evidence supporting the correction · Status: OBSERVATION → HYPOTHESIS → EXPERIMENT → VALIDATED / REJECTED
A losing trade is not an error. One error never changes the strategy.
""",
    "00 - System/Templates/Scorecard Template.md": """
# Decision scorecard (record with `desk.py score add`, BEFORE the outcome is known)
Catalyst /10 · Fundamentals /10 · Technical setup /10 · Market regime /10 · Sector strength /10 · Options structure /10 ·
Liquidity /10 · Risk/reward /10 · Timing /10 · Contradictory evidence /10 (10 = weak or fully addressed)
Overall = strategy-weighted (weights in config `scorecard`); risk/reward or liquidity ≤ 3 caps the overall at 4.
""",
    "00 - System/Templates/Company Intelligence Template.md": """
## Current Thesis
**Stance:** Bull / Neutral / Bear · as of YYYY-MM-DD · confidence
## Business
## Growth Drivers
-
## Catalysts
-
## Risks
-
## Bull Case
-
## Base Case
-
## Bear Case
-
## What Would Change The Thesis?
-
## Lessons
(what the system has learned about this company, with dates and evidence)
""",
}

COMPANY_SKELETON = """
<!-- company:intel -->
## Current Thesis
**Stance:** _not yet assessed_ · as of — · confidence —
_FACT / INTERPRETATION / PREDICTION kept separate. Fundamentals, technicals, options and history are auto-filled below._

## Business
-

## Growth Drivers
-

## Catalysts
-

## Risks
-

## Bull Case
-

## Base Case
-

## Bear Case
-

## What Would Change The Thesis?
-

## Lessons
-
"""


def static(root: Path, mark) -> None:
    S = root / "00 - System"
    mark(_append_once(S / "Trading Constitution.md", "## 8. Data quality", CONSTITUTION_ADD), S / "Trading Constitution.md")
    mark(_append_once(S / "Daily Workflow.md", "## V1.1 intelligence layer", WORKFLOW_ADD), S / "Daily Workflow.md")
    for rel, txt in TEMPLATES_ADD.items():
        mark(_once(root / rel, txt), root / rel)
    bt = root / "09 - Strategy Research" / "Backtests.md"
    mark(_append_once(bt, "[[Screener Backtest]]", "## This desk's backtest (added V1.1)\n- [[Screener Backtest]]: point-in-time "
                      "replay of the screener with the same simulator as the forward test. Read its bias notes first."), bt)
    pipe = root / "09 - Strategy Research" / "Improvement Pipeline.md"
    mark(_append_once(pipe, "## 🔗 Databases (V1.1)", "## 🔗 Databases (V1.1)\n- Process failures: [[Error Database]] · Repeatable "
                      "successes: [[Pattern Database]] · Formal experiments: `Experiments/` · Trades not taken: [[Rejected Trades]] · "
                      "Pre-trade scores: [[Decision Scorecards]] · Historical evidence: [[Screener Backtest]]"), pipe)


# ------------------------------------------------------------------ predictions (database + calibration)
def predictions_block(L: Dict) -> str:
    I = L.get("intelligence") or {}
    cal = I.get("calibration") or {}
    ps = memory.predictions()
    acc = cal.get("accuracy") or {}
    ev = {e["id"]: e for e in memory.events()}
    out = ["## Confidence calibration",
           f"Scored {cal.get('scored', 0)} · {cal.get('evidence', '')}. A bucket is flagged only with ≥ 10 predictions and a "
           "statistically significant gap.",
           _table(["Confidence", "n", "Hit rate", "95% CI", "Expected", "Flag"],
                  [[b["bucket"], b["n"], _pp(b["hit_rate"]), (f"{_pp(b['ci95'][0])}-{_pp(b['ci95'][1])}" if b.get("ci95") else "n/a"),
                    _pp(b["expected"]), b["flag"]] for b in cal.get("buckets", [])]),
           "", "**Are high-confidence calls better than low-confidence ones?** " +
           (json.dumps(cal["high_vs_low"]) if cal.get("high_vs_low") else "needs ≥ 10 scored predictions at ≥ 75% and at < 65%"),
           "", "## Accuracy dimensions",
           f"- Directional: {_pp(acc.get('directional'))} · magnitude met: {_pp(acc.get('magnitude_met'))} · median "
           f"actual/expected magnitude: {_n(acc.get('median_magnitude_ratio'))}",
           f"- Timing: {acc.get('timing') or 'no scored predictions yet'}", "",
           f"## Prediction database ({len(ps)})"]
    for p in reversed(ps):
        res = ("pending (due " + p["due"] + ")") if p.get("status") != "scored" else \
            ("✔ correct" if p.get("correct") else "✘ incorrect")
        link = f"[[{p['ticker']}]]" if p.get("ticker") else p.get("asset", "")
        e = ev.get(p.get("event_id"))
        out += [f"> [!note]- P{p['id']} · {p['created'][:10]} · {link} · {p['statement']} · **{res}**",
                f"> - **Direction / magnitude / horizon:** {p.get('direction')} · {_p(p.get('magnitude'))} · "
                f"{(str(p.get('horizon_days')) + ' trading days') if p.get('horizon_days') else 'until ' + p['due']} · benchmark {p.get('benchmark') or 'none'}",
                f"> - **Confidence:** {p.get('confidence_pct', '?'):.0f}% ({p.get('confidence')}) · category {p.get('category') or '—'} · strategy {p.get('strategy') or '—'}",
                f"> - **Reasoning:** {p.get('reason')}", f"> - **Catalyst:** {p.get('catalyst') or '—'}"
                + (f" · event [[{event_note(e)}]]" if e else ""),
                f"> - **Supporting evidence:** {p.get('supporting') or '—'}", f"> - **Contradicting evidence:** {p.get('contradicting') or '—'}",
                f"> - **Anchor:** {_m(p.get('start_price'))} {p.get('start_time') or ''} · regime {p.get('regime_trend')} / {p.get('regime_vol')}",
                f"> - **Actual result:** {p.get('actual_result') or (_p(p.get('actual_return')) if p.get('actual_return') is not None else '—')}"
                f" · vs benchmark {_p(p.get('relative_return'))} · magnitude ratio {_n(p.get('magnitude_ratio'))} · timing {p.get('timing') or '—'}",
                f"> - **Lesson:** {p.get('lesson') or '—'}", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ rejected trades + opportunity cost
def rejections_note(L: Dict) -> str:
    I = L.get("intelligence") or {}
    st = I.get("rejections") or {}
    recs = I.get("rejection_records") or []
    by_id = {r["id"]: r for r in recs}
    out = ["Trades analyzed but **not taken**, simulated with the same method as everything else (next open after the "
           "decision, proposed stop/target, 20-day limit). **A rejected trade that went up is not automatically a mistake.** "
           "Judge whether the reason was valid with what was known then (`--verdict`).", "",
           f"Total {st.get('total', 0)} · resolved {st.get('resolved', 0)} · outcome agreed with the rejection in "
           f"{_pp(st.get('share_agreeing_with_rejection'))} of resolved cases.", "",
           "## By rejection reason (hypothetical R)", _table(PERF_HEAD, _perf_rows(st.get("by_reason") or {}, money=False)), "",
           "## Opportunity cost (selected vs alternatives, all simulated identically)"]
    oc = I.get("opportunity_cost") or []
    out.append(_table(["Date", "Selected", "Alternatives", "Best so far", "Selection optimal?"],
                      [[o["date"], "; ".join(f"{x['label']} {_r(x['r'])}" for x in o["selected"]),
                        "; ".join(f"{x['label']} {_r(x['r'])}" for x in o["alternatives"]), o.get("best"),
                        {True: "yes", False: "no", None: "pending (not all resolved)"}[o.get("selection_optimal")]] for o in oc]))
    out += ["", f"## Rejected trades ({len(memory.rejections())})"]
    for r in reversed(memory.rejections()):
        x = by_id.get(r["id"], {})
        out += [f"> [!note]- R{r['id']} · {r['decided']} · [[{r['ticker']}]] {r['direction']} · **{r['reason']}** · "
                f"hypothetical {_r(x.get('hypothetical_r'))} ({x.get('status', 'pending')})",
                f"> - **Proposed:** {r['instrument']} entry {_m(r['entry'])} · stop {_m(r['stop'])} · target {_m(r['target'])} · "
                f"size {r.get('quantity')} · risk {_m(r.get('risk_usd'))} · expected {_p(r.get('expected_return'))} · R:R {_n(r.get('reward_risk'), 1)}",
                f"> - **Catalyst:** {r.get('catalyst') or '—'}", f"> - **Thesis:** {r.get('thesis') or '—'}",
                f"> - **Alternative selected:** {r.get('alternative') or '—'} · regime {r.get('regime_trend')} / {r.get('regime_vol')}",
                f"> - **What happened afterward:** {x.get('sim_exit_reason') or x.get('status') or 'pending'}"
                f"{(' on ' + x['sim_exit_date']) if x.get('sim_exit_date') else ''} · hypothetical P/L {_m(x.get('hypothetical_pnl'))}"
                f" · {x.get('outcome_note') or ''}",
                f"> - **Rejection correct (process)?** {r.get('process_verdict') or '_to assess_'} · **Lesson:** {r.get('lesson') or '—'}",
                f"> - Notes: {r.get('notes') or '—'}", ""]
    return "\n".join(out)


# ------------------------------------------------------------------ errors, patterns, experiments, scorecards, backtest
def errors_note(L: Dict, book: Dict) -> str:
    names = {t["id"]: _trade_name(t) for t in book["trades"]}
    st = (L.get("intelligence") or {}).get("errors") or {}
    es = memory.errors()
    out = ["Process failures in the **decision-making**, not losing trades. A perfectly executed trade can lose; a lucky "
           "trade can win with bad process. One error never changes the strategy (status starts at OBSERVATION).", "",
           f"Total {st.get('n', 0)} · by status {st.get('by_status', {})} · by severity {st.get('by_severity', {})}",
           f"Repeated types (≥ 3, candidate process HYPOTHESIS): {st.get('repeated_types') or 'none'}", "",
           "## By type", _table(["Error type", "Count"], [[k, v] for k, v in (st.get("by_type") or {}).items()]), "", "## Errors"]
    for e in reversed(es):
        links = " · ".join(x for x in ([f"[[{names[e['trade_id']]}]]"] if e.get("trade_id") in names else []) +
                           ([f"prediction P{e['prediction_id']}"] if e.get("prediction_id") else []) +
                           ([f"event #{e['event_id']}"] if e.get("event_id") else []))
        out += [f"> [!warning]- E{e['id']} · {e['date']} · {e['subject']} · **{e['error_type']}** · {e['severity']} · {e['status']}",
                f"> - **Original reasoning:** {e['original_reasoning']}", f"> - **What happened:** {e['what_happened']}",
                f"> - **What was missed:** {e['what_missed']}", f"> - **Root cause:** {e['root_cause']}",
                f"> - **Lesson:** {e['lesson']}", f"> - **Proposed correction:** {e.get('proposed_correction') or '—'}",
                f"> - **Evidence supporting the correction:** {e.get('evidence')}", f"> - Links: {links or '—'} · source {e.get('source') or '—'}", ""]
    return "\n".join(out)


def patterns_note(L: Dict) -> str:
    pats = (L.get("intelligence") or {}).get("patterns") or []
    out = ["Repeatable situations where the system did well, **only** when the evidence standard is approached. "
           "One or two examples are never a pattern. Statuses: **VALIDATED** (paper/forward evidence meets the standard) · "
           "**BACKTEST-SUPPORTED** (backtest only: needs forward confirmation) · **CANDIDATE** (n ≥ 20 and positive, not proven).", "",
           _table(["Pattern", "Source", "Status", "n", "Win %", "Avg R", "95% CI low", "Avg win", "Avg loss", "Tickers", "Months"],
                  [[p["pattern"], p["source"], p["status"], p["n"], _pp(p["win_rate"]), _r(p["avg_r"]), _n(p["ci95_low"]),
                    _r(p.get("avg_win_r")), _r(p.get("avg_loss_r")), p["tickers"], p["months"]] for p in pats]), ""]
    for p in pats[:12]:
        out += [f"### {p['pattern']} ({p['source']})",
                f"- **Status:** {p['status']} · {p['evidence']}",
                f"- **Conditions required:** {p['dimension'].replace('_list', '')} = {p['pattern']}",
                f"- **Typical entry/stop/target:** screener rules (next open · 1.5× ATR stop · ≥ 2R target) — see [[Strategy]]",
                f"- **Sample / win rate / average / EV:** n={p['n']} · {_pp(p['win_rate'])} · win {_r(p.get('avg_win_r'))} / loss {_r(p.get('avg_loss_r'))} · EV {_r(p['avg_r'])}",
                f"- **Best securities:** {', '.join(p['best_securities']) or 'n/a (need ≥ 3 per ticker)'}",
                f"- **Worst securities:** {', '.join(p['worst_securities']) or 'n/a'}",
                f"- **Failure conditions** (same setup, other regimes with negative average R): {p['failure_conditions'] or 'none found'}",
                f"- **Confidence in pattern:** {'medium' if p['status'].startswith('VALIDATED') else 'low'} — {p['status'].split(' (')[0]}", ""]
    return "\n".join(out)


def experiment_note(x: Dict) -> str:
    a, b = x.get("baseline") or {}, x.get("alternative") or {}
    ci = x.get("difference_ci95")
    return "\n".join([
        f"**Status:** {x['status']} · created {x['created']} · dataset **{x['dataset']}** · min {x['min_n_per_arm']} per arm", "",
        f"## Hypothesis\n{x['hypothesis']}", f"## Reason\n{x['reason']}",
        f"## Experiment\nCompare `{x['baseline_filter'] or '(all)'}` (baseline) with `{x['alternative_filter']}` (alternative) on "
        f"{x['metric']}. Only independent, resolved observations count"
        + ("; only data collected after the experiment began." if x["dataset"] in ("forward", "trades", "rejections") else "."),
        "## Baseline", f"n={a.get('n', 0)} · win {_pp(a.get('win_rate'))} · avg {_r(a.get('avg_r'))} · {a.get('evidence', '')}",
        "## Alternative", f"n={b.get('n', 0)} · win {_pp(b.get('win_rate'))} · avg {_r(b.get('avg_r'))} · {b.get('evidence', '')}",
        "## Sample size", f"Required ≥ {x['min_n_per_arm']} per arm.",
        "## Results", f"- Difference in mean R (alternative − baseline): {_r(x.get('difference_mean_r'))}",
        "## Statistical evidence", f"- 95% CI of the difference: {('[' + _n(ci[0]) + ', ' + _n(ci[1]) + ']') if ci else 'n/a'}",
        f"- Suggestion (automatic, not a decision): {x['suggestion']}",
        "## Decision", f"{x.get('decision') or '_none yet_'} {('· ' + x['decision_evidence']) if x.get('decision_evidence') else ''}",
        "Options: Keep baseline · Adopt alternative (needs Samanyu's approval + new version) · Continue testing · Reject hypothesis",
        "", "Back to [[Improvement Pipeline]]"])


def scorecards_note(L: Dict) -> str:
    ev = (L.get("intelligence") or {}).get("scorecards") or {}
    cards = memory.scorecards()
    w = config().get("scorecard") or {}
    return "\n".join([
        "Every serious candidate is scored **before** its outcome is known (prevents hindsight). Weights depend on the strategy; "
        f"a risk/reward or liquidity score ≤ 3 caps the overall at {w.get('gate_cap', 4)}. Weights are judgment-based until "
        "evidence shows the score predicts results.", "",
        f"## Does the score predict outcomes? {ev.get('evidence', '')}",
        f"- Scorecards {ev.get('scorecards', 0)} · with outcomes {ev.get('with_outcomes', 0)} · Spearman(score, R) "
        f"{_n(ev.get('spearman_score_vs_r'))} · by bucket {ev.get('by_bucket') or 'n/a'}", "",
        "## Weights (from config, V" + memory.current_version() + ")", "```json", json.dumps({"default": w.get("default_weights"),
                                                                                             "by_strategy": w.get("strategy_weights")}, indent=1), "```",
        f"## Scorecards ({len(cards)})",
        _table(["#", "Created", "Ticker", "Dir", "Strategy"] + [f.replace("_", "/")[:6] for f in memory.SCORE_FACTORS] + ["Overall", "Gated"],
               [[f"S{c['id']}", c["created"], f"[[{c['ticker']}]]", c["direction"], c["strategy"]] +
                [c["scores"][f] for f in memory.SCORE_FACTORS] + [c["overall"], ", ".join(c["gated_by"]) or "—"] for c in reversed(cards)])])


def backtest_note(L: Dict) -> str:
    B = L.get("backtest") or {}
    st = B.get("setup_by_trend") or {}
    trends = ["Strong Bull", "Bull", "Neutral", "Choppy", "Bear", "Strong Bear"]
    return "\n".join([
        f"**Point-in-time replay of the screener** · period {B.get('period')} · {B.get('signals', 0)} raw signals → "
        f"**{B.get('independent_resolved', 0)} independent resolved** (overlaps removed).",
        f"> [!warning] Biases: {B.get('biases')}. Backtest evidence can start a HYPOTHESIS or EXPERIMENT; it cannot validate a change.", "",
        "## Overall", _table(PERF_HEAD, _perf_rows({"All setups": B.get("overall") or {}}, money=False)), "",
        "## By setup", _table(PERF_HEAD, _perf_rows(B.get("by_setup") or {}, money=False)), "",
        "## By composite trend", _table(PERF_HEAD, _perf_rows(B.get("by_trend") or {}, money=False)), "",
        "## By risk regime", _table(PERF_HEAD, _perf_rows(B.get("by_risk") or {}, money=False)), "",
        "## By volatility regime", _table(PERF_HEAD, _perf_rows(B.get("by_volatility") or {}, money=False)), "",
        "## By sector", _table(PERF_HEAD, _perf_rows(B.get("by_sector") or {}, money=False)), "",
        "## Setup × trend (avg R, n)",
        _table(["Setup"] + trends, [[s] + [f"{_r(v.get(t, {}).get('avg_r'))} (n={v.get(t, {}).get('n', 0)})" for t in trends] for s, v in st.items()]),
        "", "Related: [[Setup Forward Test]] (live signals) · [[Regime Playbook]] · [[Pattern Database]] · [[Backtests]]"])


# ------------------------------------------------------------------ regimes
def regime_history_extra(L: Dict) -> str:
    h = regimes.history()
    if h.empty:
        return ""
    cnt = L.get("regime_counts") or {}
    rows = h.tail(40).iloc[::-1]
    return "\n".join([
        "## Composite market-regime database (V1.1)",
        f"{len(h)} trading days classified point-in-time ({h['date'].iloc[0]} → {h['date'].iloc[-1]}). Trend uses SPY, QQQ and IWM "
        "trends, breadth, drawdown and momentum. Risk uses credit spreads, VIX term structure, small vs large caps, dollar, gold "
        "and sector leadership. Volatility uses VIX and realized volatility. No single indicator decides.",
        f"Distribution: trend {cnt.get('trend')} · risk {cnt.get('risk')} · volatility {cnt.get('volatility')}", "",
        _table(["Date", "Trend", "Risk", "Volatility", "SPY/QQQ/IWM", "Breadth", "VIX (term)", "10Y (1m bp)", "Curve", "HY (1m)",
                "DXY 1m", "Oil 1m", "Gold 1m", "Leaders", "Fed"],
               [[r["date"], r["trend"], r["risk"], r["volatility"], f"{r['spy_trend']:+.0f}/{r['qqq_trend']:+.0f}/{r['iwm_trend']:+.0f}",
                 _pp(r["breadth_50d"]), f"{_n(r['vix'], 1)} ({_n(r['vix_term'])})", f"{_n(r['tnx'])} ({r['tnx_1m_bp']:+.0f})",
                 _n(r["curve_10y2y"]), f"{_n(r['hy_spread'])} ({r['hy_1m']:+.2f})", _p(r["dxy_1m"]), _p(r["oil_1m"]), _p(r["gold_1m"]),
                 r["leaders"], r["fed_signal"]] for _, r in rows.iterrows()]),
        "", "Full history: `journal/regime_history.csv`. The scan-time rule-based log is below."])


def regime_playbook_extra(L: Dict) -> str:
    I = L.get("intelligence") or {}
    sc = I.get("similar_conditions") or {}
    B = L.get("backtest") or {}
    P = L.get("performance") or {}
    t = sc.get("today") or {}
    lab = sc.get("backtest_same_regime_label") or {}
    return "\n".join([
        "## How has this type of trade performed in conditions like today's? (V1.1)",
        f"**Today ({t.get('date')}):** {t.get('trend')} · {t.get('risk')} · {t.get('volatility')} · VIX {_n(t.get('vix'), 1)} · breadth "
        f"{_pp(t.get('breadth_50d'))} · 10Y {_n(t.get('tnx'))}% · HY {_n(t.get('hy_spread'))} · leaders {t.get('leaders')} · Fed {t.get('fed_signal')}",
        "**30 most similar past days** (closest conditions, excluding the last 20 sessions): " +
        ", ".join(f"{d['date']} ({d['trend']}/{d['risk']})" for d in (sc.get("similar_days") or [])[:10]) + " …", "",
        "### Backtest outcomes on similar days (BACKTEST)",
        _table(PERF_HEAD, _perf_rows(((sc.get("backtest_on_similar_days") or {}).get("by_setup")) or {}, money=False)), "",
        f"### Backtest outcomes in the same regime label ({lab.get('label')})",
        _table(PERF_HEAD, _perf_rows(lab.get("by_setup") or {}, money=False)), "",
        "### Paper trades by composite regime",
        _table(PERF_HEAD, _perf_rows(P.get("by_composite_trend") or {})), "",
        "### Backtest by composite trend · risk · volatility",
        _table(PERF_HEAD, _perf_rows({**{f"trend: {k}": v for k, v in (B.get("by_trend") or {}).items()},
                                       **{f"risk: {k}": v for k, v in (B.get("by_risk") or {}).items()},
                                       **{f"vol: {k}": v for k, v in (B.get("by_volatility") or {}).items()}}, money=False))])


def macro_log_extra() -> str:
    h = regimes.history()
    if h.empty:
        return ""
    rows = h.tail(30).iloc[::-1]
    return "\n".join(["## Macro conditions (composite regime database, V1.1)",
                      _table(["Date", "Fed funds", "2Y", "10Y", "Curve 10-2", "2Y−FF (Fed proxy)", "HY spread", "DXY 1m", "Oil 1m",
                              "Gold 1m", "Regime"],
                             [[r["date"], _n(r["fed_funds"]), _n(r["two_year"]), _n(r["tnx"]), _n(r["curve_10y2y"]),
                               f"{_n(r['fed_proxy_2y_minus_ff'])} ({r['fed_signal']})", _n(r["hy_spread"]), _p(r["dxy_1m"]),
                               _p(r["oil_1m"]), _p(r["gold_1m"]), f"{r['trend']} / {r['risk']}"] for _, r in rows.iterrows()]),
                      "Sources: FRED (fed funds, 2Y, curve, HY OAS; lagged one day) and Yahoo Finance (10Y, DXY, oil, gold)."])


# ------------------------------------------------------------------ events (one note per event)
def event_body(e: Dict, book: Dict) -> str:
    rx = e.get("reaction") or {}
    ma = rx.get("multi_asset") or {}
    trades = [t for t in book["trades"] if e["id"] in (t.get("event_ids") or [])]
    preds = [p for p in memory.predictions() if p.get("event_id") == e["id"]]
    rejs = [r for r in memory.rejections() if r.get("event_id") == e["id"]]
    uni = universe().set_index("ticker")
    etf_names = {v: k for k, v in config()["sector_etfs"].items()}
    sectors = [f"[[{sector_note(etf_names.get(s, s))}]]" for s in e.get("sectors", []) if s in etf_names]

    def cell(v, unit):
        if v is None:
            return "pending"
        return f"{v:+.0f} bp" if unit == "bp" else (f"{v:+.2f} pts" if unit == "points" else _p(v))
    oh = rx.get("one_hour") or {}
    return "\n".join([
        f"**Date:** {e['date']}{(' ' + e['time'] + ' ET') if e.get('time') else ''}{' (after the close)' if e.get('after_close') else ''} · "
        f"**Category:** {e['category']} · **Source:** {e.get('source') or '—'} · reliability {e.get('reliability') or '—'} · recorded {e['recorded']}",
        "", "## FACT", f"**What happened:** {e.get('what_happened') or e['title']}",
        f"**What the market expected:** {e.get('market_expected') or '—'}", f"**What actually happened vs expectations:** {e.get('actual_vs_expected') or '—'}",
        "", "## INTERPRETATION", e.get("interpretation") or "—", f"**Second-order effects:** {e.get('second_order') or '—'}",
        "", "## PREDICTION (recorded before the reaction)", f"**Expected impact:** {e['expected_impact']}",
        f"**Expected S&P direction:** {e.get('expected_spy') or 'not stated'}",
        "", "## Market reaction (measured)",
        f"**1 hour:** {', '.join(f'{k} {_p(v)}' for k, v in oh.items() if k != 'note') or oh.get('note', 'n/a (no event time recorded or intraday data unavailable)')}",
        _table(["Asset", "1 day", "1 week", "1 month"], [[k, cell(v.get("1d"), v.get("unit")), cell(v.get("1w"), v.get("unit")),
                                                          cell(v.get("1m"), v.get("unit"))] for k, v in ma.items()]),
        f"**Was the reaction expected / over- or under-reaction?** {rx.get('verdict', 'pending')}"
        + (f" · S&P expectation correct: {rx.get('spy_expectation_correct')}" if rx.get("spy_expectation_correct") is not None else ""),
        f"**Options reaction:** {e.get('options_impact') or '—'} · **Volatility:** see VIX row",
        "", "## Links", f"Companies: {', '.join(f'[[{t}]]' for t in e.get('tickers', [])) or '—'} · Sectors: {', '.join(sectors) or '—'}",
        f"Trades: {', '.join(f'[[{_trade_name(t)}]]' for t in trades) or '—'} · Predictions: {', '.join('P' + str(p['id']) for p in preds) or '—'}"
        f" ([[Prediction Accuracy]]) · Rejected: {', '.join('R' + str(r['id']) for r in rejs) or '—'} ([[Rejected Trades]])",
        f"Regime that day: {e.get('regime_trend')} / {e.get('regime_vol')} · Daily note: [[{_di(e['recorded'][:10])}]] · Database: [[News Reaction Database]]",
        "", f"**Lesson:** {e.get('lesson') or '_pending until the 1-week/1-month reaction is measured_'}", f"**Notes:** {e.get('notes') or '—'}"])


# ------------------------------------------------------------------ companies + sectors
def company_extra(t: str, snap: Dict, book: Dict) -> str:
    f, te, op = snap.get("fundamentals") or {}, snap.get("technical") or {}, snap.get("options") or {}
    uni = universe().set_index("ticker")
    sector = uni["sector"].get(t, "")
    ev = [e for e in memory.events() if t in e.get("tickers", [])]
    rejs = [r for r in memory.rejections() if r["ticker"] == t]
    errs = [e for e in memory.errors() if e["subject"].upper().startswith(t)]
    big = lambda x: f"${x / 1e9:,.1f}B" if isinstance(x, (int, float)) else "n/a"
    em = op.get("expected_moves") or []
    return "\n".join([
        f"Sector: [[{sector_note(sector)}]] · ETF {uni['sector_etf'].get(t, '—')}", "",
        f"## Fundamental Picture *(FACT · {snap.get('fundamentals_source', 'not retrieved')})*",
        _table(["Revenue growth", "Earnings growth", "Gross margin", "Operating margin", "Free cash flow", "Cash", "Debt",
                "Debt/equity", "P/E (ttm / fwd)", "PEG", "P/S", "Next earnings", "EPS est.", "Analyst target (n)", "Short % float"],
               [[_p(f.get("revenueGrowth")), _p(f.get("earningsGrowth")), _pp(f.get("grossMargins"), 1), _pp(f.get("operatingMargins"), 1),
                 big(f.get("freeCashflow")), big(f.get("totalCash")), big(f.get("totalDebt")), _n(f.get("debtToEquity"), 0),
                 f"{_n(f.get('trailingPE'), 1)} / {_n(f.get('forwardPE'), 1)}", _n(f.get("pegRatio")), _n(f.get("priceToSalesTrailing12Months"), 1),
                 f.get("next_earnings") or "n/a", _n(f.get("eps_estimate")), f"{_m(f.get('targetMeanPrice'))} ({f.get('numberOfAnalystOpinions') or 'n/a'})",
                 _pp(f.get("shortPercentOfFloat"), 1)]]) if f else "_not retrieved yet_", "",
        f"## Technical Picture *(FACT · daily bars as of {te.get('as_of', 'n/a')})*",
        _table(["Price", "Trend", "vs 20/50/200d", "Support (20d / 55d low)", "Resistance (20d / 52w high)", "RSI 14", "ATR %", "RS 3m vs SPY", "Volume vs 50d"],
               [[_m(te.get("price")), te.get("trend"), f"{_p(te.get('vs_20d'))} / {_p(te.get('vs_50d'))} / {_p(te.get('vs_200d'))}",
                 f"{_m(te.get('support_20d_low'))} / {_m(te.get('support_55d_low'))}", f"{_m(te.get('resistance_20d_high'))} / {_m(te.get('resistance_52w_high'))}",
                 _n(te.get("rsi14"), 0), _pp(te.get("atr_pct"), 1), _p(te.get("rs_3m_vs_spy")), _n(te.get("volume_vs_50d"))]]) if te else "_n/a_", "",
        f"## Options Picture *(FACT · {op.get('source', 'run `desk.py options` to populate')}{(' · ' + op['retrieved']) if op.get('retrieved') else ''})*",
        (f"ATM 30d IV {_pp(op.get('atm_iv_30d'), 1)} · IV rank {_n(op.get('iv_rank'))} · HV20 {_pp(op.get('hv20'), 1)} · quotes {op.get('quote_quality')}\n\n"
         + _table(["Expiry", "DTE", "ATM strike", "ATM IV", "Expected move"], [[x.get("expiry"), x.get("dte"), x.get("atm_strike"), _pp(x.get("atm_iv"), 1),
                                                                                _pp(x.get("expected_move_pct"), 1)] for x in em])
         + "\n\nLargest open interest: " + "; ".join(f"{x['expiry']} {x['type']} {x['strike']} OI {x['open_interest']}" for x in op.get("top_open_interest") or [])
         + f"\n\nLast structure considered: {op.get('best_structure')}") if op else "_not analyzed yet_", "",
        f"## Historical Rejected Trades ({len(rejs)})", _table(["#", "Date", "Dir", "Reason"], [[f"R{r['id']}", r["decided"], r["direction"], r["reason"]] for r in rejs]),
        f"## Historical News ({len(ev)})", ", ".join(f"[[{event_note(e)}]]" for e in ev) or "—",
        f"## Analytical errors involving {t} ({len(errs)})", ", ".join(f"E{e['id']} {e['error_type']}" for e in errs) or "—", ""])


def sector_body(name: str, etf: str, L: Dict, book: Dict, companies: List[str]) -> str:
    h = regimes.history()
    lead = lag = 0
    if len(h):
        last = h.tail(60)
        lead = int(last["leaders"].fillna("").str.split(",").map(lambda x: etf in x).sum())
        lag = int(last["laggards"].fillna("").str.split(",").map(lambda x: etf in x).sum())
    bsec = ((L.get("backtest") or {}).get("by_sector") or {}).get(name) or {}
    ev = [e for e in memory.events() if etf in e.get("sectors", [])]
    trades = [t for t in book["trades"] if universe().set_index("ticker")["sector"].get(t["ticker"]) == name]
    return "\n".join([
        f"**ETF:** {etf} · **Leadership (last 60 sessions):** top-3 on {lead} days, bottom-3 on {lag} days (composite regime database)",
        f"**Backtest signals in this sector:** n={bsec.get('n', 0)} · avg {_r(bsec.get('avg_r'))} · {bsec.get('evidence', '')}", "",
        "**Chain:** event → market regime → **sector** → company → trade → outcome → lesson", "",
        f"## Companies with intelligence files ({len(companies)})", ", ".join(f"[[{c}]]" for c in companies) or "—",
        f"## Events affecting the sector ({len(ev)})", ", ".join(f"[[{event_note(e)}]]" for e in ev) or "—",
        f"## Trades ({len(trades)})", ", ".join(f"[[{_trade_name(t)}]]" for t in trades) or "—",
        "", "Related: [[Regime Playbook]] · [[Screener Backtest]]"])


# ------------------------------------------------------------------ weekly / monthly additions
WEEKLY_ADD = """
## WEEKLY INTELLIGENCE REPORT (V1.1) <!-- weekly-intel -->
_FACT / INTERPRETATION / PREDICTION kept separate. Use the auto data above._
**What changed this week?**
-
**Most important news** (link event notes):
-
**Major macro changes:**
-
**Political/geopolitical changes:**
-
**Sector rotation:**
-
**Best predictions / worst predictions:**
-
**Best trades / worst trades** (vs benchmarks, not just P&L):
-
**Best rejected trades** (and whether the rejection was still right on process):
-
**Biggest analytical mistakes** (link [[Error Database]]):
-
**Emerging patterns** (only with evidence; link [[Pattern Database]]):
-
**Strategy experiments:**
-
**Recommended research:**
-
"""

MONTHLY_ADD = """
## MONTHLY SYSTEM AUDIT (V1.1) <!-- monthly-audit -->
Evaluate: prediction accuracy · trade performance · options performance · risk management · market-regime accuracy ·
news interpretation · sector selection · stock selection · trade ranking (opportunity cost) · position sizing · timing ·
confidence calibration.
**WHAT IS WORKING?**
**WHAT IS NOT?**
**WHAT HAS ENOUGH EVIDENCE TO CHANGE?** (only items meeting the Constitution's standard)
**WHAT NEEDS MORE DATA?**
**WHAT SHOULD NOT BE CHANGED?**
"""


def period_intel(L: Dict, book: Dict, start: str, end: str) -> str:
    ps = [p for p in memory.predictions() if p.get("scored_on") and start <= p["scored_on"] <= end]
    good = sorted([p for p in ps if p.get("correct")], key=lambda p: -float(p.get("confidence_pct") or 0))[:3]
    bad = sorted([p for p in ps if not p.get("correct")], key=lambda p: -float(p.get("confidence_pct") or 0))[:3]
    cl = sorted([t for t in book["trades"] if t["status"] == "closed" and start <= t["closed"][:10] <= end], key=lambda t: t["r_multiple"])
    rj = [r for r in (L.get("intelligence") or {}).get("rejection_records") or [] if start <= r["decided"][:10] <= end]
    rj = sorted([r for r in rj if r.get("hypothetical_r") is not None], key=lambda r: -r["hypothetical_r"])
    er = [e for e in memory.errors() if start <= e["date"] <= end]
    ev = [e for e in memory.events() if start <= e["date"] <= end]
    h = regimes.history()
    wk = h[(h["date"] >= start) & (h["date"] <= end)] if len(h) else h
    lead = pd.Series(",".join(wk["leaders"].fillna("")).split(",")).value_counts().head(3).to_dict() if len(wk) else {}
    lag = pd.Series(",".join(wk["laggards"].fillna("")).split(",")).value_counts().head(3).to_dict() if len(wk) else {}
    pats = [p for p in (L.get("intelligence") or {}).get("patterns") or [] if p["source"] != "backtest"]
    xs = (L.get("intelligence") or {}).get("experiments") or []
    return "\n".join([
        "### Intelligence data (auto)",
        f"- Regime days: {wk['trend'].value_counts().to_dict() if len(wk) else 'n/a'} · risk {wk['risk'].value_counts().to_dict() if len(wk) else ''}",
        f"- Sector leaders (days in top 3): {lead} · laggards: {lag}",
        f"- Events: " + (", ".join(f"[[{event_note(e)}]] ({e['category']})" for e in ev) or "none"),
        f"- Best predictions: " + ("; ".join(f"P{p['id']} {p['statement']} ({p['confidence_pct']:.0f}%)" for p in good) or "none scored"),
        f"- Worst predictions: " + ("; ".join(f"P{p['id']} {p['statement']} ({p['confidence_pct']:.0f}%)" for p in bad) or "none scored"),
        f"- Best trades: " + ("; ".join(f"[[{_trade_name(t)}]] {_r(t['r_multiple'])}" for t in cl[::-1][:3]) or "none closed"),
        f"- Worst trades: " + ("; ".join(f"[[{_trade_name(t)}]] {_r(t['r_multiple'])}" for t in cl[:3]) or "none closed"),
        f"- Best rejected trades (hypothetical): " + ("; ".join(f"R{r['id']} {r['ticker']} {_r(r['hypothetical_r'])} ({r['reason']})" for r in rj[:3]) or "none resolved"),
        f"- Analytical mistakes: " + ("; ".join(f"E{e['id']} {e['error_type']} ({e['severity']})" for e in er) or "none recorded"),
        f"- Patterns (forward/paper evidence): " + ("; ".join(f"{p['pattern']} {p['status'].split(' (')[0]} n={p['n']}" for p in pats[:3]) or "none qualify yet"),
        f"- Experiments: " + ("; ".join(f"X{x['id']} {x['name']}: {x['suggestion']}" for x in xs) or "none")])


# ------------------------------------------------------------------ main
def sync(root: Path, book: Dict, L: Dict, mark) -> None:
    I = L.get("intelligence") or {}
    static(root, mark)
    T = root / "02 - Trades"
    mark(_upsert(T / "Rejected Trades.md", rejections_note(L), head="---\ntype: database\ntags: [trading/rejected-trades]\n---\n# Rejected Trades",
                 tail="## Lessons from trades not taken (dated, with evidence level)\n- "), T / "Rejected Trades.md")
    R = root / "09 - Strategy Research"
    mark(_upsert(R / "Error Database.md", errors_note(L, book), head="---\ntype: database\ntags: [trading/errors]\n---\n# Error Database"), R / "Error Database.md")
    mark(_upsert(R / "Pattern Database.md", patterns_note(L), head="---\ntype: database\ntags: [trading/patterns]\n---\n# Pattern Database",
                 tail="## Pattern notes (failure conditions observed, context)\n- "), R / "Pattern Database.md")
    mark(_upsert(R / "Screener Backtest.md", backtest_note(L), head="---\ntype: analytics\ntags: [trading/backtest]\n---\n# Screener Backtest"),
         R / "Screener Backtest.md")
    for x in I.get("experiments") or []:
        p = R / "Experiments" / f"X{x['id']} {re.sub(r'[^A-Za-z0-9 &()+-]', '', x['name'])[:60]}.md"
        mark(_upsert(p, experiment_note(x), front=_front({"type": "experiment", "status": x["status"], "dataset": x["dataset"],
                                                          "tags": ["research/experiment"]}), head=f"# Experiment X{x['id']}: {x['name']}",
                     tail="## Notes\n- "), p)
    Pf = root / "10 - Performance"
    mark(_upsert(Pf / "Decision Scorecards.md", scorecards_note(L), head="---\ntype: database\ntags: [trading/scorecards]\n---\n# Decision Scorecards"),
         Pf / "Decision Scorecards.md")
    # extend existing analytics notes with a second auto block (kept separate from the original block)
    for rel, body in (("10 - Performance/Prediction Accuracy.md", predictions_block(L)),
                      ("08 - Market Regimes/Regime History.md", regime_history_extra(L)),
                      ("08 - Market Regimes/Regime Playbook.md", regime_playbook_extra(L)),
                      ("06 - Macro/Macro Log.md", macro_log_extra())):
        mark(_upsert_named(root / rel, "intel", body), root / rel)
    # events: one note each, linked from the database
    for e in memory.events():
        p = root / event_folder(e) / f"{event_note(e)}.md"
        mark(_upsert(p, event_body(e, book), front=_front({"type": "event", "date": e["date"], "category": e["category"],
                                                          "tickers": e.get("tickers", []), "tags": ["event", f"event/{_slug(e['category'])}"]}),
                     head=f"# {e['title']}", tail="## Claude notes (dated)\n- "), p)
    # companies: intelligence sections for priority companies only
    uni = universe().set_index("ticker")
    snaps = I.get("company_snapshots") or {}
    sectors: Dict[str, List[str]] = {}
    for t in I.get("priority_companies") or []:
        p = root / "04 - Companies" / f"{t}.md"
        if not p.exists():
            continue                           # created by the main sync (priority list) first
        mark(_insert_before_auto(p, "<!-- company:intel -->", COMPANY_SKELETON), p)
        mark(_upsert_named(p, "intel", company_extra(t, snaps.get(t, {}), book)), p)
        sectors.setdefault(uni["sector"].get(t, "Other"), []).append(t)
    etf_of = {k: v for k, v in config()["sector_etfs"].items()}
    for e in memory.events():
        for s in e.get("sectors", []):
            name = {v: k for k, v in etf_of.items()}.get(s)
            if name:
                sectors.setdefault(name, [])
    for name, comps in sectors.items():
        etf = etf_of.get(name) or (uni["sector_etf"].get(comps[0]) if comps else "")
        p = root / "04 - Companies" / "Sectors" / f"{sector_note(name)}.md"
        mark(_upsert(p, sector_body(name, etf, L, book, comps), front=_front({"type": "sector", "etf": etf, "tags": ["sector", f"sector/{_slug(name)}"]}),
                     head=f"# {name}", tail="## Sector notes (dated)\n- "), p)
    # weekly/monthly: append the new manual sections once and an intelligence auto block
    today = str(now_ny().date())
    from datetime import date, timedelta

    d0 = date.fromisoformat(today)
    ws, we = str(d0 - timedelta(days=d0.weekday())), str(d0 + timedelta(days=6 - d0.weekday()))
    for f in sorted((Pf / "Weekly Reviews").glob("*.md")):
        mark(_append_once(f, "<!-- weekly-intel -->", WEEKLY_ADD), f)
    for f in sorted((Pf / "Monthly Reviews").glob("*.md")):
        mark(_append_once(f, "<!-- monthly-audit -->", MONTHLY_ADD), f)
    wk = Pf / "Weekly Reviews" / f"{d0.isocalendar()[0]}-W{d0.isocalendar()[1]:02d} Weekly Review.md"
    mark(_upsert_named(wk, "intel", period_intel(L, book, ws, we)), wk)
    mo = Pf / "Monthly Reviews" / f"{today[:7]} Monthly Review.md"
    mark(_upsert_named(mo, "intel", period_intel(L, book, today[:7] + "-01", today[:7] + "-31")), mo)


def _upsert_named(path: Path, name: str, body: str) -> bool:
    """A second, named auto block (desk:<name>:start/end) so extensions never touch the original block or manual text."""
    if not path.exists():
        return False
    a, b = f"<!-- desk:{name}:start -->", f"<!-- desk:{name}:end -->"
    block = f"{a}\n> 🤖 Auto-generated (V1.1 intelligence layer). Edit outside this block only.\n\n{body.strip()}\n{b}"
    s = path.read_text(encoding="utf-8")
    if a in s and b in s:
        new = s[:s.index(a)] + block + s[s.index(b) + len(b):]
    else:
        from desk.obsidian import END

        new = s[:s.index(END) + len(END)] + "\n\n" + block + s[s.index(END) + len(END):] if END in s else s.rstrip() + "\n\n" + block + "\n"
    if new != s:
        path.write_text(new, encoding="utf-8")
        return True
    return False
