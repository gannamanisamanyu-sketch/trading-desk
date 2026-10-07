"""Trading Desk command line. RESEARCH AND PAPER TRADING ONLY (no brokerage connection).

    python desk.py scan                         # morning scan → reports/YYYY-MM-DD_scan.md
    python desk.py options NVDA --dir LONG --target 210 --stop 180 --days 21
    python desk.py size --entry 100 --stop 95 --target 112 [--dir LONG]
    python desk.py news NVDA                    # ticker news, StockTwits sentiment, fundamentals
    python desk.py quote NVDA AAPL SPY
    python desk.py paper status | update | stats
    python desk.py paper add-stock  NVDA LONG 10 185.20 178 205 --setup breakout --reason "..." --catalyst "..."
    python desk.py paper add-option NVDA LONG 1 --from-last-options --reason "..." --catalyst "..."
    python desk.py paper close 3 --price 4.10 --reason "manual"
    python desk.py obsidian                     # mirror trades, scans, news and stats into the Obsidian vault
    python desk.py context                      # memory briefing before recommending (exposure, history, evidence)
    python desk.py recall NVDA                  # every trade, prediction, event and signal for a ticker
    python desk.py learn                        # end of day: score predictions/events/signals, update statistics
    python desk.py predict add NVDA --direction OUTPERFORM --benchmark SOXX --days 10 --confidence medium --reason "..."
    python desk.py event add --title "..." --date 2026-10-07 --category Fed --expected "..." --tickers NVDA --sectors XLK
    python desk.py version check | show | new --version 1.1 --name ... --changes ... --reason ... --evidence ... --expected ... --approved-by Samanyu
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

import pandas as pd

from desk.common import DATA, config, market_session, stamp

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 80)


def cmd_scan(a):
    from desk.brief import run_scan

    out = run_scan(refresh=not a.cached)
    from desk import memory
    n = memory.record_scan(out)
    print(f"(memory: {n} new screener signal(s) logged for forward testing)")
    r = out["regime"]
    print(f"\nScan complete ({out['runtime_s']}s) · {out['generated']} · session: {out['session']}")
    print(f"Regime: {r.get('regime')} · Fed proxy: {out['fed_proxy'].get('signal')}")
    if out["data_gaps"]:
        print("Data gaps:", *out["data_gaps"], sep="\n  - ")
    s = out["setups"]
    if len(s):
        print(s[["ticker", "direction", "setups", "entry", "stop", "target", "reward_risk", "shares", "risk_$", "score"]]
              .head(10).round(2).to_string(index=False))
    print("\nPacket:", out.get("scan_file", "reports/" + out["generated"][:10] + "_scan.md"))


def cmd_options(a):
    from desk import options as O
    from desk import paper, risk

    ch = O.load_chain(a.ticker.upper())
    print(f"{a.ticker.upper()} spot {ch['spot']:.2f} ({ch['spot_time']}) · chain retrieved {ch.get('retrieved')} · {ch.get('quote_quality')}")
    print(f"HV20 {ch['hv20']:.1%} · HV60 {ch['hv60']:.1%} · ATM 30d IV {ch.get('atm_iv_30d') or float('nan'):.1%} · "
          f"IV rank: {ch['iv_rank'].get('iv_rank')} ({ch['iv_rank'].get('note')})")
    ts = ch.get("term")
    if ts is not None and len(ts):
        print("\nExpected move by expiry (ATM straddle):")
        print(ts.assign(atm_iv=ts.atm_iv.map("{:.1%}".format), expected_move_pct=ts.expected_move_pct.map("{:.1%}".format))
              .head(8).to_string(index=False))
    un = ch["chain"][ch["chain"]["unusual"]].sort_values("volume", ascending=False)
    if len(un):
        print("\nUnusual activity (volume > max(500, 2×OI)):")
        print(un[["expiry", "type", "strike", "volume", "open_interest", "price", "iv"]].head(8).round(3).to_string(index=False))
    if a.target is None or a.stop is None:
        return
    acct = paper.account(paper.load())
    budget = config()["risk"]["max_risk_per_trade"] * acct["equity"]
    res = O.evaluate_structures(ch, a.dir.upper(), a.target, a.stop, a.days, budget)
    if res.empty:
        print("\nNo liquid candidates for this thesis.")
        return
    cols = ["structure", "expiry", "dte", "long_strike", "short_strike", "debit", "max_loss", "max_profit", "breakeven",
            "pop", "pnl_at_target", "pnl_at_stop", "rr_at_target", "delta", "theta_day", "iv", "spread_pct", "oi",
            "contracts_for_budget", "quality", "score"]
    print(f"\nCandidate structures for {a.dir.upper()} target {a.target} / stop {a.stop} within {a.days} days "
          f"(1% risk budget ${budget:,.0f}):")
    print(res[cols].head(8).round(3).to_string(index=False))
    best = res.iloc[0]
    if ch.get("quote_quality", "").startswith("STALE"):
        print("\n⚠ STALE OPTION PRICES (market closed): structures below are a PLANNING estimate only. "
              "Re-run after 9:30 ET before quoting any option price or logging a paper trade.")
    sz = risk.size_option(acct["equity"], best["max_loss"], best["pnl_at_target"], acct["open_risk"], acct["options_exposure"])
    print(f"\nBEST: {best['structure']} {best['expiry']} {best['long_strike']:g}"
          f"{'/' + format(best['short_strike'], 'g') if pd.notna(best['short_strike']) else ''} · debit {best['debit']:.2f} · "
          f"contracts {sz.quantity} · max loss ${sz.risk:,.0f} ({sz.risk_pct:.2%}) · profit at target ${sz.potential_profit:,.0f}")
    for n in sz.notes:
        print("  ⚠", n)
    for n in O.explain_choice(best, res, ch, ch["spot"], a.stop, a.target):
        print("  •", n)
    last = {"ticker": a.ticker.upper(), "direction": a.dir.upper(), "target": a.target, "stop": a.stop,
            "structure": best["structure"], "expiry": best["expiry"], "long_strike": best["long_strike"],
            "short_strike": None if pd.isna(best["short_strike"]) else best["short_strike"], "debit": best["debit"],
            "max_loss": best["max_loss"], "pnl_at_target": best["pnl_at_target"], "contract": best["contract"],
            "short_contract": best["short_contract"], "type": "call" if a.dir.upper() == "LONG" else "put",
            "contracts": sz.quantity, "priced": stamp(), "spot": ch["spot"], "dte": int(best["dte"]),
            "net_delta": float(best["delta"]), "iv": float(best["iv"]), "theta_day": float(best["theta_day"]),
            "vega": float(best["vega"]), "volume": float(best["volume"]), "oi": float(best["oi"]),
            "spread_pct": None if pd.isna(best["spread_pct"]) else float(best["spread_pct"]), "quality": best["quality"],
            "pop": float(best["pop"]), "breakeven": float(best["breakeven"]), "hv20": ch["hv20"],
            "iv_rank": ch["iv_rank"].get("iv_rank"), "atm_iv_30d": ch.get("atm_iv_30d"), "horizon_days": a.days}
    leg = ch["chain"][ch["chain"]["contract"] == best["contract"]]
    last["long_delta"] = float(leg["delta"].iloc[0]) if len(leg) else None
    sgn = 1 if last["type"] == "call" else -1
    last["otm_pct"] = sgn * (best["long_strike"] - ch["spot"]) / ch["spot"]
    ts = ch.get("term")
    if ts is not None and len(ts) and (ts["expiry"] == best["expiry"]).any():
        last["expected_move_pct"] = float(ts.loc[ts["expiry"] == best["expiry"], "expected_move_pct"].iloc[0])
    last["alternatives"] = [{"structure": r["structure"], "expiry": r["expiry"], "dte": int(r["dte"]),
                             "long_strike": r["long_strike"], "short_strike": None if pd.isna(r["short_strike"]) else r["short_strike"],
                             "debit": float(r["debit"]), "contract": r["contract"],
                             "short_contract": None if pd.isna(r["short_contract"]) else r["short_contract"],
                             "rr_at_target": float(r["rr_at_target"])} for _, r in res.iloc[1:4].iterrows()]
    (DATA / "options").mkdir(exist_ok=True)
    snap = {"ticker": a.ticker.upper(), "retrieved": stamp(), "quote_quality": ch.get("quote_quality"), "spot": ch["spot"],
            "atm_iv_30d": ch.get("atm_iv_30d"), "iv_rank": ch["iv_rank"].get("iv_rank"), "hv20": ch["hv20"], "hv60": ch["hv60"],
            "expected_moves": [] if ts is None else ts.head(6).to_dict("records"),
            "best_structure": {k: last.get(k) for k in ("structure", "expiry", "long_strike", "short_strike", "debit", "dte")},
            "top_open_interest": ch["chain"].nlargest(5, "open_interest")[["expiry", "type", "strike", "open_interest", "volume"]].to_dict("records"),
            "source": "Yahoo Finance option chain (delayed; STALE outside market hours)"}
    (DATA / "options" / f"{a.ticker.upper()}.json").write_text(json.dumps(snap, indent=1, default=str))
    (DATA / "last_options.json").write_text(json.dumps(last, indent=2, default=str))


def cmd_size(a):
    from desk import paper, risk

    acct = paper.account(paper.load())
    sz = risk.size_stock(acct["equity"], a.entry, a.stop, a.target, acct["open_risk"], a.dir.upper())
    print(f"equity ${acct['equity']:,.2f} · open risk ${acct['open_risk']:,.0f}")
    print(f"{'OK' if sz.ok else 'REJECTED'}: {sz.quantity} shares · invested ${sz.invested:,.0f} "
          f"({sz.invested / acct['equity']:.1%}) · risk ${sz.risk:,.0f} ({sz.risk_pct:.2%}) · profit at target "
          f"${sz.potential_profit:,.0f} · R:R {sz.reward_risk:.1f} · binding limit: {sz.binding_limit}", *sz.notes, sep="\n")


def cmd_news(a):
    from desk.analysis import fundamentals
    from desk.common import universe
    from desk.news import ticker_news
    from desk.signals_alt import stocktwits_sentiment

    t = a.ticker.upper()
    uni = universe().set_index("ticker")
    name = uni.loc[t, "name"] if t in uni.index else ""
    n = ticker_news(t, name, days=a.days)
    if len(n):
        n["time"] = n["time_utc"].map(lambda x: stamp(x) if x is not None else "")
        print(n[["time", "publisher", "title"]].to_string(index=False))
    print("\nStockTwits:", stocktwits_sentiment(t))
    print("\nFundamentals:", json.dumps(fundamentals(t), indent=1, default=str))


def cmd_quote(a):
    from desk.market import quote

    print(f"session: {market_session()} · retrieved {stamp()} (Yahoo Finance, may be delayed)")
    print(quote([t.upper() for t in a.tickers]).to_string(index=False))


def _memory_fields(a, setup: str, option: bool = False, structure: str = "") -> dict:
    from desk import memory

    strat = memory.check_choice(a.strategy, memory.STRATEGIES, "strategy") or memory.SETUP_TO_STRATEGY.get(setup, "Catalyst")
    strategies = [strat] + ([("Options spread" if "spread" in structure.lower() else "Options directional")] if option else [])
    return {"strategies": strategies, "catalyst_category": memory.check_choice(a.catalyst_category, memory.CATALYSTS, "catalyst category"),
            "horizon_days": a.horizon_days, "system_version": memory.current_version(), **memory.regime_tags(a.regime),
            "scorecard_id": a.scorecard, "event_ids": [int(x) for x in (a.event_ids or "").split(",") if x.strip()],
            "prediction_ids": [int(x) for x in (a.prediction_ids or "").split(",") if x.strip()]}


def cmd_paper(a):
    from desk import paper

    if a.action == "status":
        book = paper.load()
        acct = paper.account(book)
        print(json.dumps({k: round(v, 2) for k, v in acct.items()}, indent=1))
        op = [t for t in book["trades"] if t["status"] == "open"]
        if op:
            print(pd.DataFrame(op)[["id", "opened", "ticker", "instrument", "direction", "quantity", "entry_price",
                                    "last_mark", "stop", "target", "risk", "setup"]].to_string(index=False))
    elif a.action == "update":
        out = paper.update()
        print(json.dumps({k: round(v, 2) for k, v in out["account"].items()}, indent=1), *out["events"], sep="\n")
    elif a.action == "stats":
        print(json.dumps(paper.statistics(), indent=1, default=str))
    elif a.action == "add-stock":
        from desk import risk

        acct = paper.account(paper.load())
        sz = risk.size_stock(acct["equity"], a.entry, a.stop, a.target, acct["open_risk"], a.direction.upper())
        if a.quantity > sz.quantity:
            sys.exit(f"REJECTED: {a.quantity} shares exceeds the risk-engine maximum of {sz.quantity} ({sz.binding_limit})")
        risk_d = a.quantity * abs(a.entry - a.stop)
        t = paper.add_trade(a.ticker, "stock", a.direction, a.quantity, a.entry, a.stop, a.target, a.reason, a.catalyst,
                            a.setup, a.regime, a.confidence, risk_d, abs(a.target - a.entry) / a.entry,
                            extra=_memory_fields(a, a.setup))
        print("Recorded paper trade", json.dumps(t, indent=1))
    elif a.action == "add-option":
        last = json.loads((DATA / "last_options.json").read_text())
        if last["ticker"] != a.ticker.upper():
            sys.exit("last options analysis was for a different ticker; run `desk.py options` first")
        qty = a.quantity or last["contracts"]
        if qty > last["contracts"]:
            sys.exit(f"REJECTED: {qty} contracts exceeds the risk-engine maximum of {last['contracts']}")
        opt = {k: last[k] for k in ("structure", "expiry", "long_strike", "short_strike", "contract", "short_contract", "type")}
        opt["underlying"] = last["ticker"]
        opt.update({k: last.get(k) for k in ("dte", "long_delta", "net_delta", "iv", "iv_rank", "theta_day", "vega", "volume",
                                             "oi", "spread_pct", "quality", "pop", "breakeven", "otm_pct",
                                             "expected_move_pct", "hv20", "priced", "alternatives")})
        extra = _memory_fields(a, a.setup, option=True, structure=last["structure"])
        extra.update({"underlying_entry": last.get("spot"), "horizon_days": a.horizon_days or last.get("horizon_days")})
        t = paper.add_trade(last["ticker"], "option", last["direction"], qty, last["debit"], last["stop"], last["target"],
                            a.reason, a.catalyst, a.setup, a.regime, a.confidence, qty * last["max_loss"],
                            last["pnl_at_target"] / last["max_loss"], option=opt, extra=extra)
        print("Recorded paper options trade", json.dumps(t, indent=1))
    elif a.action == "close":
        book = paper.load()
        t = next(x for x in book["trades"] if x["id"] == a.id and x["status"] == "open")
        paper.close_trade(book, t, a.price, a.reason)
        paper.save(book)
        print(f"Closed #{a.id}: P&L ${t['pnl']:,.2f} ({t['return_pct']:.1%})")


def cmd_predict(a):
    from desk import learning, memory

    if a.action == "add":
        p = memory.add_prediction(a.ticker, a.direction or "", a.days, a.confidence, a.reason, a.catalyst, a.benchmark or "",
                                  a.magnitude, a.category or "", a.strategy or "", a.statement or "", a.regime or "",
                                  confidence_pct=a.confidence_pct, supporting=a.supporting or "", contradicting=a.contradicting or "",
                                  event_id=a.event_id, kind=a.kind, due=a.due or "", asset=a.asset or "")
        anchor = f"anchored at {p['start_price']:.2f} ({p['start_time']})" if p.get("start_price") else "event prediction (resolve with a source)"
        print(f"Prediction #{p['id']} recorded ({p['confidence_pct']:.0f}% confidence): {p['statement']} · {anchor} · due {p['due']}")
    elif a.action == "resolve":
        p = memory.resolve_prediction(a.id, a.outcome == "correct", a.actual or "", a.source or "", a.actual_magnitude)
        print(f"Prediction #{p['id']} resolved: {'correct' if p['correct'] else 'incorrect'}")
    elif a.action == "update":
        p = memory.update_prediction(a.id, lesson=a.lesson, contradicting=a.contradicting, supporting=a.supporting)
        print(f"Prediction #{p['id']} updated")
    else:
        ps = memory.predictions()
        if a.action == "score":
            learning.run()
            ps = memory.predictions()
        df = pd.DataFrame(ps)
        if df.empty:
            print("No predictions recorded yet.")
            return
        cols = [c for c in ("id", "created", "statement", "confidence", "due", "status", "actual_return", "relative_return", "correct") if c in df]
        print(df[cols].tail(30).to_string(index=False))


def cmd_reject(a):
    from desk import memory

    if a.action == "add":
        r = memory.add_rejection(a.ticker, a.direction, a.instrument, a.entry, a.stop, a.target, a.reason, a.thesis or "",
                                 a.catalyst or "", a.quantity, a.alternative or "", a.strategy or "", a.scorecard, a.event_id,
                                 a.notes or "", a.decided or "", a.regime or "")
        print(f"Rejected trade R{r['id']} recorded: {r['direction']} {r['ticker']} · reason: {r['reason']} · "
              f"R:R {r['reward_risk']:.1f} · hypothetical size {r['quantity']}")
    elif a.action == "update":
        r = memory.update_rejection(a.id, process_verdict=a.verdict, lesson=a.lesson)
        print(f"R{r['id']} updated")
    else:
        df = pd.DataFrame(memory.rejections())
        print(df[["id", "decided", "ticker", "direction", "reason", "alternative"]].to_string(index=False) if len(df) else "none")


def cmd_error(a):
    from desk import memory

    if a.action == "add":
        e = memory.add_error(a.subject, a.type, a.reasoning or "", a.happened or "", a.missed or "", a.root_cause or "",
                             a.severity, a.lesson or "", a.correction or "", a.evidence or "", a.date or "", a.trade_id,
                             a.prediction_id, a.event_id, a.source or "")
        print(f"Error E{e['id']} recorded: {e['error_type']} ({e['severity']}) · status OBSERVATION")
    elif a.action == "update":
        e = memory.update_error(a.id, status=a.status, evidence=a.evidence, lesson=a.lesson, proposed_correction=a.correction)
        print(f"E{e['id']} → {e['status']}")
    else:
        df = pd.DataFrame(memory.errors())
        print(df[["id", "date", "subject", "error_type", "severity", "status"]].to_string(index=False) if len(df) else "none")


def cmd_experiment(a):
    from desk import memory

    if a.action == "add":
        x = memory.add_experiment(a.name, a.hypothesis, a.reason, a.dataset, a.baseline, a.alternative, a.min_n, a.notes or "")
        print(f"Experiment X{x['id']} '{x['name']}' started on {x['dataset']}")
    elif a.action == "update":
        x = memory.update_experiment(a.id, decision=a.decision, decision_evidence=a.evidence, notes=a.notes)
        print(f"X{x['id']} → {x.get('decision') or x['status']}")
    else:
        for x in memory.experiments():
            print(f"X{x['id']} {x['name']} [{x['status']}] baseline: {x['baseline_filter']} vs alternative: {x['alternative_filter']}")


def cmd_score(a):
    from desk import memory

    if a.action == "add":
        scores = {f: getattr(a, f) for f in memory.SCORE_FACTORS}
        c = memory.add_scorecard(a.ticker, a.direction, a.strategy or "", scores, a.notes or "")
        print(f"Scorecard S{c['id']} for {c['ticker']}: overall {c['overall']}/10"
              + (f" (capped by weak {', '.join(c['gated_by'])})" if c["gated_by"] else "")
              + f" · weights for {c['strategy'] or 'default'}. Pass --scorecard {c['id']} when recording the trade or rejection.")
    else:
        for c in memory.scorecards():
            print(f"S{c['id']} {c['created']} {c['ticker']} {c['direction']} {c['strategy']} → {c['overall']}")


def cmd_regime(a):
    from desk import regimes

    h = regimes.history()
    if h.empty:
        print("No regime history yet: run desk.py learn")
        return
    r = h.iloc[-1]
    print(f"{r['date']}: {r['trend']} · {r['risk']} · {r['volatility']} (trend pts {r['trend_points']}, risk pts {r['risk_points']})")
    print(f"  SPY/QQQ/IWM trend {r['spy_trend']:+.0f}/{r['qqq_trend']:+.0f}/{r['iwm_trend']:+.0f} · breadth {r['breadth_50d']:.0%} · "
          f"VIX {r['vix']:.1f} (term {r['vix_term']:.2f}) · 10Y {r['tnx']:.2f}% ({r['tnx_1m_bp']:+.0f}bp 1m) · curve {r['curve_10y2y']} · "
          f"HY {r['hy_spread']} · DXY 1m {r['dxy_1m']:+.1%} · oil 1m {r['oil_1m']:+.1%} · gold 1m {r['gold_1m']:+.1%} · "
          f"leaders {r['leaders']} · Fed: {r['fed_signal']}")
    print("Most similar past days:")
    print(regimes.similar_days(k=a.k)[["date", "trend", "risk", "volatility", "distance"]].to_string(index=False))


def cmd_event(a):
    from desk import memory

    if a.action == "add":
        e = memory.add_event(a.title, a.date, a.category, a.expected, (a.tickers or "").split(","), (a.sectors or "").split(","),
                             a.expected_spy or "", a.after_close, a.source or "", a.notes or "", a.what_happened or "",
                             a.market_expected or "", a.actual_vs_expected or "", a.time or "", a.reliability or "",
                             a.interpretation or "", a.second_order or "")
        print(f"Event #{e['id']} recorded: {e['date']} [{e['category']}] {e['title']}")
    elif a.action == "update":
        e = memory.update_event(a.id, lesson=a.lesson, options_impact=a.options_impact, notes=a.notes, time=a.time,
                                what_happened=a.what_happened, market_expected=a.market_expected,
                                actual_vs_expected=a.actual_vs_expected, interpretation=a.interpretation,
                                second_order=a.second_order, reliability=a.reliability)
        print(json.dumps(e, indent=1, default=str))
    else:
        df = pd.DataFrame(memory.events())
        print(df[["id", "date", "category", "title"]].to_string(index=False) if len(df) else "No events recorded yet.")


def cmd_version(a):
    from desk import memory

    if a.action == "check":
        r = memory.version_check()
        print(f"System V{r['version']}: " + ("parameters match the documented version ✔" if r["ok"] else r["problem"]))
        for d in r.get("diffs", []):
            print("  ·", d)
    elif a.action == "new":
        rec = memory.new_version(a.version, a.name, a.changes, a.reason, a.evidence, a.expected, a.approved_by)
        print(f"Recorded V{rec['version']} ({rec['name']}). Now set system.version: \"{rec['version']}\" in config/desk.yaml.")
    else:
        for v in memory.versions():
            print(f"V{v['version']} · {v['date']} · {v['name']} · approved by {v.get('approved_by')}")
        print("current:", memory.current_version())


def cmd_learn(a):
    from desk import learning

    learning.run()


def cmd_recall(a):
    """Everything the desk has on one ticker: trades, predictions, events, signals."""
    from desk import learning, memory, paper

    t = a.ticker.upper()
    L = learning.load()
    trades = [x for x in paper.load()["trades"] if t in (x["ticker"], (x.get("option") or {}).get("underlying"))]
    print(f"== {t}: {len(trades)} paper trade(s)")
    for x in trades:
        ex = (L.get("trades") or {}).get(str(x["id"]), {})
        print(f"  #{x['id']} {x['opened']} {x['direction']} {x['instrument']} @ {x['entry_price']} stop {x['stop']} "
              f"target {x['target']} · {x['status']} · P&L {x.get('pnl', 'open')} · R {x.get('r_multiple', '-')} · "
              f"MFE {ex.get('mfe_r', '-')}R MAE {ex.get('mae_r', '-')}R · {x.get('setup')}\n     thesis: {x.get('reason')}")
    ps = [p for p in memory.predictions() if t in (p["ticker"], p.get("benchmark"))]
    print(f"== predictions: {len(ps)}")
    for p in ps:
        res = "" if p.get("status") != "scored" else (" ✔" if p["correct"] else " ✘") + f" {p['actual_return']:+.1%}"
        print(f"  #{p['id']} {p['created']} {p['statement']} ({p['confidence']}) → {p.get('status')}{res}")
    es = [e for e in memory.events() if t in e.get("tickers", []) + e.get("sectors", [])]
    print(f"== events: {len(es)}")
    for e in es:
        r = ((e.get("reaction") or {}).get("returns") or {}).get(t, {})
        print(f"  #{e['id']} {e['date']} [{e['category']}] {e['title']} · expected: {e['expected_impact']} · "
              f"actual 1d {r.get('1d')} 5d {r.get('5d')} · lesson: {e.get('lesson') or '-'}")
    if memory.SIGNALS.exists():
        sg = pd.read_csv(memory.SIGNALS)
        sg = sg[sg["ticker"] == t]
        print(f"== screener signals: {len(sg)}")
        if len(sg):
            print(sg[["date", "direction", "setups", "entry_ref", "stop", "target", "regime_rule"]].tail(10).to_string(index=False))


def cmd_context(a):
    """Daily memory review: relevance-based retrieval in the playbook's priority order (not the whole vault)."""
    from desk import learning, memory, paper

    L = learning.load()
    I = L.get("intelligence") or {}
    book = paper.load()
    tickers = [t.strip().upper() for t in (a.tickers or "").split(",") if t.strip()]
    cats = [c.strip() for c in (a.categories or "").split(",") if c.strip()]
    vc = memory.version_check()
    j = lambda d: json.dumps(d, default=str)
    short = lambda v: f"n={v.get('n', 0)} avg {v.get('avg_r', 0):+.2f}R win {v.get('win_rate', 0):.0%} · {v.get('evidence', '')[:24]}"
    print(f"MEMORY REVIEW · system V{vc['version']} " + ("✔" if vc["ok"] else "⚠ " + vc["problem"]) +
          f" · learning {L.get('generated', 'never: run desk.py learn')} (data through {L.get('data_through')})")
    rn = L.get("regime_now") or {}
    ff = lambda x, spec: format(x, spec) if isinstance(x, (int, float)) else "n/a"
    print(f"\n[1] REGIME (composite): {rn.get('trend')} · {rn.get('risk')} · {rn.get('volatility')} · VIX {ff(rn.get('vix'), '.1f')} · "
          f"breadth {ff(rn.get('breadth_50d'), '.0%')} · 10Y {ff(rn.get('tnx'), '.2f')}% · leaders {rn.get('leaders')} · Fed {rn.get('fed_signal')}")
    sc = I.get("similar_conditions") or {}
    print("    backtest on the 30 most similar past days (BACKTEST, biased; for confidence only):")
    for k, v in ((sc.get("backtest_on_similar_days") or {}).get("by_setup") or {}).items():
        print(f"      {k}: {short(v)}")
    lab = sc.get("backtest_same_regime_label") or {}
    print(f"    backtest in the same label ({lab.get('label')}):")
    for k, v in (lab.get("by_setup") or {}).items():
        print(f"      {k}: {short(v)}")
    ps = memory.predictions()
    rel = [p for p in ps if not tickers or p.get("ticker") in tickers or p.get("benchmark") in tickers]
    cal = I.get("calibration") or {}
    print(f"\n[2] PREDICTIONS: {cal.get('scored', 0)} scored · directional {(cal.get('accuracy') or {}).get('directional')} · "
          + "; ".join(f"{b['bucket']}: {b['n']}" + (f" {b['flag']}" if b['flag'].startswith(('OVER', 'UNDER')) else "") for b in cal.get("buckets", [])))
    for p in rel[-8:]:
        print(f"    #{p['id']} {p['created'][:10]} {p['statement']} ({p['confidence_pct']:.0f}%) → {p.get('status')}"
              + (f" {'✔' if p.get('correct') else '✘'}" if p.get("status") == "scored" else f" due {p['due']}")
              + (f" · lesson: {p['lesson']}" if p.get("lesson") else ""))
    es = memory.events()
    rel_e = [e for e in es if (cats and e["category"] in cats) or (tickers and set(tickers) & set(e.get("tickers", [])))] \
        if (cats or tickers) else es[-5:]
    print(f"\n[3] SIMILAR EVENTS ({len(rel_e)}):")
    for e in rel_e[-8:]:
        rx = (e.get("reaction") or {})
        print(f"    #{e['id']} {e['date']} [{e['category']}] {e['title'][:70]} · expected: {e['expected_impact'][:80]} · "
              f"{rx.get('verdict', 'pending')} · lesson: {e.get('lesson') or '-'}")
    closed = [t for t in book["trades"] if t["status"] == "closed"]
    print(f"\n[4] SIMILAR TRADES: {len(closed)} closed")
    for t in closed[-6:]:
        print(f"    #{t['id']} {t['ticker']} {t['direction']} {t.get('strategies')} {t.get('regime_trend')} → {t['pnl']:+.0f} ({t['r_multiple']:+.2f}R)")
    perf = L.get("performance") or {}
    for k in ("by_strategies", "by_composite_trend"):
        for g, v in (perf.get(k) or {}).items():
            print(f"    {k[3:]} {g}: {short(v)}")
    snaps = I.get("company_snapshots") or {}
    print("\n[5] COMPANY FILES:" + ("" if tickers else " (pass --tickers to retrieve)"))
    for t in tickers:
        sn = snaps.get(t, {})
        te, fu = sn.get("technical") or {}, sn.get("fundamentals") or {}
        f = lambda x, spec: format(x, spec) if isinstance(x, (int, float)) else "n/a"
        iv = (sn.get("options") or {}).get("atm_iv_30d")
        print(f"    {t}: {te.get('trend')} · vs 50d {f(te.get('vs_50d'), '+.1%')} · RSI {f(te.get('rsi14'), '.0f')} · "
              f"RS 3m vs SPY {f(te.get('rs_3m_vs_spy'), '+.1%')} · support {f(te.get('support_20d_low'), '.2f')} / "
              f"resistance {f(te.get('resistance_20d_high'), '.2f')} · fwd P/E {f(fu.get('forwardPE'), '.1f')} · "
              f"next earnings {fu.get('next_earnings')} · options: {('ATM IV ' + f(iv, '.0%')) if sn.get('options') else 'not analyzed'}")
        for t2 in [x for x in book["trades"] if x["ticker"] == t]:
            print(f"      trade #{t2['id']} {t2['opened']} {t2['status']} {t2.get('pnl', 'open')}")
        for r in [x for x in memory.rejections() if x["ticker"] == t]:
            print(f"      rejected R{r['id']} {r['decided']} ({r['reason']})")
    errs = memory.errors()
    print(f"\n[6] RECENT MISTAKES ({len(errs)}): repeated types {(I.get('errors') or {}).get('repeated_types') or 'none'}")
    for e in errs[-6:]:
        print(f"    E{e['id']} {e['date']} {e['error_type']} ({e['severity']}, {e['status']}): {e['lesson']}")
    pats = I.get("patterns") or []
    today_tags = {rn.get("trend"), rn.get("risk"), rn.get("volatility")}
    rel_p = [p for p in pats if p["pattern"].split(" × ")[-1] in today_tags]
    print(f"\n[7] PATTERNS relevant to today's regime ({len(rel_p)} of {len(pats)}):")
    for p in rel_p[:6]:
        print(f"    {p['pattern']} [{p['source']}] {p['status']} · n={p['n']} avg {p['avg_r']:+.2f}R")
    print("\n[8] EXPERIMENTS:")
    for x in I.get("experiments") or []:
        print(f"    X{x['id']} {x['name']} [{x['status']}]: {x['suggestion']}")
    o = L.get("options") or {}
    print(f"\n[9] OPTIONS: {o.get('closed_option_trades', 0)} closed option trades · " + j({k: o.get(k) for k in ('options_vs_stock', 'iv_vs_realized', 'theta') if o.get(k)}))
    acct = paper.account(book)
    print(f"\n[10] EXPOSURE: equity ${acct['equity']:,.0f} · cash ${acct['cash']:,.0f} · open risk ${acct['open_risk']:,.0f} "
          f"({acct['open_risk'] / acct['equity']:.2%} of 5%) · options ${acct['options_exposure']:,.0f}")
    for t in [x for x in book["trades"] if x["status"] == "open"]:
        print(f"    open #{t['id']} {t['ticker']} {t['direction']} {t['quantity']} @ {t['entry_price']} stop {t['stop']} target {t['target']}")
    print("\nAlso read: Watchlist.md, Improvement Pipeline.md and the last Daily Intelligence self-critique in the vault.")


