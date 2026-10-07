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
    r = out["regime"]
    print(f"\nScan complete ({out['runtime_s']}s) · {out['generated']} · session: {out['session']}")
    print(f"Regime: {r.get('regime')} · Fed proxy: {out['fed_proxy'].get('signal')}")
    if out["data_gaps"]:
        print("Data gaps:", *out["data_gaps"], sep="\n  - ")
    s = out["setups"]
    if len(s):
        print(s[["ticker", "direction", "setups", "entry", "stop", "target", "reward_risk", "shares", "risk_$", "score"]]
              .head(10).round(2).to_string(index=False))
    print("\nPacket: reports/" + out["generated"][:10] + "_scan.md")


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
            "contracts": sz.quantity, "priced": stamp()}
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
                            a.setup, a.regime, a.confidence, risk_d, abs(a.target - a.entry) / a.entry)
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
        t = paper.add_trade(last["ticker"], "option", last["direction"], qty, last["debit"], last["stop"], last["target"],
                            a.reason, a.catalyst, a.setup, a.regime, a.confidence, qty * last["max_loss"],
                            last["pnl_at_target"] / last["max_loss"], option=opt)
        print("Recorded paper options trade", json.dumps(t, indent=1))
    elif a.action == "close":
        book = paper.load()
        t = next(x for x in book["trades"] if x["id"] == a.id and x["status"] == "open")
        paper.close_trade(book, t, a.price, a.reason)
        paper.save(book)
        print(f"Closed #{a.id}: P&L ${t['pnl']:,.2f} ({t['return_pct']:.1%})")


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
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    {"scan": cmd_scan, "options": cmd_options, "size": cmd_size, "news": cmd_news, "quote": cmd_quote,
     "paper": cmd_paper}[a.cmd](a)


if __name__ == "__main__":
    main()
