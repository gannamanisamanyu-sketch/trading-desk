"""Web pages for the trading desk (Streamlit). Reads the repo's reports/ and journal/.

Research and paper trading only: nothing here can place an order.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from desk.common import JOURNAL, REPORTS, config, market_session, stamp

BLUE, GREY, GREEN, RED = "#2563eb", "#9ca3af", "#16a34a", "#dc2626"
DECISIONS = {"AGGRESSIVE BUY": "🟢", "MODERATE BUY": "🟢", "SELECTIVE TRADES ONLY": "🟡", "HOLD CASH": "⚪", "RISK-OFF": "🔴"}


def banner() -> None:
    st.caption("⚠️ **Research and paper trading only.** $30,000 simulated account. Not financial advice; no brokerage "
               "connection. Quotes from Yahoo Finance are delayed.")


def md(text: str) -> str:
    """Escape $ so Streamlit doesn't render text between two dollar signs as a LaTeX formula."""
    return text.replace("$", "\\$")


def money(x) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"${x:,.2f}"


def files(pattern: str):
    return sorted(REPORTS.glob(pattern), reverse=True)


@st.cache_data(ttl=120)
def live_prices(tickers: tuple) -> dict:
    import yfinance as yf

    out = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="1d", interval="1m", prepost=True)
            if h.empty:
                h = yf.Ticker(t).history(period="5d")
            out[t] = (float(h["Close"].iloc[-1]), stamp(h.index[-1].to_pydatetime()))
        except Exception:
            out[t] = (np.nan, "unavailable")
    return out


def load_book() -> dict:
    p = JOURNAL / "ledger.json"
    return json.loads(p.read_text()) if p.exists() else {"starting_cash": 30000, "trades": []}


# ------------------------------------------------------------------ Today
def today_page() -> None:
    st.title("📋 Today's desk report")
    banner()
    reports = files("*_report.md")
    if not reports:
        st.info("No reports yet. Open a Claude session in the trading-desk folder and ask “What's the trade today?”")
        return
    pick = st.selectbox("Report", reports, format_func=lambda p: p.name.replace("_report.md", ""), index=0)
    text = pick.read_text()
    m = re.search(r"FINAL DECISION:\s*\**([A-Z /-]+?)\**\s*$", text, re.M)
    decision = m.group(1).strip() if m else None
    rg = re.search(r"MARKET REGIME:\s*\**([A-Z /]+?)\**[\s(]", text)
    st.markdown(f"### {DECISIONS.get(decision, '•')} {decision or 'Decision not found'}")
    st.caption(f"Market regime: **{rg.group(1).strip() if rg else 'n/a'}** · market session now: **{market_session()}**")
    best = re.search(r"BEST TRADE OF THE DAY(.*?)━━", text, re.S)
    if best:
        with st.container(border=True):
            st.markdown("**Best trade of the day**")
            st.markdown(md(best.group(1).strip()))
    with st.expander("Full report", expanded=False):
        st.markdown(md(text.replace("━━━━━━━━━━━━━━━━━━━━━━", "\n---\n")))