def cmd_obsidian(a):
    from desk import obsidian

    r = obsidian.sync()
    if r.get("skipped"):
        print("Obsidian sync skipped:", r["skipped"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("--cached", action="store_true")
    p = sub.add_parser("options"); p.add_argument("ticker"); p.add_argument("--dir", default="LONG")
    p.add_argument("--target", type=float); p.add_argument("--stop", type=float); p.add_argument("--days", type=int, default=21)
    p = sub.add_parser("size"); p.add_argument("--entry", type=float, required=True); p.add_argument("--stop", type=float, required=True)
    p.add_argument("--target", type=float, required=True); p.add_argument("--dir", default="LONG")
    p = sub.add_parser("news"); p.add_argument("ticker"); p.add_argument("--days", type=int, default=3)
    p = sub.add_parser("quote"); p.add_argument("tickers", nargs="+")
    p = sub.add_parser("paper"); p.add_argument("action", choices=["status", "update", "stats", "add-stock", "add-option", "close"])
    p.add_argument("ticker", nargs="?"); p.add_argument("direction", nargs="?"); p.add_argument("quantity", nargs="?", type=int)
    p.add_argument("entry", nargs="?", type=float); p.add_argument("stop", nargs="?", type=float); p.add_argument("target", nargs="?", type=float)
    p.add_argument("--id", type=int); p.add_argument("--price", type=float); p.add_argument("--reason", default="")
    p.add_argument("--catalyst", default=""); p.add_argument("--setup", default="discretionary")
    p.add_argument("--regime", default=""); p.add_argument("--confidence", default="medium")
    p.add_argument("--from-last-options", action="store_true")
    p.add_argument("--strategy"); p.add_argument("--catalyst-category"); p.add_argument("--horizon-days", type=int)
    p.add_argument("--scorecard", type=int); p.add_argument("--event-ids"); p.add_argument("--prediction-ids")
    p = sub.add_parser("predict"); p.add_argument("action", choices=["add", "list", "score", "resolve", "update"]); p.add_argument("ticker", nargs="?")
    p.add_argument("--id", type=int); p.add_argument("--kind", default="price", choices=["price", "event"]); p.add_argument("--due")
    p.add_argument("--asset"); p.add_argument("--confidence-pct", type=float); p.add_argument("--supporting")
    p.add_argument("--contradicting"); p.add_argument("--event-id", type=int); p.add_argument("--outcome", choices=["correct", "incorrect"])
    p.add_argument("--actual"); p.add_argument("--actual-magnitude", type=float); p.add_argument("--source"); p.add_argument("--lesson")
    p.add_argument("--direction"); p.add_argument("--days", type=int); p.add_argument("--confidence", default="medium")
    p.add_argument("--reason", default=""); p.add_argument("--catalyst", default=""); p.add_argument("--benchmark")
    p.add_argument("--magnitude", type=float); p.add_argument("--category"); p.add_argument("--strategy")
    p.add_argument("--statement"); p.add_argument("--regime")
    p = sub.add_parser("event"); p.add_argument("action", choices=["add", "update", "list"]); p.add_argument("--id", type=int)
    p.add_argument("--title"); p.add_argument("--date"); p.add_argument("--category"); p.add_argument("--expected", default="")
    p.add_argument("--expected-spy"); p.add_argument("--tickers"); p.add_argument("--sectors")
    p.add_argument("--after-close", action="store_true"); p.add_argument("--source"); p.add_argument("--notes")
    p.add_argument("--lesson"); p.add_argument("--options-impact"); p.add_argument("--time"); p.add_argument("--what-happened")
    p.add_argument("--market-expected"); p.add_argument("--actual-vs-expected"); p.add_argument("--reliability")
    p.add_argument("--interpretation"); p.add_argument("--second-order")
    p = sub.add_parser("reject"); p.add_argument("action", choices=["add", "update", "list"]); p.add_argument("ticker", nargs="?")
    p.add_argument("--id", type=int); p.add_argument("--direction", default="LONG"); p.add_argument("--instrument", default="stock")
    p.add_argument("--entry", type=float); p.add_argument("--stop", type=float); p.add_argument("--target", type=float)
    p.add_argument("--quantity", type=int); p.add_argument("--reason"); p.add_argument("--thesis"); p.add_argument("--catalyst")
    p.add_argument("--alternative"); p.add_argument("--strategy"); p.add_argument("--scorecard", type=int)
    p.add_argument("--event-id", type=int); p.add_argument("--notes"); p.add_argument("--decided"); p.add_argument("--regime")
    p.add_argument("--verdict"); p.add_argument("--lesson")
    p = sub.add_parser("error"); p.add_argument("action", choices=["add", "update", "list"]); p.add_argument("--id", type=int)
    p.add_argument("--subject"); p.add_argument("--type"); p.add_argument("--reasoning"); p.add_argument("--happened")
    p.add_argument("--missed"); p.add_argument("--root-cause"); p.add_argument("--severity", default="low"); p.add_argument("--lesson")
    p.add_argument("--correction"); p.add_argument("--evidence"); p.add_argument("--date"); p.add_argument("--trade-id", type=int)
    p.add_argument("--prediction-id", type=int); p.add_argument("--event-id", type=int); p.add_argument("--source"); p.add_argument("--status")
    p = sub.add_parser("experiment"); p.add_argument("action", choices=["add", "update", "list"]); p.add_argument("--id", type=int)
    p.add_argument("--name"); p.add_argument("--hypothesis"); p.add_argument("--reason"); p.add_argument("--dataset")
    p.add_argument("--baseline"); p.add_argument("--alternative"); p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--decision"); p.add_argument("--evidence"); p.add_argument("--notes")
    p = sub.add_parser("score"); p.add_argument("action", choices=["add", "list"]); p.add_argument("ticker", nargs="?")
    p.add_argument("--direction", default="LONG"); p.add_argument("--strategy"); p.add_argument("--notes")
    for f in ("catalyst", "fundamentals", "technical", "regime", "sector", "options", "liquidity", "risk_reward", "timing", "contradictory"):
        p.add_argument("--" + f.replace("_", "-"), type=float, dest=f)
    p = sub.add_parser("regime"); p.add_argument("--k", type=int, default=10)
    p = sub.add_parser("version"); p.add_argument("action", choices=["show", "check", "new"]); p.add_argument("--version")
    p.add_argument("--name", default=""); p.add_argument("--changes", default=""); p.add_argument("--reason", default="")
    p.add_argument("--evidence", default=""); p.add_argument("--expected", default=""); p.add_argument("--approved-by", default="")
    sub.add_parser("learn")
    p = sub.add_parser("context"); p.add_argument("--tickers"); p.add_argument("--categories")
    p = sub.add_parser("recall"); p.add_argument("ticker")
    sub.add_parser("obsidian")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    {"scan": cmd_scan, "options": cmd_options, "size": cmd_size, "news": cmd_news, "quote": cmd_quote,
     "paper": cmd_paper, "obsidian": cmd_obsidian, "predict": cmd_predict, "event": cmd_event, "version": cmd_version,
     "learn": cmd_learn, "recall": cmd_recall, "context": cmd_context, "reject": cmd_reject, "error": cmd_error,
     "experiment": cmd_experiment, "score": cmd_score, "regime": cmd_regime}[a.cmd](a)
    # keep the Obsidian vault current after anything that changes memory
    if a.cmd in ("scan", "learn") or (a.cmd == "paper" and a.action in ("update", "add-stock", "add-option", "close")) or \
            (a.cmd in ("predict", "event", "version", "reject", "error", "experiment", "score") and
             a.action in ("add", "update", "score", "new", "resolve")):
        from desk import obsidian
        obsidian.safe_sync()


if __name__ == "__main__":
    main()
