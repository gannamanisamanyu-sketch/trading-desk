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
            "horizon_days": a.horizon_days, "system_version": memory.current_version(), **memory.regime_tags(a.regime)}


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
                                             "expected_move_pct", "hv20", "priced")})
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
        p = memory.add_prediction(a.ticker, a.direction, a.days, a.confidence, a.reason, a.catalyst, a.benchmark or "",
                                  a.magnitude, a.category or "", a.strategy or "", a.statement or "", a.regime or "")
        print(f"Prediction #{p['id']} recorded: {p['statement']} · anchored at {p['start_price']:.2f} ({p['start_time']}) · due {p['due']}")
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


def cmd_event(a):
    from desk import memory

    if a.action == "add":
        e = memory.add_event(a.title, a.date, a.category, a.expected, (a.tickers or "").split(","), (a.sectors or "").split(","),
                             a.expected_spy or "", a.after_close, a.source or "", a.notes or "")
        print(f"Event #{e['id']} recorded: {e['date']} [{e['category']}] {e['title']}")
    elif a.action == "update":
        e = memory.update_event(a.id, lesson=a.lesson, options_impact=a.options_impact, notes=a.notes)
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
    """Pre-recommendation briefing from memory (the 12 checks in the playbook)."""
    from desk import learning, memory, paper

    L = learning.load()
    book = paper.load()
    vc = memory.version_check()
    print(f"SYSTEM V{vc['version']} · " + ("parameters match ✔" if vc["ok"] else "⚠ " + vc["problem"] + " " + "; ".join(vc.get("diffs", []))))
    print(f"learning.json: {L.get('generated', 'never run: run desk.py learn')} (data through {L.get('data_through')})")
    acct = paper.account(book)
    print(f"\n[10] EXPOSURE equity ${acct['equity']:,.0f} · cash ${acct['cash']:,.0f} · open risk ${acct['open_risk']:,.0f} "
          f"({acct['open_risk'] / acct['equity']:.2%} of 5%) · options ${acct['options_exposure']:,.0f}")
    tr = book["trades"]
    closed = sorted([x for x in tr if x["status"] == "closed"], key=lambda x: x.get("closed", ""))
    print("[1] RECENT TRADES:", *[f"  #{x['id']} {x['ticker']} {x['direction']} {x['status']} {x.get('pnl', '')}" for x in tr[-8:]] or ["  none"], sep="\n")
    print("[2] RECENT LOSERS:", *[f"  #{x['id']} {x['ticker']} {x['pnl']:+.0f} ({x['r_multiple']:+.2f}R) {x.get('exit_reason')} · {x.get('setup')}" for x in closed if x["pnl"] <= 0][-5:] or ["  none"], sep="\n")
    print("[3] RECENT WINNERS:", *[f"  #{x['id']} {x['ticker']} {x['pnl']:+.0f} ({x['r_multiple']:+.2f}R) {x.get('exit_reason')} · {x.get('setup')}" for x in closed if x["pnl"] > 0][-5:] or ["  none"], sep="\n")
    perf = L.get("performance", {})
    print("[4] STRATEGY EVIDENCE (closed paper trades):")
    for k, v in (perf.get("by_strategies") or {}).items():
        print(f"  {k}: n={v['n']} win {v.get('win_rate', 0):.0%} avgR {v.get('avg_r', 0):+.2f} · {v['evidence']}")
    if not perf.get("by_strategies"):
        print("  no closed trades yet")
    ft = L.get("forward_test", {})
    print(f"    screener forward test: {ft.get('signals', 0)} signals, {ft.get('resolved', 0)} resolved")
    for k, v in (ft.get("by_setup") or {}).items():
        print(f"    {k}: n={v['n']} win {v.get('win_rate', 0):.0%} avgR {v.get('avg_r', 0):+.2f} · {v['evidence']}")
    tags = memory.regime_tags()
    print(f"[5] CURRENT REGIME (rule, latest scan {tags['regime_source']}): {tags['regime_rule']} → {tags['regime_trend']} · {tags['regime_vol']} · {tags['regime_risk']}")
    print("[6] HISTORY IN THIS REGIME:")
    print("   trades:", json.dumps({k: {kk: v.get(kk) for kk in ("n", "win_rate", "avg_r", "evidence")} for k, v in (perf.get("by_regime_trend") or {}).items()}, default=str))
    print("   signals:", json.dumps({k: {kk: v.get(kk) for kk in ("n", "avg_r", "evidence")} for k, v in (ft.get("by_regime") or {}).items()}, default=str))
    reg = (L.get("regimes") or {}).get("by_regime", {}).get(tags["regime_rule"] or "", {})
    print("   SPY after this regime label:", json.dumps({k: reg.get(k) for k in ("days", "spy_fwd_5d_avg", "n_5d", "best_sectors_5d_vs_spy", "evidence")}, default=str))
    print("[7] PAST EVENTS BY CATEGORY:", json.dumps(L.get("events") or "none", default=str))
    o = L.get("options", {})
    print(f"[8/9] OPTIONS LEARNING: {o.get('closed_option_trades', 0)} closed option trades")
    for k in ("by_structure", "by_dte_bucket", "by_delta_bucket"):
        for kk, v in (o.get(k) or {}).items():
            print(f"   {k[3:]} {kk}: n={v['n']} avgR {v.get('avg_r', 0):+.2f} · {v['evidence']}")
    for k in ("options_vs_stock", "iv_vs_realized", "theta"):
        if o.get(k):
            print(f"   {k}: {json.dumps(o[k], default=str)}")
    pr = L.get("predictions", {})
    print(f"[12] PREDICTIONS: {pr.get('scored', 0)} scored / {pr.get('total', 0)} total · accuracy "
          f"{(pr.get('overall') or {}).get('accuracy', 'n/a')} · {(pr.get('overall') or {}).get('evidence', '')}")
    for p in [p for p in memory.predictions() if p.get("status") != "scored"]:
        print(f"   open #{p['id']} {p['statement']} (due {p['due']}, mark {p.get('mark_return')})")
    print("[11] WATCHLIST, lessons and running experiments: read the Obsidian notes "
          "03 - Portfolio/Watchlist.md and 09 - Strategy Research/Improvement Pipeline.md")


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
    p = sub.add_parser("predict"); p.add_argument("action", choices=["add", "list", "score"]); p.add_argument("ticker", nargs="?")
    p.add_argument("--direction"); p.add_argument("--days", type=int); p.add_argument("--confidence", default="medium")
    p.add_argument("--reason", default=""); p.add_argument("--catalyst", default=""); p.add_argument("--benchmark")
    p.add_argument("--magnitude", type=float); p.add_argument("--category"); p.add_argument("--strategy")
    p.add_argument("--statement"); p.add_argument("--regime")
    p = sub.add_parser("event"); p.add_argument("action", choices=["add", "update", "list"]); p.add_argument("--id", type=int)
    p.add_argument("--title"); p.add_argument("--date"); p.add_argument("--category"); p.add_argument("--expected", default="")
    p.add_argument("--expected-spy"); p.add_argument("--tickers"); p.add_argument("--sectors")
    p.add_argument("--after-close", action="store_true"); p.add_argument("--source"); p.add_argument("--notes")
    p.add_argument("--lesson"); p.add_argument("--options-impact")
    p = sub.add_parser("version"); p.add_argument("action", choices=["show", "check", "new"]); p.add_argument("--version")
    p.add_argument("--name", default=""); p.add_argument("--changes", default=""); p.add_argument("--reason", default="")
    p.add_argument("--evidence", default=""); p.add_argument("--expected", default=""); p.add_argument("--approved-by", default="")
    sub.add_parser("learn"); sub.add_parser("context")
    p = sub.add_parser("recall"); p.add_argument("ticker")
    sub.add_parser("obsidian")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    {"scan": cmd_scan, "options": cmd_options, "size": cmd_size, "news": cmd_news, "quote": cmd_quote,
     "paper": cmd_paper, "obsidian": cmd_obsidian, "predict": cmd_predict, "event": cmd_event, "version": cmd_version,
     "learn": cmd_learn, "recall": cmd_recall, "context": cmd_context}[a.cmd](a)
    # keep the Obsidian vault current after anything that changes memory
    if a.cmd in ("scan", "learn") or (a.cmd == "paper" and a.action in ("update", "add-stock", "add-option", "close")) or \
            (a.cmd in ("predict", "event", "version") and a.action in ("add", "update", "score", "new")):
        from desk import obsidian
        obsidian.safe_sync()


if __name__ == "__main__":
    main()