# ------------------------------------------------------------------ Portfolio
def portfolio_page() -> None:
    from desk import paper

    st.title("💼 Paper portfolio")
    banner()
    book = load_book()
    trades = book["trades"]
    open_t = [t for t in trades if t["status"] == "open"]
    prices = live_prices(tuple(sorted({t["ticker"] for t in open_t if t["instrument"] == "stock"})))
    marks = {t["id"]: prices[t["ticker"]][0] for t in open_t if t["instrument"] == "stock" and np.isfinite(prices[t["ticker"]][0])}
    acct = paper.account(book, marks)
    start = acct["starting_cash"]
    c = st.columns(4)
    c[0].metric("Equity (live)", money(acct["equity"]), f"{acct['total_return']:+.2%}", border=True)
    c[1].metric("Cash", money(acct["cash"]), f"{acct['cash'] / acct['equity']:.0%} of equity", delta_color="off", border=True)
    c[2].metric("Open risk", money(acct["open_risk"]), f"{acct['open_risk'] / acct['equity']:.2%} (limit 5%)", delta_color="off", border=True)
    c[3].metric("Options exposure", money(acct["options_exposure"]), "limit 15%", delta_color="off", border=True)

    st.subheader("Open positions")
    if open_t:
        rows = []
        for t in open_t:
            px, when = prices.get(t["ticker"], (t.get("last_mark", np.nan), "last saved mark"))
            if t["instrument"] != "stock":
                px, when = t.get("last_mark"), "last desk update"
            mult = 100 if t["instrument"] == "option" else 1
            pnl = ((px - t["entry_price"]) if t["direction"] == "LONG" or t["instrument"] == "option" else (t["entry_price"] - px)) * t["quantity"] * mult
            dist_stop = (px / t["stop"] - 1) if t["instrument"] == "stock" else np.nan
            rows.append({"#": t["id"], "ticker": t["ticker"], "type": t["instrument"], "side": t["direction"], "qty": t["quantity"],
                         "entry": t["entry_price"], "price": px, "price time": when, "stop": t["stop"], "target": t["target"],
                         "P&L $": pnl, "P&L %": pnl / t["cost"], "to stop": dist_stop, "risk $": t["risk"], "opened": t["opened"],
                         "setup": t["setup"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
            "entry": st.column_config.NumberColumn(format="$%.2f"), "price": st.column_config.NumberColumn(format="$%.2f"),
            "stop": st.column_config.NumberColumn(format="$%.2f"), "target": st.column_config.NumberColumn(format="$%.2f"),
            "P&L $": st.column_config.NumberColumn(format="$%.2f"), "P&L %": st.column_config.NumberColumn(format="percent"),
            "to stop": st.column_config.NumberColumn(format="percent"), "risk $": st.column_config.NumberColumn(format="$%.0f")})
        for t in open_t:
            with st.expander(f"#{t['id']} {t['ticker']}: thesis"):
                st.markdown(md(f"**Reason:** {t['reason']}\n\n**Catalyst:** {t['catalyst']}\n\n"
                               f"**Regime at entry:** {t['regime']} · **Confidence:** {t['confidence']}"))
    else:
        st.caption("No open positions. Cash is a position.")

    eq_p = JOURNAL / "equity_history.csv"
    if eq_p.exists():
        eq = pd.read_csv(eq_p, parse_dates=["date"])
        if len(eq):
            fig = go.Figure(go.Scatter(x=eq["date"], y=eq["equity"], mode="lines+markers", line=dict(color=BLUE)))
            fig.add_hline(y=start, line_dash="dash", line_color=GREY)
            fig.update_layout(title="Equity (end of each desk update)", height=300, margin=dict(l=10, r=10, t=50, b=10),
                              yaxis_tickprefix="$")
            st.plotly_chart(fig)

    st.subheader("Closed trades")
    closed = [t for t in trades if t["status"] == "closed"]
    if closed:
        st.dataframe(pd.DataFrame(closed)[["id", "ticker", "instrument", "direction", "quantity", "entry_price", "exit_price",
                                           "pnl", "return_pct", "r_multiple", "holding_days", "exit_reason", "setup", "regime"]],
                     hide_index=True)
    else:
        st.caption("No closed trades yet.")
    st.subheader("Performance statistics")
    stats = paper.statistics(book)
    if stats.get("closed_trades", 0) == 0:
        st.caption(f"Statistics start once trades close. Reviews are flagged every 20 closed trades "
                   f"({stats.get('open_trades', 0)} open now).")
    else:
        c = st.columns(5)
        c[0].metric("Closed trades", stats["closed_trades"], border=True)
        c[1].metric("Win rate", f"{stats['win_rate']:.0%}", border=True)
        c[2].metric("Profit factor", f"{stats['profit_factor']:.2f}", border=True)
        c[3].metric("Avg R multiple", f"{stats['avg_r_multiple']:.2f}", border=True)
        c[4].metric("EV / trade", money(stats["expected_value_per_trade"]), border=True)
        st.caption(stats.get("milestone", ""))
        for k in ("by_setup", "by_regime", "by_instrument"):
            if k in stats:
                st.markdown(f"**{k.replace('_', ' ').title()}**")
                st.dataframe(pd.DataFrame(stats[k]).T)


# ------------------------------------------------------------------ Scan
def scan_page() -> None:
    st.title("🛰️ Market scan")
    banner()
    scans = files("*_scan*.md")
    if not scans:
        st.info("No scans yet. The GitHub Action runs one every weekday at 8:00 ET.")
        return
    pick = st.selectbox("Scan", scans, format_func=lambda p: p.stem.replace("_scan", " ").replace("_", " "), index=0)
    st.markdown(md(pick.read_text()))


# ------------------------------------------------------------------ Options lab
def options_page() -> None:
    from desk import options as O
    from desk import paper, risk

    st.title("🧪 Options lab")
    banner()
    st.markdown("Enter a directional thesis. The engine prices the chain with its own IV and Greeks, ranks long options "
                "and debit spreads by payoff at target vs. maximum loss, and sizes the best one against the 1% rule.")
    with st.form("opt"):
        c = st.columns(5)
        tk = c[0].text_input("Ticker", "NVDA").upper().strip()
        direction = c[1].selectbox("Direction", ["LONG", "SHORT"])
        target = c[2].number_input("Target price", value=0.0, step=1.0)
        stop = c[3].number_input("Stop price", value=0.0, step=1.0)
        days = c[4].number_input("Holding days", value=21, min_value=3, max_value=90)
        go_ = st.form_submit_button("Analyze", type="primary")
    if not go_:
        return
    if target <= 0 or stop <= 0:
        st.error("Enter both a target and a stop.")
        return
    with st.spinner(f"Loading {tk} option chain…"):
        try:
            ch = O.load_chain(tk)
        except Exception as exc:
            st.error(f"Could not load the chain: {exc}")
            return
    if ch.get("quote_quality", "").startswith("STALE"):
        st.warning("STALE option prices (market closed): treat the results as planning estimates only.")
    c = st.columns(4)
    c[0].metric("Spot", f"{ch['spot']:.2f}", ch.get("spot_time"), delta_color="off", border=True)
    c[1].metric("ATM 30-day IV", f"{(ch.get('atm_iv_30d') or float('nan')):.1%}", border=True)
    c[2].metric("20-day realized vol", f"{ch['hv20']:.1%}", border=True)
    c[3].metric("IV rank", "n/a" if ch["iv_rank"].get("iv_rank") is None else f"{ch['iv_rank']['iv_rank']:.0%}",
                ch["iv_rank"].get("note"), delta_color="off", border=True)
    if ch.get("term") is not None and len(ch["term"]):
        st.markdown("**Expected move by expiry (ATM straddle)**")
        st.dataframe(ch["term"], hide_index=True, column_config={
            "atm_iv": st.column_config.NumberColumn(format="percent"),
            "expected_move_pct": st.column_config.NumberColumn(format="percent")})
    acct = paper.account(load_book())
    budget = config()["risk"]["max_risk_per_trade"] * acct["equity"]
    res = O.evaluate_structures(ch, direction, target, stop, int(days), budget)
    if res.empty:
        st.info("No liquid candidates for this thesis.")
        return
    best = res.iloc[0]
    sz = risk.size_option(acct["equity"], best["max_loss"], best["pnl_at_target"], acct["open_risk"], acct["options_exposure"])
    with st.container(border=True):
        st.markdown(md(f"**Best: {best['structure']} {best['expiry']} {best['long_strike']:g}"
                    f"{'/' + format(best['short_strike'], 'g') if pd.notna(best['short_strike']) else ''}**: debit "
                    f"${best['debit']:.2f} · {sz.quantity} contract(s) · max loss ${sz.risk:,.0f} ({sz.risk_pct:.2%}) · "
                    f"profit at target ${sz.potential_profit:,.0f} · PoP {best['pop']:.0%}"))
        for n in sz.notes:
            st.warning(md(n))
        for n in O.explain_choice(best, res, ch, ch["spot"], stop, target):
            st.markdown(md(f"- {n}"))
    st.dataframe(res[["structure", "expiry", "dte", "long_strike", "short_strike", "debit", "max_loss", "max_profit",
                      "breakeven", "pop", "pnl_at_target", "pnl_at_stop", "rr_at_target", "delta", "theta_day", "iv",
                      "contracts_for_budget", "quality", "score"]].head(12), hide_index=True,
                 column_config={"pop": st.column_config.NumberColumn(format="percent"),
                                "iv": st.column_config.NumberColumn(format="percent")})


# ------------------------------------------------------------------ Sizer
def sizer_page() -> None:
    from desk import paper, risk

    st.title("📏 Position sizer")
    banner()
    acct = paper.account(load_book())
    st.caption(f"Paper equity {money(acct['equity'])} · open risk {money(acct['open_risk'])}. Limits: 1% per trade, "
               "5% total, 15% per position.")
    c = st.columns(4)
    direction = c[0].selectbox("Direction", ["LONG", "SHORT"])
    entry = c[1].number_input("Entry", value=100.0, step=0.5)
    stop = c[2].number_input("Stop", value=95.0, step=0.5)
    target = c[3].number_input("Target", value=112.0, step=0.5)
    sz = risk.size_stock(acct["equity"], entry, stop, target, acct["open_risk"], direction)
    c = st.columns(4)
    c[0].metric("Shares", sz.quantity, "OK" if sz.ok else "REJECTED", delta_color="normal" if sz.ok else "inverse", border=True)
    c[1].metric("Invested", money(sz.invested), f"{sz.invested / acct['equity']:.1%} of equity", delta_color="off", border=True)
    c[2].metric("Max loss at stop", money(sz.risk), f"{sz.risk_pct:.2%}", delta_color="off", border=True)
    c[3].metric("Reward / risk", f"{sz.reward_risk:.1f} : 1", money(sz.potential_profit) + " at target", delta_color="off", border=True)
    st.caption(f"Binding limit: **{sz.binding_limit}**" + ("" if sz.ok else f" · {'; '.join(sz.notes)}"))
