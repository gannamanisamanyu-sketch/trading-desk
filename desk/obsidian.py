"""Mirror the desk's memory into Samanyu's Obsidian vault (`brain 2/Trading/`).

The vault is the trading system's long-term memory. This module writes the FACTUAL layer (ledger, outcomes,
statistics, scans, headlines, versions) inside AUTO blocks. Everything outside those blocks (reviews,
self-critiques, lessons, hypotheses, experiments) is written by Claude/Samanyu and is never overwritten.

Layout (numbers match the folder names):
  Trading Dashboard.md
  00 - System/            Trading Constitution, Risk Rules, Strategy, Current System Version, System Changelog,
                          Daily Workflow, Templates/
  01 - Daily Intelligence/<day> Daily Intelligence.md (+ Raw/ reports and scans)
  02 - Trades/Active|Completed/Trade NNN TICKER.md, Trade Templates/
  03 - Portfolio/         Portfolio, Watchlist
  04 - Companies/<TICKER>.md
  05 - Options/           Options Learning, Options Playbook
  06 - Macro/             News Reaction Database, Macro Events, Macro Log
  07 - Politics & Geopolitics/Political & Geopolitical Events
  08 - Market Regimes/    Regime History, Regime Playbook
  09 - Strategy Research/ Improvement Pipeline, Setup Forward Test, Backtests, Experiments/
  10 - Performance/       Performance Dashboard, Prediction Accuracy, Weekly Reviews/, Monthly Reviews/
  11 - System Versions/   V1.0 — Initial System, ...
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from desk import memory
from desk.common import DATA, REPORTS, config, now_ny

START, END = "<!-- desk:auto:start -->", "<!-- desk:auto:end -->"
NOTICE = "> 🤖 Auto-generated from the ledger, journal and market data (`desk.py obsidian`). Edit outside this block only."
REVIEW_PENDING, CRITIQUE_PENDING, WEEKLY_PENDING, MONTHLY_PENDING = (
    "<!-- review:pending -->", "<!-- critique:pending -->", "<!-- weekly:pending -->", "<!-- monthly:pending -->")


def vault_dir() -> Optional[Path]:
    raw = os.environ.get("TRADING_DESK_VAULT") or (config().get("obsidian") or {}).get("folder")
    if not raw:
        return None
    p = Path(os.path.expanduser(raw))
    return p if p.parent.exists() else None


# ------------------------------------------------------------------ formatting helpers
def _m(x) -> str:
    try:
        return f"${x:,.2f}" if x is not None and x == x else "n/a"
    except (TypeError, ValueError):
        return "n/a"


def _p(x, d=1) -> str:
    return f"{x:+.{d}%}" if isinstance(x, (int, float)) and x == x else "n/a"


def _pp(x, d=0) -> str:
    return f"{x:.{d}%}" if isinstance(x, (int, float)) and x == x else "n/a"


def _r(x) -> str:
    return f"{x:+.2f}R" if isinstance(x, (int, float)) and x == x else "n/a"


def _n(x, d=2) -> str:
    return f"{x:.{d}f}" if isinstance(x, (int, float)) and x == x else "n/a"


def _slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _front(d: Dict) -> str:
    def v(x):
        if isinstance(x, list):
            return "[" + ", ".join(str(i) for i in x) + "]"
        return json.dumps(x) if isinstance(x, str) else ("" if x is None else x)
    return "---\n" + "\n".join(f"{k}: {v(x)}" for k, x in d.items()) + "\n---"


def _upsert(path: Path, auto: str, head: str = "", tail: str = "", front: Optional[str] = None) -> bool:
    """Replace the AUTO block (create the file from head/tail if new). `front` refreshes YAML frontmatter."""
    block = f"{START}\n{NOTICE}\n\n{auto.strip()}\n{END}"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old is not None:
        if START in old and END in old:
            new = old[:old.index(START)] + block + old[old.index(END) + len(END):]
        else:
            new = old.rstrip() + "\n\n" + block + "\n"
        if front and new.startswith("---"):
            new = front + new[new.index("---", 3) + 3:]
    else:
        new = "\n\n".join(x for x in ((front or "").strip(), head.strip(), block, tail.strip()) if x) + "\n"
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
        return True
    return False


def _once(path: Path, text: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return True


def _table(headers: List[str], rows: List[List]) -> str:
    if not rows:
        return "_none yet_"
    clean = lambda x: str(x).replace("|", "/").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)] +
                     ["| " + " | ".join(clean(x) for x in r) + " |" for r in rows])


def _perf_rows(d: Dict[str, Dict], money: bool = True) -> List[List]:
    rows = []
    for k, v in sorted(d.items(), key=lambda kv: -kv[1].get("n", 0)):
        ci = v.get("ci95_mean_r") or [None, None]
        rows.append([k, v.get("n", 0), _pp(v.get("win_rate")), _r(v.get("avg_r")),
                     (f"[{_n(ci[0])}, {_n(ci[1])}]" if ci[0] is not None else "n/a"),
                     _m(v.get("avg_win")) if money else _r(v.get("avg_win_r")),
                     _m(v.get("avg_loss")) if money else _r(v.get("avg_loss_r")),
                     _n(v.get("profit_factor_usd") if money else v.get("profit_factor")),
                     _m(v.get("expected_value")) if money else _r(v.get("avg_r")),
                     _m(v.get("max_drawdown")) if money else "n/a", _n(v.get("avg_holding_days"), 1),
                     _p(v.get("return_on_start")) if money else "n/a", _n(v.get("risk_adjusted")), v.get("evidence", "")])
    return rows


PERF_HEAD = ["Group", "n", "Win %", "Avg R", "95% CI mean R", "Avg win", "Avg loss", "Profit factor", "Expected value",
             "Max drawdown", "Avg hold (d)", "Return", "Risk-adj (R/σ)", "Evidence"]


def _trade_name(t: Dict) -> str:
    return f"Trade {t['id']:03d} {t['ticker']}"


def _di(day: str) -> str:
    return f"{day} Daily Intelligence"


def _section(md: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}.*?$\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def _week(day: str) -> str:
    y, w, _ = pd.Timestamp(day).isocalendar()
    return f"{y}-W{w:02d}"


# ------------------------------------------------------------------ static system notes (written once)
CONSTITUTION = """
---
type: system
tags: [trading/system, constitution]
---
# Trading Constitution

The non-negotiable rules of this paper-trading research system. Claude reads this **first** in every desk
session. Changing anything here requires Samanyu's explicit approval and a new [[System Changelog|system version]].

## 1. What this system is
- A **research and PAPER-trading** desk for a $30,000 simulated account (U.S. stocks and options).
- No brokerage connection and no real-money trades, ever. No assumed extra capital.
- Claude is not a financial adviser. Ideas are educational and model-generated, and Samanyu makes every decision.
- **Obsidian = long-term memory · Claude = reasoning · market/news APIs = current data · paper trading =
  experiments · trade journal = feedback · performance analytics = evaluation · versions = controlled improvement.**

## 2. Safety rules (never broken)
1. Never silently change [[Risk Rules|risk limits]], never silently change the strategy, never silently increase risk.
2. Never fabricate market data, news, historical performance or outcomes. Unknown = "unknown"; open trades are never
   described as winners or losers.
3. Every number in a report comes from a tool output or a cited, timestamped source; stale data is labeled STALE.
4. Every meaningful system change is documented in [[System Changelog]] and a note in `11 - System Versions/`.
   `desk.py version check` compares live parameters with the documented version and flags any undocumented change.

## 3. Process over outcome
- **A losing trade is not a bad trade, and a winning trade is not a good trade.** Grade every decision on its
  process: was the thesis sound and sourced, the entry disciplined, the size within limits, the structure right,
  contradictory evidence weighed? Then, separately, the outcome.
- Every post-trade review classifies the trade: **Good process / good outcome · Good process / bad outcome (accept) ·
  Bad process / good outcome (luck; fix the process) · Bad process / bad outcome (fix the process).**

## 4. Evidence standards (anti-overfitting)
Nothing changes because of one trade, three trades, one stock or one unusual news event.

| Evidence level | Requirement | What it allows |
|---|---|---|
| INSUFFICIENT | n < 20 outcomes | Record OBSERVATIONS only. No conclusions. |
| PRELIMINARY | 20 ≤ n < 50 | Form HYPOTHESES and design EXPERIMENTS. No rule changes. |
| ESTABLISHED | n ≥ 50 **and** 95% CI of mean R excludes 0 **and** robustness checks | A VALIDATED CHANGE may be proposed. |

**Robustness checks** (computed automatically in [[Performance Dashboard]]): ≥ 10 different tickers · ≥ 2 market
regimes · ≥ 2 calendar months · still positive with the single best trade removed · no ticker > 20% of the sample.

**Validated change** (strategy edit): an experiment defined *before* the data was collected, with a control and a
variant, each arm ≥ 30 trades or ≥ 100 forward-tested signals, a difference whose 95% CI excludes 0, holding in
≥ 2 regimes. Then: Samanyu approves → `desk.py version new ...` → new note in `11 - System Versions/`.
Prefer simple rules that work across securities, regimes, periods and catalysts over rules that explain the past perfectly.

## 5. Improvement pipeline
Every idea moves through [[Improvement Pipeline]] in order, never skipping a stage:
**OBSERVATION → HYPOTHESIS → EXPERIMENT → VALIDATED CHANGE or REJECTED CHANGE.**
Rejected changes stay recorded (with the evidence) so they are not re-tried by accident.

## 6. Risk-limit changes
Risk limits may only be **loosened** by Samanyu, explicitly, in chat. Claude may propose tightening, but still
documents it as a new version. Risk is never increased to "make up" for losses.

## 7. Memory loop
LEARN → ANALYZE → PREDICT → TRADE IDEA → RECORD → OBSERVE OUTCOME → COMPARE PREDICTION vs REALITY → LEARN → IMPROVE → REPEAT.
See [[Daily Workflow]] for exactly what is read and written each day.

## Tags
`#trade` `#trade/active` `#trade/completed` · `#strategy/<name>` · `#regime/<bull|bear|sideways>` ·
`#vol/<low|normal|high>-volatility` · `#risk/<risk-on|risk-off|mixed>` · `#catalyst/<category>` · `#prediction` ·
`#event/<category>` · `#review/weekly` `#review/monthly` · `#system/version` · `#research/observation|hypothesis|experiment`
"""

WORKFLOW = """
---
type: system
tags: [trading/system, workflow]
---
# Daily Workflow

How Claude uses this vault every trading day. Commands run in `~/code/trading-desk`.

| Step | What Claude does | Reads | Writes |
|---|---|---|---|
| 1 | Read the current system | [[Trading Constitution]], [[Risk Rules]], [[Strategy]], [[Current System Version]] | |
| 2 | Read recent intelligence | last 3-5 notes in `01 - Daily Intelligence/` (decisions, self-critiques) | |
| 3 | Read recent trades | [[Portfolio]], `02 - Trades/Active`, the latest `Completed` reviews | |
| 4 | Read similar setups | `desk.py context` (strategy evidence, forward test), [[Setup Forward Test]], `04 - Companies/<ticker>` via `desk.py recall TICKER` | |
| 5 | Read regime history | [[Regime Playbook]], [[Regime History]] | |
| 6 | Current news + data | `desk.py scan`, web search (cited, timestamped), [[News Reaction Database]] for similar past events | scan → `Raw/`, signals + regime log |
| 7 | Generate opportunities | screener, news mismatches, insider buys, sector leaders, [[Watchlist]] | |
| 8 | Rank | evidence from steps 1-5 adjusts **confidence**, never the hard rules | |
| 9 | Plan | report from `reports/TEMPLATE.md`; options only if they beat the stock | report |
| 10 | Store | `desk.py predict add` for each meaningful call; `desk.py event add` for each material event; paper trades with `--strategy --catalyst-category --horizon-days` | predictions, events, trades |
| 11 | End of day: compare | `desk.py paper update` then `desk.py learn` (scores predictions, events, signals; MFE/MAE) | statistics |
| 12 | Journal | post-trade review on every completed trade (template in `02 - Trades/Trade Templates`) | `## Review` in the trade note |
| 13 | Lessons | self-critique in today's Daily Intelligence note; observations into [[Improvement Pipeline]] | daily note, pipeline |
| 14 | Proposals | only with evidence that meets the [[Trading Constitution#4. Evidence standards (anti-overfitting)|standards]] | pipeline; versions only after approval |

**Weekly (Friday or the next session):** fill in the week's note in `10 - Performance/Weekly Reviews/`.
**Monthly (first session of a month):** fill in `10 - Performance/Monthly Reviews/` and compare system versions.
**Open action items** (pending reviews, critiques, predictions due, version warnings) are listed on the [[Trading Dashboard]].

Automatic syncing: after `scan`, `learn`, `paper`, `predict`, `event` and `version` commands, when the Trading Desk
app opens, and weekdays at 7:00 and 14:30 (Arizona) via launchd (`scripts/obsidian_sync.sh`: pull, learn, sync).
"""

STRATEGY = """
---
type: system
tags: [trading/system, strategy]
---
# Strategy

The current strategy as run by the code. The parameters below are auto-filled from `config/desk.yaml`. A change
to anything here must go through the [[Improvement Pipeline]] and a new version ([[System Changelog]]).

## Strategy vocabulary (every trade and prediction is tagged with one)
Momentum · Breakout · Pullback · Mean reversion · Earnings · Macro · News-driven · Political · Geopolitical ·
Sector rotation · Catalyst · Options directional · Options spread · Volatility

Screener setup → strategy: breakout/breakdown → Breakout · pullback → Pullback · rs_leader → Momentum ·
oversold_uptrend → Mean reversion · laggard_in_strong_sector → Sector rotation · news_mismatch → News-driven.
Options trades are additionally tagged Options directional (long call/put) or Options spread (verticals).

## Decision principles
- Rank only setups with ≥ 2:1 reward/risk and a real catalyst or clear technical edge; social buzz is never enough alone.
- Cash is a position; there is no daily trade quota. FINAL DECISION scale: AGGRESSIVE BUY · MODERATE BUY ·
  SELECTIVE TRADES ONLY · HOLD CASH · RISK-OFF.
- Historical evidence from this vault adjusts **confidence and ranking** only once it is at least PRELIMINARY;
  it never overrides the hard risk rules.
"""

OPTIONS_PLAYBOOK = """
---
type: system
tags: [trading/options]
---
# Options Playbook

Current options rules (V1.0) and what we are trying to learn. Evidence lives in [[Options Learning]].

## Rules now
- Use an option only if it beats the stock on reward/risk **and** fits the 1% max-loss rule (full premium = risk).
- Structures: long call/put (|delta| 0.25-0.8) or vertical debit spreads (short strike between long strike and target).
- Liquidity: open interest ≥ 100, bid/ask spread ≤ 15% of mid, 14-120 DTE (expiry ≥ horizon + 10 days).
- Pre-market quotes are STALE; re-price after the open before recording.

## Open questions to answer with evidence (not opinions)
- Which DTE ranges and deltas perform best?
- Do options beat the stock for the same thesis (counterfactual R in [[Options Learning]])?
- Are we overpaying for implied volatility (entry IV vs realized vol over the hold)?
- Is theta eating the edge? Are strikes too far OTM?
"""

PIPELINE = """
---
type: research
tags: [trading/research, improvement-pipeline]
---
# Improvement Pipeline

Controlled self-improvement. Every idea starts as an OBSERVATION and moves one stage at a time, with dates and
evidence. Thresholds: [[Trading Constitution#4. Evidence standards (anti-overfitting)|evidence standards]]. Nothing
here changes the live system until it is a VALIDATED CHANGE approved by Samanyu and recorded as a new version.

## 🔎 Observations (single data points; no conclusions)
- 2026-10-07 · Pre-market option quotes were stale (zero bid/ask/OI), so "unusual activity" before the open was unreliable. #research/observation
- 2026-10-07 · The NVDA trade meant "at the open" was logged at 15:07 instead; logged honestly at the actual price. Process note: decide and log at the price available at decision time. #research/observation
- 2026-10-07 · FOMC minutes were a non-event for SPY / 10Y / VIX intraday (n=1). #research/observation
- 2026-10-07 · The rule-based scan regime (BULL at 15:16) and the analyst regime (NEUTRAL) disagreed; reports must say which is which. #research/observation
- 2026-10-07 · Day-1 reaction: SpaceX→NVDA "news not priced in" call: NVDA −1.0% on the reaction day (n=1, 1 day). Houthi→refiners call: MPC +2.1%, VLO +1.0%, airlines down. Too early to judge either. #research/observation

## 🧪 Hypotheses (testable statements; need PRELIMINARY evidence to form)
_None yet. Needs ≥ 20 outcomes in a group before forming one._

## 🔬 Experiments (designed before data is collected; one note each in `Experiments/`)
_None running._

## ✅ Validated changes
_None. See [[System Changelog]]._

## ❌ Rejected changes (kept so they are not retried by accident)
_None._
"""

BACKTESTS = """
---
type: research
tags: [trading/research, backtest]
---
# Backtests

Historical tests that inform this desk. **This desk itself has no backtest yet**: its evidence is the forward
paper record ([[Performance Dashboard]]) and the forward test of every screener signal ([[Setup Forward Test]]).

## Related systems (separate rules, separate results; don't transfer conclusions blindly)
- **Algorithmic U.S. Equity Paper Trading System**: `~/code/paper_trading_bot` (`RESEARCH_LOG.md` documents every
  backtest change and result). Project note: [[Algorithmic Paper Trading System]].
- **Portfolio Risk Lab**: `~/code/portfolio-risk-lab` (VaR, stress tests, optimization). Project note: [[Portfolio Risk Lab]].

## Rules for adding a backtest here
Point-in-time data only (no look-ahead), costs and slippage included, out-of-sample period stated, sample size and
regimes covered stated, and a link to the code and commit that produced it.
"""

WATCHLIST = """
---
type: portfolio
tags: [trading/watchlist]
---
# Watchlist

Names being watched, each with the trigger that would make it actionable and the date added. Remove stale names
(say why). Company history: `04 - Companies/`.

| Added | Ticker | Idea | Trigger to act | Invalidation | Status |
|---|---|---|---|---|---|
| 2026-10-07 | [[CRM]] | Pullback in a strong software sector | Reclaims its 20-day average (~238) | New low below the pullback | WAIT |
| 2026-10-07 | [[MRVL]] | AI custom-silicon leader; S&P 500 inclusion | A fresh setup (the 287 breakout failed on Oct 7 and does not carry over) | — | WAIT |
"""

TEMPLATES = {
    "02 - Trades/Trade Templates/Trade Plan Template.md": """
# Trade plan: {{TICKER}}
- **Prediction:** {{TICKER}} will ___ (direction) by ___% (magnitude) within ___ trading days (timeframe)
- **Strategy:** (vocabulary in [[Strategy]]) · **Catalyst category:** · **Regime at entry:**
- **Thesis:**
- **Catalyst** (source, time):
- **Entry / stop / target:** · **Position size** (from `desk.py size`): · **Max loss:**
- **Instrument choice** (stock vs option, and why):
- **What would prove me wrong:**
- **Contradictory evidence I considered:**
- Recorded with: `desk.py paper add-stock ... --strategy ... --catalyst-category ... --horizon-days N`
  and `desk.py predict add ...`
""",
    "02 - Trades/Trade Templates/Post-Trade Review Template.md": """
## Review (post-trade analysis)
**Prediction vs outcome** (facts are in the auto block above):
- Expected direction / magnitude / timeframe →
- Actual price movement, and how the catalyst actually played out →
- Max favorable / adverse excursion (MFE / MAE) →

**Grades** (yes / no / partly + one line of evidence each)
- Thesis correct?
- Timing correct? (early / late / right)
- Position sizing correct?
- Instrument / options structure correct?

**Process vs outcome:** Good process + good outcome · Good process + bad outcome · Bad process + good outcome · Bad process + bad outcome
**What I got right:**
**What I got wrong:**
**What should change** (as an OBSERVATION for the [[Improvement Pipeline]], not a rule):
**What should NOT change:**
""",
    "02 - Trades/Trade Templates/Options Post-Trade Review Template.md": """
## Options review
- Underlying / call-put / strike(s) / expiry / DTE at entry →
- Entry vs exit premium · delta · IV and IV rank at entry · theta/day · volume / OI →
- Expected move vs actual move of the underlying →
- Would the stock have done better for the same thesis? (see the counterfactual R in the auto block)
- Did we overpay for IV (entry IV vs realized vol)? Did theta decide the trade? Was the strike too far OTM?
- Lesson (as an OBSERVATION):
""",
    "00 - System/Templates/Daily Self-Critique Template.md": "{critique}",
    "00 - System/Templates/Weekly Review Template.md": "{weekly}",
    "00 - System/Templates/Monthly Review Template.md": "{monthly}",
    "00 - System/Templates/Prediction Template.md": """
# Prediction (record with `desk.py predict add`, which anchors the live price)
Date: · Ticker: · Prediction: · Expected direction: · Expected timeframe (trading days): · Confidence (low/medium/high):
Reason: · Catalyst: · Benchmark (for relative calls): · Expected magnitude:
Later (auto-filled by `desk.py learn`): actual result · actual return · benchmark return · correct/incorrect
Lesson (written by Claude after scoring):
""",
    "00 - System/Templates/Event Template.md": """
# Event (record with `desk.py event add`; the market impact is measured automatically)
EVENT: · DATE: · CATEGORY: · EXPECTED IMPACT: · EXPECTED SPY (UP/DOWN/FLAT, optional):
STOCKS / SECTORS AFFECTED:
Auto: ACTUAL MARKET IMPACT (1-day and 5-day returns)
Later: OPTIONS IMPACT · LESSON (`desk.py event update --id N --lesson "..." --options-impact "..."`)
""",
    "00 - System/Templates/Experiment Template.md": """
---
type: experiment
status: proposed   # proposed | running | validated | rejected
tags: [research/experiment]
---
# Experiment: {{name}}
- **Observation that led here:** (link to the pipeline entry)
- **Hypothesis:** (one sentence, testable)
- **Control vs variant:** (exactly what differs)
- **Success metric and threshold:** (e.g. mean R difference, 95% CI excludes 0)
- **Minimum sample:** ≥ 30 trades per arm, or ≥ 100 forward-tested signals, across ≥ 2 regimes
- **Start date:** · **How data is collected:** (paper trades tagged `experiment:<name>` / forward test)
- **Result:** · **Decision:** VALIDATED / REJECTED · **Version created:**
""",
    "00 - System/Templates/System Version Template.md": """
# V{{x.y}} — {{name}}
WHAT CHANGED: · WHY: · EVIDENCE: · EXPECTED EFFECT: · APPROVED BY:
ACTUAL EFFECT: (auto-filled by performance-by-version once enough trades exist)
Create with `desk.py version new --version x.y --name ... --changes ... --reason ... --evidence ... --expected ... --approved-by Samanyu`
""",
    "00 - System/Templates/Company Template.md": """
# {{TICKER}}
Company notes are created automatically in `04 - Companies/` when a ticker appears in a trade, prediction, event or
screener signal. Add business notes, thesis history and lessons below the auto block.
""",
}

CRITIQUE = f"""
## Self-critique (end of day) {CRITIQUE_PENDING}
_Claude: answer each in one or two lines, with evidence. Remove the pending marker when done._
1. What did I predict?
2. What actually happened?
3. Which predictions were wrong?
4. Why were they wrong?
5. Did I misread the news?
6. Did I misread the market regime?
7. Did I misjudge the catalyst?
8. Did I enter too early?
9. Did I enter too late?
10. Was the option structure wrong?
11. Was position sizing wrong?
12. Did I ignore contradictory evidence?
13. Did I overreact to news?
14. Did I underreact to news?
15. Was any trade actually **bad**, or did a **good trade simply lose**? (process vs outcome)

**Observations to add to the [[Improvement Pipeline]]:**
"""

WEEKLY = f"""
## WEEKLY TRADING SYSTEM REVIEW {WEEKLY_PENDING}
_Claude: use the auto data above plus the week's Daily Intelligence notes. Remove the pending marker when done._
Analyze: trading performance · prediction accuracy · news interpretation · market-regime accuracy · sector selection ·
stock selection · options selection · position sizing · risk management · timing · trade frequency.

**What worked:**
**What failed:**
**Biggest mistake:**
**Best decision:**
**Strategy with highest expectancy** (with its evidence level):
**Strategy with lowest expectancy** (with its evidence level):
**Current market regime:**
**Recommended adjustments** (as OBSERVATION / HYPOTHESIS / EXPERIMENT only):
**Adjustments NOT justified yet** (and what evidence would be needed):
"""

MONTHLY = f"""
## MONTHLY SYSTEM REVIEW {MONTHLY_PENDING}
_Claude: deeper statistical review. Compare system versions using the auto table. Remove the pending marker when done._
**Version comparison** (paper results per version, evidence level, regimes covered):
**Strategies with established positive / negative expectancy:**
**Prediction calibration** (are high-confidence calls right more often than low-confidence ones?):
**Options learning** (DTE, delta, IV paid, theta, options vs stock):
**Regime learning** (what works in which regime):
**Experiments: status and results:**
**Proposed version changes** (only those meeting the evidence standard; otherwise "none justified"):
"""


# ------------------------------------------------------------------ dynamic notes
def _risk_rules(vc: Dict) -> str:
    cfg = config()
    r, o = cfg["risk"], cfg["options"]
    status = "✅ live parameters match the documented version" if vc["ok"] else \
        "🚨 **" + vc["problem"] + "**:\n" + "\n".join(f"- `{d}`" for d in vc.get("diffs", []))
    return "\n".join([
        f"**System version:** V{vc['version']} · {status}", "",
        _table(["Rule", "Limit", "Meaning"], [
            ["Max loss per trade", _pp(r["max_risk_per_trade"], 1), "Loss if the stop (or full premium) is hit"],
            ["Max total open risk", _pp(r["max_total_risk"], 1), "Sum of open risk across positions"],
            ["Max options exposure", _pp(r["max_options_exposure"], 1), "Premium at risk in options"],
            ["Max single position", _pp(r["max_single_stock"], 1), "Market value of one position"],
            ["Options risk basis", r.get("options_risk_basis"), "Long options and debit spreads count full premium"],
            ["Starting capital", _m(cfg["account"]["starting_cash"]), "No additional capital is ever assumed"]]),
        "", f"Options liquidity: OI ≥ {o['min_open_interest']}, spread ≤ {_pp(o['max_spread_pct'])}, "
            f"DTE {o['min_days']}-{o['max_days']}.",
        "Enforced in code by `desk/risk.py` (sizing takes the minimum across all limits; trades that would breach a limit are rejected)."])


def _strategy_auto() -> str:
    from desk.analysis import SETUP_INFO

    s = config()["screening"]
    return "\n".join(["## Screener setups (V" + memory.current_version() + ")"] +
                     [f"- **{k}** → {memory.SETUP_TO_STRATEGY.get(k)}: {v}" for k, v in SETUP_INFO.items()] +
                     ["- **news_mismatch** → News-driven: material news whose price reaction does not match", "",
                      "## Parameters", f"- Universe filters: price ≥ ${s['min_price']}, avg dollar volume ≥ ${s['min_dollar_volume']:,.0f}",
                      f"- Stop: {s['atr_stop_multiple']}× ATR(14) · minimum reward/risk {s['min_reward_risk']}:1",
                      "- Regime model: SPY vs 50/200-day, breadth, VIX level and term structure, 1-month SPY return → 6 regimes"])


def _versions_auto(L: Dict) -> str:
    vs = memory.versions()
    byv = (L.get("performance") or {}).get("by_system_version") or {}
    rows = [[f"[[{_vname(v)}|V{v['version']}]]", v["date"], v["name"], v.get("approved_by"), byv.get(v["version"], {}).get("n", 0),
             _r(byv.get(v["version"], {}).get("avg_r")), byv.get(v["version"], {}).get("evidence", "no closed trades")]
            for v in vs]
    return _table(["Version", "Date", "Name", "Approved by", "Closed trades", "Avg R", "Evidence"], rows)


def _vname(v: Dict) -> str:
    return f"V{v['version']} — {v['name']}"


def _version_note(v: Dict, L: Dict) -> str:
    byv = ((L.get("performance") or {}).get("by_system_version") or {}).get(v["version"])
    pr = ((L.get("predictions") or {}).get("by_system_version") or {}).get(v["version"])
    return "\n".join([
        f"# {_vname(v)}", f"Date: {v['date']} · Previous: {('V' + v['previous']) if v.get('previous') else 'none'} · "
                         f"Approved by: {v.get('approved_by')}", "",
        f"**WHAT CHANGED:** {v['changes']}", "", f"**WHY:** {v['reason']}", "", f"**EVIDENCE:** {v['evidence']}", "",
        f"**EXPECTED EFFECT:** {v['expected_effect']}", "",
        "**ACTUAL EFFECT** (paper trades opened under this version): " +
        (f"{byv['n']} closed · win {_pp(byv.get('win_rate'))} · avg {_r(byv.get('avg_r'))} · {byv['evidence']}" if byv else "no closed trades yet"),
        "", "**Predictions under this version:** " + (f"{pr['n']} scored · accuracy {_pp(pr['accuracy'])} · {pr['evidence']}" if pr else "none scored yet"),
        "", f"Backtest results: none recorded · Live results: not applicable (paper only).", "",
        "## Parameter snapshot (frozen; never edited)", "```json", json.dumps(v["params"], indent=1), "```",
        f"params hash `{v.get('params_hash')}` · [[System Changelog]] · [[Current System Version]]"])


def _trade_note(t: Dict, ex: Dict) -> (str, str):
    opt = t.get("option") or {}
    closed = t["status"] == "closed"
    strategies = t.get("strategies") or [memory.SETUP_TO_STRATEGY.get(t.get("setup"), "Unclassified")]
    tags = ["trade", "trade/completed" if closed else "trade/active"] + [f"strategy/{_slug(s)}" for s in strategies]
    for k, pre in (("regime_trend", "regime"), ("regime_vol", "vol"), ("regime_risk", "risk"), ("catalyst_category", "catalyst")):
        if t.get(k):
            tags.append(f"{pre}/{_slug(t[k])}")
    fm = {"type": "trade", "trade_id": t["id"], "ticker": t["ticker"], "instrument": t["instrument"],
          "direction": t["direction"], "status": t["status"], "opened": t["opened"], "closed": t.get("closed"),
          "strategy": ", ".join(strategies), "setup": t.get("setup"), "catalyst_category": t.get("catalyst_category"),
          "regime": t.get("regime"), "regime_trend": t.get("regime_trend"), "regime_vol": t.get("regime_vol"),
          "regime_risk": t.get("regime_risk"), "confidence": t.get("confidence"), "system_version": t.get("system_version"),
          "pnl": round(t["pnl"], 2) if closed else None, "r_multiple": round(t["r_multiple"], 2) if closed else None,
          "tags": tags}
    mult = 100 if t["instrument"] == "option" else 1
    if closed:
        status = (f"**COMPLETED {t['closed']}**: exit {_m(t.get('exit_price'))} ({t.get('exit_reason')}) → P&L "
                  f"**{_m(t['pnl'])}** ({_p(t.get('return_pct'))}, {_r(t.get('r_multiple'))}, {t.get('holding_days')} days)")
    else:
        mark = t.get("last_mark", t["entry_price"])
        sign = -1 if (t["direction"] == "SHORT" and t["instrument"] == "stock") else 1
        un = sign * (mark - t["entry_price"]) * t["quantity"] * mult
        status = f"**ACTIVE** · last mark {_m(mark)} → unrealized {_m(un)} ({_r(un / t['risk'])}). Outcome unknown until closed."
    exp_move = t.get("expected_return")
    rows = [["Opened", f"{t['opened']} ET"], ["Ticker / direction", f"[[{(opt.get('underlying') or t['ticker'])}]] {t['direction']}"],
            ["Instrument", t["instrument"] + (f" · {opt.get('structure')} {opt.get('expiry')} {opt.get('long_strike')}"
                                              f"{'/' + str(opt.get('short_strike')) if opt.get('short_strike') else ''}" if opt else "")],
            ["Strategy · setup", f"{', '.join(strategies)} · {t.get('setup')}"],
            ["Catalyst category", t.get("catalyst_category") or "not recorded"],
            ["Regime at entry", f"analyst {t.get('regime') or 'n/a'} → {t.get('regime_trend')} · {t.get('regime_vol')} · {t.get('regime_risk')} "
                                f"(rule: {t.get('regime_rule')}, {t.get('regime_source')})"],
            ["Expected direction", "up" if t["direction"] == "LONG" else "down"],
            ["Expected magnitude (to target)", _p(exp_move) if t["instrument"] == "stock" else f"{_n(exp_move)}× premium at target"],
            ["Expected timeframe", f"{t['horizon_days']} trading days" if t.get("horizon_days") else "not recorded at entry"],
            ["Entry", _m(t["entry_price"]) + (f" (underlying {_m(t.get('underlying_entry'))})" if opt else "")],
            ["Stop / target", f"{_m(t['stop'])} / {_m(t['target'])}" + (" (underlying levels)" if opt else "")],
            ["Position size", f"{t['quantity']} {'contracts' if opt else 'shares'} · cost {_m(t['cost'])}"],
            ["Max loss (risk)", _m(t["risk"])], ["Confidence", t.get("confidence")], ["System version", f"V{t.get('system_version', '1.0')}"]]
    if opt:
        rows += [["DTE at entry", opt.get("dte")], ["Delta (long leg / net)", f"{_n(opt.get('long_delta'))} / {_n(opt.get('net_delta'))}"],
                 ["IV · IV rank · HV20", f"{_pp(opt.get('iv'), 1)} · {_n(opt.get('iv_rank'))} · {_pp(opt.get('hv20'), 1)}"],
                 ["Theta/day (position per contract)", _m(opt.get("theta_day"))], ["Volume / OI", f"{opt.get('volume')} / {opt.get('oi')}"],
                 ["Moneyness", _p(opt.get("otm_pct")) + " OTM (negative = ITM)"], ["Expected move to expiry", _pp(opt.get("expected_move_pct"), 1)]]
    rows += [["MFE (best move after entry)", f"{_p(ex.get('mfe_pct'))} · {_r(ex.get('mfe_r'))}" if "mfe_pct" in ex else "n/a until the first full day after entry"],
             ["MAE (worst move after entry)", f"{_p(ex.get('mae_pct'))} · {_r(ex.get('mae_r'))}" if "mae_pct" in ex else "n/a"],
             ["Realized vol over the hold", _pp(ex.get("realized_vol"), 1) if "realized_vol" in ex else "n/a"]]
    if closed:
        rows += [["Exit", f"{_m(t.get('exit_price'))} on {t.get('closed')} ({t.get('exit_reason')})"],
                 ["P&L · return · R", f"{_m(t['pnl'])} · {_p(t.get('return_pct'))} · {_r(t.get('r_multiple'))}"],
                 ["Holding period", f"{t.get('holding_days')} days"]]
        if opt:
            rows += [["Underlying exit", _m(ex.get("underlying_exit"))],
                     ["Stock on the same thesis (counterfactual)", _r(ex.get("stock_counterfactual_r"))]]
    body = "\n".join([f"# {_trade_name(t)}: {t['direction']} {t['ticker']}", "", status, "",
                      _table(["Field", "Value"], rows), "",
                      "**Original thesis:** " + (t.get("reason") or "n/a"), "", "**Catalyst:** " + (t.get("catalyst") or "n/a"),
                      "", ("**Notes:** " + t["notes"].strip()) if t.get("notes", "").strip() else "",
                      f"Opened in [[{_di(t['opened'][:10])}]]" + (f" · closed in [[{_di(t['closed'][:10])}]]" if closed else "") +
                      " · [[Portfolio]] · [[Performance Dashboard]]"])
    return _front(fm), body


def _portfolio(book: Dict, L: Dict) -> str:
    from desk import paper

    acct = paper.account(book)
    tr = book["trades"]
    op = [t for t in tr if t["status"] == "open"]
    cl = sorted([t for t in tr if t["status"] == "closed"], key=lambda x: x.get("closed", ""), reverse=True)
    lim = config()["risk"]
    L_ = [f"*Synced {now_ny():%Y-%m-%d %H:%M} ET · marks from the last ledger update (Yahoo, delayed) · PAPER ONLY*", "",
          _table(["Equity", "Cash", "Open value", "Realized", "Unrealized", "Return"],
                 [[_m(acct["equity"]), _m(acct["cash"]), _m(acct["open_market_value"]), _m(acct["realized_pnl"]),
                   _m(acct["unrealized_pnl"]), _p(acct["total_return"], 2)]]), "",
          "## Exposure vs limits",
          _table(["Measure", "Now", "Limit"], [
              ["Open risk", f"{_m(acct['open_risk'])} ({_pp(acct['open_risk'] / acct['equity'], 2)})", _pp(lim["max_total_risk"])],
              ["Options exposure", f"{_m(acct['options_exposure'])} ({_pp(acct['options_exposure'] / acct['equity'], 2)})", _pp(lim["max_options_exposure"])],
              ["Largest position", (lambda m: f"{m[0]} {_pp(m[1] / acct['equity'], 1)}")(max(((t['ticker'], t.get('last_mark', t['entry_price']) * t['quantity'] * (100 if t['instrument'] == 'option' else 1)) for t in op), key=lambda x: x[1], default=('none', 0))), _pp(lim["max_single_stock"])],
              ["Cash", _pp(acct["cash"] / acct["equity"], 1), "cash is a position"]]), "",
          f"## Active positions ({len(op)})",
          _table(["Trade", "Dir", "Qty", "Entry", "Mark", "Stop", "Target", "Risk", "Strategy", "Opened"],
                 [[f"[[{_trade_name(t)}]]", t["direction"], t["quantity"], _m(t["entry_price"]), _m(t.get("last_mark")),
                   _m(t["stop"]), _m(t["target"]), _m(t["risk"]), ", ".join(t.get("strategies") or []), t["opened"]] for t in op]), "",
          f"## Completed trades ({len(cl)})",
          _table(["Trade", "Dir", "Entry", "Exit", "P&L", "R", "Days", "Exit reason", "Strategy"],
                 [[f"[[{_trade_name(t)}]]", t["direction"], _m(t["entry_price"]), _m(t.get("exit_price")), _m(t.get("pnl")),
                   _r(t.get("r_multiple")), t.get("holding_days"), t.get("exit_reason"), ", ".join(t.get("strategies") or [])] for t in cl])]
    eq = paper.EQUITY
    if eq.exists():
        df = pd.read_csv(eq).tail(30)
        L_ += ["", "## Equity history (last 30 updates)",
               _table(["Date", "Equity", "Cash", "Open risk", "Return"],
                      [[r["date"], _m(r["equity"]), _m(r["cash"]), _m(r["open_risk"]), _p(r["total_return"], 2)] for _, r in df.iloc[::-1].iterrows()])]
    return "\n".join(L_)


def _perf_dashboard(L: Dict) -> str:
    P = L.get("performance") or {}
    ov = P.get("overall") or {}
    out = [f"*Learning run {L.get('generated', 'never')} · data through {L.get('data_through')} · closed paper trades only; "
           "open trades are excluded (their outcome is unknown).*", "",
           f"## Overall: {ov.get('n', 0)} closed trades · {ov.get('evidence', '')}",
           _table(PERF_HEAD, _perf_rows({"All trades": ov})) if ov.get("n") else "_No closed trades yet. Nothing can be concluded._", ""]
    for key, title in (("by_strategies", "By strategy"), ("by_regime_trend", "By market regime (trend)"),
                       ("by_regime_vol", "By volatility regime"), ("by_regime_risk", "By risk-on / risk-off"),
                       ("by_catalyst_category", "By catalyst"), ("by_sector", "By sector"), ("by_instrument", "By instrument"),
                       ("by_direction", "By direction"), ("by_structure", "By structure"), ("by_system_version", "By system version")):
        out += [f"## {title}", _table(PERF_HEAD, _perf_rows(P.get(key) or {})), ""]
    sbr = L.get("strategy_by_regime") or {}
    if sbr:
        regs = sorted({r for v in sbr.values() for r in v})
        out += ["## Strategy × regime (avg R, n)", _table(["Strategy"] + regs, [[s] + [f"{_r(v.get(r, {}).get('avg_r'))} (n={v.get(r, {}).get('n', 0)})" for r in regs] for s, v in sbr.items()])]
    out += ["", "Evidence levels and robustness checks: [[Trading Constitution#4. Evidence standards (anti-overfitting)|evidence standards]]. "
            "Screener-signal statistics (larger sample, hypothetical): [[Setup Forward Test]]."]
    return "\n".join(out)


def _prediction_note(L: Dict) -> str:
    st = L.get("predictions") or {}
    ps = memory.predictions()
    ov = st.get("overall")
    out = [f"Total {st.get('total', 0)} · scored {st.get('scored', 0)} · pending {st.get('pending', 0)}", ""]
    if ov:
        out += [f"**Accuracy {_pp(ov['accuracy'])}** (95% CI {_pp(ov['ci95'][0])}-{_pp(ov['ci95'][1])}) vs {_pp(ov['implied_by_confidence'])} implied by stated confidence · "
                f"Brier {_n(ov['brier'], 3)} (lower is better; 0.25 = coin flip) · {ov['evidence']}", ""]
        for key in ("by_confidence", "by_category", "by_strategy", "by_direction", "by_regime_trend", "by_ticker"):
            d = st.get(key) or {}
            if d:
                out += [f"### {key[3:].replace('_', ' ').title()}", _table(["Group", "n", "Accuracy", "Implied", "Brier", "Evidence"],
                        [[k, v["n"], _pp(v["accuracy"]), _pp(v["implied_by_confidence"]), _n(v["brier"], 3), v["evidence"]] for k, v in d.items()]), ""]
    else:
        out += ["_No predictions scored yet. Calibration needs ≥ 20 scored predictions._", ""]
    out += ["## All predictions", _table(["#", "Made", "Prediction", "Conf.", "Category", "Due", "Start", "Result", "Return", "vs bench", "Correct"],
            [[p["id"], p["created"], f"[[{p['ticker']}]]: {p['statement']}", p["confidence"], p.get("category") or "", p["due"],
              _m(p["start_price"]), p.get("status"), _p(p.get("actual_return", p.get("mark_return"))), _p(p.get("relative_return")),
              {True: "✔", False: "✘"}.get(p.get("correct"), "pending")] for p in reversed(ps)])]
    return "\n".join(out)


def _event_rows(es: List[Dict]) -> List[List]:
    rows = []
    for e in sorted(es, key=lambda x: x["date"], reverse=True):
        rx = e.get("reaction") or {}
        ret = rx.get("returns") or {}
        act = "; ".join(f"{k} {_p(v.get('1d'))} / {_p(v.get('5d'))}" for k, v in ret.items()) or rx.get("status", "pending")
        rows.append([e["date"] + (" (after close)" if e.get("after_close") else ""), e["category"], e["title"], e["expected_impact"],
                     act, ", ".join(f"[[{t}]]" for t in e.get("tickers", [])), ", ".join(e.get("sectors", [])),
                     e.get("options_impact") or "", e.get("lesson") or "_lesson pending_"])
    return rows


EVENT_HEAD = ["Date", "Category", "Event", "Expected impact", "Actual impact (1d / 5d)", "Stocks", "Sectors", "Options impact", "Lesson"]


def _events_note(es: List[Dict], L: Dict, cats=None) -> str:
    sel = [e for e in es if cats is None or e["category"] in cats]
    st = L.get("events") or {}
    pat = [[k, v["n"], _p(v["avg_spy_1d"], 2), _pp(v["avg_abs_spy_1d"], 2), _p(v.get("avg_spy_5d"), 2),
            _pp(v.get("expectation_hit_rate")), v["evidence"]] for k, v in st.items() if cats is None or k in cats]
    return "\n".join([f"{len(sel)} event(s). Actual impact = close before the event → close of the reaction day (1d) and 4 sessions later (5d). "
                      "Intraday events are measured close-to-close, so the whole session is included.", "",
                      "## Patterns by category", _table(["Category", "Events", "Avg SPY 1d", "Avg |SPY 1d|", "Avg SPY 5d", "SPY expectation hit", "Evidence"], pat), "",
                      "## Events", _table(EVENT_HEAD, _event_rows(sel))])


def _forward_test(L: Dict) -> str:
    F = L.get("forward_test") or {}
    out = ["Every screener signal is tested as a hypothetical trade: entry at the next session's open (same day if the "
           "scan ran pre-market), the scan's stop and target, stop checked first, exit at the close after "
           f"20 trading days. **Hypothetical: not trades, no costs, but no hindsight either** (signals are logged before outcomes).", "",
           f"Signals logged {F.get('signals', 0)} · resolved {F.get('resolved', 0)} · still open {F.get('open', 0)}", "",
           "## Overall", _table(PERF_HEAD, _perf_rows({"All signals": F.get("overall") or {}}, money=False)) if (F.get("overall") or {}).get("n") else "_No resolved signals yet._",
           "", "## By setup", _table(PERF_HEAD, _perf_rows(F.get("by_setup") or {}, money=False)),
           "", "## By regime", _table(PERF_HEAD, _perf_rows(F.get("by_regime") or {}, money=False)),
           "", "## By direction", _table(PERF_HEAD, _perf_rows(F.get("by_direction") or {}, money=False))]
    sbr = F.get("setup_by_regime") or {}
    if sbr:
        regs = sorted({r for v in sbr.values() for r in v})
        out += ["", "## Setup × regime (avg R, n)", _table(["Setup"] + regs, [[s] + [f"{_r(v.get(r, {}).get('avg_r'))} (n={v.get(r, {}).get('n', 0)})" for r in regs] for s, v in sbr.items()])]
    rec = F.get("recent") or []
    out += ["", "## Latest 40 signals", _table(["Date", "Ticker", "Dir", "Setups", "Stop", "Target", "Status", "Entry", "Exit", "R / mark"],
            [[s["date"], f"[[{s['ticker']}]]", s["direction"], s["setups"], _n(s["stop"]), _n(s["target"]), s.get("status"),
              _n(s.get("entry")), (s.get("exit_reason") or ""), _r(s.get("r") if s.get("r") is not None else s.get("mark_r"))] for s in reversed(rec)])]
    return "\n".join(out)


def _options_learning(L: Dict, book: Dict) -> str:
    O = L.get("options") or {}
    ex = L.get("trades") or {}
    ot = [t for t in book["trades"] if t["instrument"] == "option"]
    out = [f"Closed option trades: {O.get('closed_option_trades', 0)}. Questions and rules: [[Options Playbook]].", ""]
    for key, title in (("by_structure", "By structure"), ("by_dte_bucket", "By DTE at entry"),
                       ("by_delta_bucket", "By |delta| of the long leg"), ("by_otm_bucket", "By moneyness")):
        out += [f"## {title}", _table(PERF_HEAD, _perf_rows(O.get(key) or {})), ""]
    for key, title in (("options_vs_stock", "Options vs the stock for the same thesis"), ("iv_vs_realized", "Are we overpaying for IV?"),
                       ("theta", "Theta cost")):
        out += [f"## {title}", "```json\n" + json.dumps(O[key], indent=1) + "\n```" if O.get(key) else "_no closed option trades yet_", ""]
    rows = []
    for t in ot:
        o, x = t.get("option") or {}, ex.get(str(t["id"]), {})
        rows.append([f"[[{_trade_name(t)}]]", o.get("underlying"), o.get("type"), f"{o.get('long_strike')}/{o.get('short_strike') or '-'}",
                     o.get("expiry"), o.get("dte"), _m(t["entry_price"]), _m(t.get("exit_price")), _n(o.get("long_delta")),
                     _pp(o.get("iv"), 1), _n(o.get("iv_rank")), _m(o.get("theta_day")), f"{o.get('volume')}/{o.get('oi')}",
                     _m(t.get("underlying_entry")), _m(x.get("underlying_exit")), t.get("catalyst_category") or "",
                     _pp(o.get("expected_move_pct"), 1), _p(x.get("mfe_pct")), _m(t.get("pnl")), t["status"]])
    out += ["## All option trades", _table(["Trade", "Underlying", "C/P", "Strikes", "Expiry", "DTE", "Entry prem.", "Exit prem.", "Delta",
                                            "IV", "IV rank", "Theta/d", "Vol/OI", "Und. entry", "Und. exit", "Catalyst", "Exp. move",
                                            "MFE", "P&L", "Status"], rows)]
    return "\n".join(out)


def _regime_history(L: Dict) -> str:
    R = L.get("regimes") or {}
    hist = R.get("history") or []
    return "\n".join([f"{R.get('days_logged', 0)} day(s) logged (latest scan of each day; rule-based model).", "",
                      _table(["Date", "Rule regime", "Score", "SPY", "VIX", "VIX3M", "Inverted", "Breadth >50d", "SPY vs 50d", "Fed proxy"],
                             [[h["date"], h["regime_rule"], h.get("score"), _n(h.get("spy")), _n(h.get("vix")), _n(h.get("vix3m")),
                               h.get("vix_term_inverted"), _pp(h.get("breadth_above_50d")), _p(h.get("spy_vs_50d")), h.get("fed_signal")]
                              for h in reversed(hist)])])


def _regime_playbook(L: Dict) -> str:
    R = (L.get("regimes") or {}).get("by_regime") or {}
    P = L.get("performance") or {}
    F = L.get("forward_test") or {}
    out = ["## What followed each regime label (SPY and sectors)",
           _table(["Regime", "Days", "SPY fwd 5d", "% positive", "SPY fwd 20d", "Best sectors 5d vs SPY", "Worst sectors", "Evidence"],
                  [[k, v["days"], _p(v.get("spy_fwd_5d_avg")), _pp(v.get("spy_fwd_5d_pos_share")), _p(v.get("spy_fwd_20d_avg")),
                    ", ".join(f"{a} {_p(b)}" for a, b in v.get("best_sectors_5d_vs_spy") or []),
                    ", ".join(f"{a} {_p(b)}" for a, b in v.get("worst_sectors_5d_vs_spy") or []), v["evidence"]] for k, v in R.items()]), "",
           "## Paper trades by regime", _table(PERF_HEAD, _perf_rows(P.get("by_regime_trend") or {})), "",
           "## Screener signals by regime (forward test)", _table(PERF_HEAD, _perf_rows(F.get("by_regime") or {}, money=False))]
    sbr = L.get("strategy_by_regime") or {}
    if sbr:
        regs = sorted({r for v in sbr.values() for r in v})
        out += ["", "## Which strategies work in which regime (paper trades)",
                _table(["Strategy"] + regs, [[s] + [f"{_r(v.get(r, {}).get('avg_r'))} (n={v.get(r, {}).get('n', 0)})" for r in regs] for s, v in sbr.items()])]
    return "\n".join(out)


def _macro_log(L: Dict) -> str:
    hist = (L.get("regimes") or {}).get("history") or []
    return "\n".join(["Daily macro snapshot from the scans (FRED-based Fed proxy). Full economic tables are in each day's raw scan.", "",
                      _table(["Date", "Fed proxy", "VIX", "SPY vs 200d", "Daily note"],
                             [[h["date"], h.get("fed_signal"), _n(h.get("vix")), _p(h.get("spy_vs_200d")), f"[[{_di(h['date'])}]]"] for h in reversed(hist)]),
                      "", "Events: [[Macro Events]] · [[News Reaction Database]] · Politics: [[Political & Geopolitical Events]]"])


def _company(t: str, book: Dict, sig: pd.DataFrame, L: Dict) -> str:
    trades = [x for x in book["trades"] if t in (x["ticker"], (x.get("option") or {}).get("underlying"))]
    ps = [p for p in memory.predictions() if t in (p["ticker"], p.get("benchmark"))]
    es = [e for e in memory.events() if t in e.get("tickers", []) + e.get("sectors", [])]
    sg = sig[sig["ticker"] == t] if len(sig) else sig
    fwd = {(s["date"], s["ticker"]): s for s in (L.get("forward_test") or {}).get("recent") or []}
    return "\n".join([
        f"## Paper trades ({len(trades)})",
        _table(["Trade", "Opened", "Dir", "Status", "P&L", "R", "Strategy"],
               [[f"[[{_trade_name(x)}]]", x["opened"], x["direction"], x["status"], _m(x.get("pnl")), _r(x.get("r_multiple")),
                 ", ".join(x.get("strategies") or [])] for x in trades]), "",
        f"## Predictions ({len(ps)})",
        _table(["#", "Made", "Prediction", "Conf.", "Result", "Correct"],
               [[p["id"], p["created"], p["statement"], p["confidence"], _p(p.get("actual_return", p.get("mark_return"))),
                 {True: "✔", False: "✘"}.get(p.get("correct"), "pending")] for p in ps]), "",
        f"## Events ({len(es)})", _table(EVENT_HEAD, _event_rows(es)), "",
        f"## Screener signals ({len(sg)})",
        _table(["Date", "Dir", "Setups", "Stop", "Target", "Regime", "Outcome"],
               [[r["date"], r["direction"], r["setups"], _n(r["stop"]), _n(r["target"]), r["regime_rule"],
                 (lambda f: f"{f.get('status')} {_r(f.get('r') if f.get('r') is not None else f.get('mark_r'))}" if f else "")(fwd.get((r["date"], t)))]
                for _, r in sg.iterrows()])])


def _daily(day: str, files: List[Path], brief: Optional[Dict], book: Dict) -> str:
    report = next((f for f in files if f.name == f"{day}_report.md"), None)
    scans = sorted(f for f in files if "_scan" in f.name)
    out = []
    if report:
        txt = report.read_text(encoding="utf-8")
        dec = re.search(r"FINAL DECISION:\s*\**([A-Z /-]+?)\**\s*$", txt, re.M)
        reg = re.search(r"MARKET REGIME:\s*\**([A-Z /-]+?)\**[\s(]", txt)
        out += [f"**Decision:** {dec.group(1).strip() if dec else 'see report'} · **Analyst regime:** {reg.group(1).strip() if reg else 'n/a'}", ""]
        for title in ("BEST TRADE OF THE DAY", "OPTIONS TRADE OF THE DAY", "FINAL DECISION", "INTRADAY UPDATE", "CLOSE UPDATE"):
            for m in re.finditer(rf"^{title}.*?$\n(.*?)(?=^━|\Z)", txt, re.S | re.M):
                out += [f"### {m.group(0).splitlines()[0].strip().strip('*')}", m.group(1).strip(), ""]
    else:
        out += ["No analyst report this day (scan data only).", ""]
    if brief:
        r = brief.get("regime", {})
        out += ["## Latest scan snapshot", f"{brief.get('generated')} · rule regime **{r.get('regime')}** (score {r.get('score')}) · "
                f"SPY {_n(r.get('spy'))} · VIX {_n(r.get('vix'))} vs VIX3M {_n(r.get('vix3m'))} · breadth {_pp(r.get('breadth_above_50d'))} · "
                f"Fed proxy: {(brief.get('fed_proxy') or {}).get('signal')} · data gaps: {', '.join(brief.get('data_gaps') or []) or 'none'}", ""]
    if memory.SIGNALS.exists():
        sg = pd.read_csv(memory.SIGNALS)
        sg = sg[sg["date"].astype(str) == day]
        out += [f"## Screener signals logged ({len(sg)}; forward-tested in [[Setup Forward Test]])",
                _table(["Time", "Ticker", "Dir", "Setups", "Entry ref", "Stop", "Target", "R:R"],
                       [[r["generated"][11:], f"[[{r['ticker']}]]", r["direction"], r["setups"], _n(r["entry_ref"]), _n(r["stop"]),
                         _n(r["target"]), _n(r["reward_risk"], 1)] for _, r in sg.iterrows()]), ""]
    ev = [e for e in memory.events() if e["date"] == day or e["recorded"][:10] == day]
    out += [f"## Events recorded ({len(ev)}; [[News Reaction Database]])", _table(EVENT_HEAD, _event_rows(ev)), ""]
    pm = [p for p in memory.predictions() if p["created"][:10] == day]
    psc = [p for p in memory.predictions() if p.get("scored_on") == day]
    out += [f"## Predictions made today ({len(pm)})", _table(["#", "Prediction", "Conf.", "Due"], [[p["id"], p["statement"], p["confidence"], p["due"]] for p in pm]), "",
            f"## Predictions scored today ({len(psc)})", _table(["#", "Prediction", "Return", "Correct"],
            [[p["id"], p["statement"], _p(p.get("actual_return")), "✔" if p.get("correct") else "✘"] for p in psc]), ""]
    best: Dict[str, List[str]] = {}
    for f in scans:
        for line in _section(f.read_text(encoding="utf-8"), "Top headlines").splitlines():
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            try:
                sc = float(c[0])
            except (ValueError, IndexError):
                continue
            if len(c) >= 6:
                k = re.sub(r"\W+", " ", c[-1].lower()).strip()
                if k not in best or sc > float(best[k][0]):
                    best[k] = c
    if best:
        rows = sorted(best.values(), key=lambda c: -float(c[0]))
        out += [f"## Headlines seen today ({len(rows)}, triage score 0-10)", _table(["Score", "Time", "Source", "Tags", "Ticker", "Headline"], [r[:6] for r in rows]), ""]
    if scans:
        mm = _section(scans[-1].read_text(encoding="utf-8"), "News vs. price")
        if mm:
            out += ["## News vs. price (latest scan)", mm, ""]
    tt = [t for t in book["trades"] if t["opened"][:10] == day or (t.get("closed") or "")[:10] == day]
    out += ["## Paper trades touched today"] + ([f"- [[{_trade_name(t)}]]: {t['direction']} {t['ticker']} ({t['status']})" for t in tt] or ["- none"])
    out += ["", "## Raw files"] + [f"- [[{f.stem}]]" for f in files]
    return "\n".join(out)


def _period_stats(book: Dict, start: str, end: str) -> str:
    cl = [t for t in book["trades"] if t["status"] == "closed" and start <= t["closed"][:10] <= end]
    op = [t for t in book["trades"] if start <= t["opened"][:10] <= end]
    ps = [p for p in memory.predictions() if p.get("scored_on") and start <= p["scored_on"] <= end]
    es = [e for e in memory.events() if start <= e["date"] <= end]
    hit = [e["reaction"]["spy_expectation_correct"] for e in es if (e.get("reaction") or {}).get("spy_expectation_correct") is not None]
    reg = []
    if memory.REGIME_LOG.exists():
        lg = pd.read_csv(memory.REGIME_LOG)
        reg = lg[(lg["date"].astype(str) >= start) & (lg["date"].astype(str) <= end)]["regime_rule"].value_counts().to_dict()
    pnl = sum(t["pnl"] for t in cl)
    return "\n".join([f"Period {start} → {end}",
                      f"- Trades opened {len(op)} · closed {len(cl)} · realized P&L {_m(pnl)} · "
                      f"wins {sum(t['pnl'] > 0 for t in cl)} / losses {sum(t['pnl'] <= 0 for t in cl)}",
                      "- Closed: " + (", ".join(f"[[{_trade_name(t)}]] {_r(t['r_multiple'])}" for t in cl) or "none"),
                      f"- Predictions scored {len(ps)} · correct {sum(bool(p['correct']) for p in ps)}",
                      f"- Events recorded {len(es)} · SPY expectation hits {sum(hit)}/{len(hit)}",
                      f"- Regime days: {reg or 'none logged'}",
                      "- Small samples: describe, don't conclude ([[Trading Constitution#4. Evidence standards (anti-overfitting)|evidence standards]])."])


def _dashboard(book: Dict, L: Dict, root: Path) -> str:
    from desk import paper

    acct = paper.account(book)
    vc = memory.version_check()
    today = str(now_ny().date())
    todo = []
    if not vc["ok"]:
        todo.append(f"🚨 {vc['problem']}: " + "; ".join(vc.get("diffs", [])))
    for f in sorted((root / "02 - Trades" / "Completed").glob("*.md")):
        if REVIEW_PENDING in f.read_text(encoding="utf-8"):
            todo.append(f"Post-trade review due: [[{f.stem}]]")
    for f in sorted((root / "01 - Daily Intelligence").glob("*Daily Intelligence.md"))[-10:]:
        if CRITIQUE_PENDING in f.read_text(encoding="utf-8"):
            todo.append(f"Self-critique due: [[{f.stem}]]")
    for sub, mk in (("Weekly Reviews", WEEKLY_PENDING), ("Monthly Reviews", MONTHLY_PENDING)):
        for f in sorted((root / "10 - Performance" / sub).glob("*.md"))[:-1]:     # past periods only
            if mk in f.read_text(encoding="utf-8"):
                todo.append(f"Review due: [[{f.stem}]]")
    due = [p for p in memory.predictions() if p.get("status") != "scored" and p["due"] <= today]
    todo += [f"Prediction #{p['id']} due {p['due']}: run `desk.py learn`" for p in due]
    if not L or L.get("data_through", "") < str((pd.Timestamp(today) - pd.offsets.BDay(1)).date()):
        todo.append("Learning data is stale: run `desk.py learn`")
    P = (L.get("performance") or {}).get("overall") or {}
    pr = (L.get("predictions") or {}).get("overall") or {}
    F = (L.get("forward_test") or {})
    tags = memory.regime_tags()
    days = sorted((root / "01 - Daily Intelligence").glob("*Daily Intelligence.md"))
    return "\n".join([
        f"*Synced {now_ny():%Y-%m-%d %H:%M} ET · PAPER TRADING ONLY*", "",
        f"**System:** [[Current System Version|V{vc['version']}]] " + ("✅ parameters match" if vc["ok"] else "🚨 undocumented change"),
        f"**Account:** {_m(acct['equity'])} equity · {_m(acct['cash'])} cash · open risk {_pp(acct['open_risk'] / acct['equity'], 2)} of 5% · [[Portfolio]]",
        f"**Regime (latest scan, rule):** {tags['regime_rule']} → {tags['regime_trend']} · {tags['regime_vol']} · {tags['regime_risk']}",
        f"**Latest intelligence:** " + (f"[[{days[-1].stem}]]" if days else "none"), "",
        "## ✅ Action items", *([f"- [ ] {x}" for x in todo] or ["- Nothing due."]), "",
        "## 📈 Evidence so far",
        f"- Closed trades: {P.get('n', 0)} · {P.get('evidence', 'no closed trades')} → [[Performance Dashboard]]",
        f"- Predictions: {(L.get('predictions') or {}).get('scored', 0)} scored / {(L.get('predictions') or {}).get('total', 0)}"
        + (f" · accuracy {_pp(pr.get('accuracy'))} · {pr.get('evidence')}" if pr else "") + " → [[Prediction Accuracy]]",
        f"- Screener forward test: {F.get('signals', 0)} signals, {F.get('resolved', 0)} resolved → [[Setup Forward Test]]",
        f"- Events: {len(memory.events())} recorded → [[News Reaction Database]]",
        f"- Options: {(L.get('options') or {}).get('closed_option_trades', 0)} closed option trades → [[Options Learning]]", "",
        "## 🧭 Map",
        "- **00 System:** [[Trading Constitution]] · [[Risk Rules]] · [[Strategy]] · [[Current System Version]] · [[System Changelog]] · [[Daily Workflow]]",
        "- **01** Daily Intelligence · **02** Trades (Active / Completed) · **03** [[Portfolio]] · [[Watchlist]] · **04** Companies",
        "- **05** [[Options Learning]] · [[Options Playbook]] · **06** [[News Reaction Database]] · [[Macro Events]] · [[Macro Log]]",
        "- **07** [[Political & Geopolitical Events]] · **08** [[Regime History]] · [[Regime Playbook]]",
        "- **09** [[Improvement Pipeline]] · [[Setup Forward Test]] · [[Backtests]] · **10** [[Performance Dashboard]] · [[Prediction Accuracy]] · **11** System Versions"])


# ------------------------------------------------------------------ main
def sync(quiet: bool = False) -> Dict[str, object]:
    root = vault_dir()
    if root is None:
        return {"skipped": "vault folder not configured or not found"}
    from desk import learning, paper

    book = paper.load()
    L = learning.load()
    today = str(now_ny().date())
    changed: List[str] = []

    def mark(ok: bool, p: Path):
        if ok:
            changed.append(str(p.relative_to(root)))

    S, D, T = root / "00 - System", root / "01 - Daily Intelligence", root / "02 - Trades"
    # --- static notes and templates (written once, then owned by Samanyu/Claude)
    for rel, txt in (("00 - System/Trading Constitution.md", CONSTITUTION), ("00 - System/Daily Workflow.md", WORKFLOW),
                     ("05 - Options/Options Playbook.md", OPTIONS_PLAYBOOK), ("09 - Strategy Research/Improvement Pipeline.md", PIPELINE),
                     ("09 - Strategy Research/Backtests.md", BACKTESTS), ("03 - Portfolio/Watchlist.md", WATCHLIST)):
        mark(_once(root / rel, txt), root / rel)
    for rel, txt in TEMPLATES.items():
        txt = {"{critique}": CRITIQUE, "{weekly}": WEEKLY, "{monthly}": MONTHLY}.get(txt, txt)
        mark(_once(root / rel, txt), root / rel)
    (root / "09 - Strategy Research" / "Experiments").mkdir(parents=True, exist_ok=True)

    vc = memory.version_check()
    mark(_upsert(S / "Strategy.md", _strategy_auto(), head=STRATEGY), S / "Strategy.md")
    mark(_upsert(S / "Risk Rules.md", _risk_rules(vc), head="---\ntype: system\ntags: [trading/system, risk]\n---\n# Risk Rules",
                 tail="## Rationale and history\n- 2026-10-07 · V1.0 limits set by Samanyu's desk specification. "
                      "Only Samanyu may loosen a limit; every change is a new version."), S / "Risk Rules.md")
    vs = memory.versions()
    cur = next((v for v in vs if v["version"] == memory.current_version()), None)
    mark(_upsert(S / "Current System Version.md",
                 (f"**Current: [[{_vname(cur)}]]** (since {cur['date']}) · " if cur else "**Current version undocumented** · ") +
                 ("✅ live parameters match its snapshot" if vc["ok"] else "🚨 " + vc["problem"] + ": " + "; ".join(vc.get("diffs", []))) +
                 "\n\nChange process: [[Improvement Pipeline]] → evidence standard met → Samanyu approves → `desk.py version new` → new note in `11 - System Versions/`.",
                 head="---\ntype: system\ntags: [trading/system, system/version]\n---\n# Current System Version"), S / "Current System Version.md")
    mark(_upsert(S / "System Changelog.md", _versions_auto(L),
                 head="---\ntype: system\ntags: [trading/system, system/version]\n---\n# System Changelog\n\nEvery version, newest last. Historical versions are never overwritten.",
                 tail="## Notes\n- "), S / "System Changelog.md")
    for v in vs:
        p = root / "11 - System Versions" / f"{_vname(v)}.md"
        mark(_upsert(p, _version_note(v, L), front=_front({"type": "system-version", "version": v["version"], "date": v["date"],
                                                            "tags": ["system/version"]}), tail="## Notes\n- "), p)

    # --- trades: Active/ while open, moved to Completed/ (with everything written in it) once closed
    ex = L.get("trades") or {}
    for t in book["trades"]:
        name = _trade_name(t) + ".md"
        act, done = T / "Active" / name, T / "Completed" / name
        front, body = _trade_note(t, ex.get(str(t["id"]), {}))
        if t["status"] == "closed":
            if act.exists() and not done.exists():
                done.parent.mkdir(parents=True, exist_ok=True)
                txt = act.read_text(encoding="utf-8")
                act.rename(done)
                if REVIEW_PENDING not in txt:
                    done.write_text(txt.replace("## Review (post-trade analysis)", f"## Review (post-trade analysis) {REVIEW_PENDING}", 1), encoding="utf-8")
                mark(True, done)
            tail = TEMPLATES["02 - Trades/Trade Templates/Post-Trade Review Template.md"].replace("## Review (post-trade analysis)", f"## Review (post-trade analysis) {REVIEW_PENDING}")
            if t["instrument"] == "option":
                tail += TEMPLATES["02 - Trades/Trade Templates/Options Post-Trade Review Template.md"]
            mark(_upsert(done, body, front=front, tail=tail), done)
        else:
            tail = TEMPLATES["02 - Trades/Trade Templates/Post-Trade Review Template.md"] + \
                ("\n" + TEMPLATES["02 - Trades/Trade Templates/Options Post-Trade Review Template.md"] if t["instrument"] == "option" else "")
            mark(_upsert(act, body, front=front, tail=tail), act)

    mark(_upsert(root / "03 - Portfolio" / "Portfolio.md", _portfolio(book, L),
                 head="---\ntype: portfolio\ntags: [trading/portfolio]\n---\n# Portfolio ($30,000 paper account)"), root / "03 - Portfolio" / "Portfolio.md")

    # --- daily intelligence + raw files
    by_day: Dict[str, List[Path]] = {}
    for f in sorted(REPORTS.glob("20??-??-??_*.md")):
        dst = D / "Raw" / f.name
        src = f.read_text(encoding="utf-8")
        if not dst.exists() or dst.read_text(encoding="utf-8") != src:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src, encoding="utf-8")
            mark(True, dst)
        by_day.setdefault(f.name[:10], []).append(f)
    for d, files in by_day.items():
        bj = DATA / "briefs" / f"{d}.json"
        brief = json.loads(bj.read_text()) if bj.exists() else None
        reg = (brief or {}).get("regime", {}).get("regime")
        p = D / f"{_di(d)}.md"
        mark(_upsert(p, _daily(d, files, brief, book),
                     front=_front({"type": "daily-intelligence", "date": d, "regime_rule": reg,
                                   "tags": ["trading/daily"] + ([f"regime/{_slug(memory.TREND_MAP.get(reg, reg))}"] if reg else [])}),
                     head=f"# Daily Intelligence: {d}", tail=CRITIQUE), p)

    # --- companies
    sig = pd.read_csv(memory.SIGNALS) if memory.SIGNALS.exists() else pd.DataFrame(columns=["ticker"])
    tick = {(t.get("option") or {}).get("underlying") or t["ticker"] for t in book["trades"]} | set(sig["ticker"])
    tick |= {p["ticker"] for p in memory.predictions()} | {x for e in memory.events() for x in e.get("tickers", [])}
    from desk.common import universe

    uni = universe().set_index("ticker")
    for tk in sorted(tick):
        p = root / "04 - Companies" / f"{tk}.md"
        name = uni["name"].get(tk, tk)
        sector = uni["sector"].get(tk, "")
        mark(_upsert(p, _company(tk, book, sig, L), front=_front({"type": "company", "ticker": tk, "name": name, "sector": sector,
                                                                  "tags": ["company", f"sector/{_slug(sector)}" if sector else "sector/other"]}),
                     head=f"# {tk}: {name}\nSector: {sector}", tail="## Notes (thesis history, business, lessons)\n- "), p)

    # --- research, options, macro, regimes, performance
    es = memory.events()
    notes = {
        "05 - Options/Options Learning.md": (_options_learning(L, book), "trading/options"),
        "06 - Macro/News Reaction Database.md": (_events_note(es, L), "trading/events"),
        "06 - Macro/Macro Events.md": (_events_note(es, L, memory.MACRO_CATS), "trading/macro"),
        "06 - Macro/Macro Log.md": (_macro_log(L), "trading/macro"),
        "07 - Politics & Geopolitics/Political & Geopolitical Events.md": (_events_note(es, L, memory.POLITICAL_CATS), "trading/politics"),
        "08 - Market Regimes/Regime History.md": (_regime_history(L), "trading/regimes"),
        "08 - Market Regimes/Regime Playbook.md": (_regime_playbook(L), "trading/regimes"),
        "09 - Strategy Research/Setup Forward Test.md": (_forward_test(L), "trading/research"),
        "10 - Performance/Performance Dashboard.md": (_perf_dashboard(L), "trading/performance"),
        "10 - Performance/Prediction Accuracy.md": (_prediction_note(L), "trading/performance"),
    }
    tails = {"08 - Market Regimes/Regime Playbook.md": "## Regime observations (dated, with evidence level)\n- ",
             "06 - Macro/News Reaction Database.md": "## Pattern notes (only with enough similar events)\n- ",
             "05 - Options/Options Learning.md": "## Options observations\n- "}
    for rel, (body, tag) in notes.items():
        p = root / rel
        mark(_upsert(p, body, head=f"---\ntype: analytics\ntags: [{tag}]\n---\n# {Path(rel).stem}", tail=tails.get(rel, "")), p)

    # --- weekly + monthly review notes for the current period (data refreshed while the period is open)
    wk = _week(today)
    from datetime import date, timedelta

    d0 = date.fromisoformat(today)
    ws, we = str(d0 - timedelta(days=d0.weekday())), str(d0 + timedelta(days=6 - d0.weekday()))
    p = root / "10 - Performance" / "Weekly Reviews" / f"{wk} Weekly Review.md"
    mark(_upsert(p, _period_stats(book, ws, we), front=_front({"type": "weekly-review", "week": wk, "tags": ["review/weekly"]}),
                 head=f"# Weekly Review {wk}", tail=WEEKLY), p)
    mo = today[:7]
    p = root / "10 - Performance" / "Monthly Reviews" / f"{mo} Monthly Review.md"
    mark(_upsert(p, _period_stats(book, mo + "-01", mo + "-31") + "\n\n## By system version\n" + _versions_auto(L),
                 front=_front({"type": "monthly-review", "month": mo, "tags": ["review/monthly"]}), head=f"# Monthly Review {mo}", tail=MONTHLY), p)

    mark(_upsert(root / "Trading Dashboard.md", _dashboard(book, L, root),
                 head="---\ntype: dashboard\ntags: [trading]\n---\n# Trading Dashboard",
                 tail="## Notes\n- Start here. Claude reads this, the [[Trading Constitution]] and the latest Daily Intelligence before every desk session."),
         root / "Trading Dashboard.md")
    out = {"vault": str(root), "changed": changed, "synced": f"{now_ny():%Y-%m-%d %H:%M} ET"}
    if not quiet:
        print(f"Obsidian sync → {root}\n  {len(changed)} file(s) updated" + "".join(f"\n  · {c}" for c in changed[:60]))
    return out


def safe_sync() -> None:
    """Called after desk commands; never lets a vault problem break trading research."""
    try:
        r = sync(quiet=True)
        if r.get("changed"):
            print(f"(Obsidian: {len(r['changed'])} note(s) updated)")
    except Exception as exc:  # pragma: no cover
        print(f"(Obsidian sync skipped: {exc.__class__.__name__}: {exc})")
