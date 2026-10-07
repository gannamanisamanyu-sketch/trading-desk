"""Learning engine: measure what actually happened and how much evidence there is.

Run at the end of each trading day (`desk.py learn`). It
  1. scores predictions whose horizon has passed (vs. the price anchored when they were made),
  2. measures the market reaction to recorded news events (1-day and 5-day),
  3. computes MFE / MAE and realized volatility for every paper trade,
  4. forward-tests every screener signal ever logged (hypothetical: next open, stop/target, 20-day timeout),
  5. tracks how each market regime was followed by SPY and the sectors,
  6. aggregates performance by strategy, regime, catalyst, sector, instrument, options structure and version,
     each with an EVIDENCE LEVEL so small samples are never mistaken for proof.

Nothing is typed in by hand: every number comes from the ledger, the journal files or Yahoo daily bars.
Results go to journal/learning.json (and the Obsidian mirror).
"""
from __future__ import annotations

import json
import math
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from desk import memory
from desk.common import DATA, JOURNAL, config, now_ny, universe

LEARNING = JOURNAL / "learning.json"
TIMEOUT_DAYS = 20          # forward-test holding limit (trading days)
MIN_PRELIM, MIN_ESTABLISHED = 20, 50


# ------------------------------------------------------------------ evidence
def evidence(n: int, ci_low: Optional[float] = None, ci_high: Optional[float] = None, robust: bool = True) -> str:
    if n < MIN_PRELIM:
        return f"INSUFFICIENT (n={n} < {MIN_PRELIM}): no conclusions"
    if n < MIN_ESTABLISHED:
        return f"PRELIMINARY (n={n}): hypotheses only"
    if ci_low is not None and ci_low > 0 and robust:
        return f"ESTABLISHED POSITIVE (n={n}, 95% CI of mean R above 0, robustness checks passed)"
    if ci_high is not None and ci_high < 0:
        return f"ESTABLISHED NEGATIVE (n={n}, 95% CI of mean R below 0)"
    return f"NOT PROVEN (n={n}): CI includes 0 or robustness checks failed"


def _ci(x: pd.Series):
    x = x.dropna()
    if len(x) < 2 or x.std() == 0:
        return None, None
    from scipy import stats

    h = stats.t.ppf(0.975, len(x) - 1) * x.std() / math.sqrt(len(x))
    return float(x.mean() - h), float(x.mean() + h)


def perf(df: pd.DataFrame, start_cash: float = 30000.0) -> Dict[str, object]:
    """Stats for a set of resolved outcomes. Needs column r (R multiple); pnl, holding_days, ticker, month,
    regime_trend and close_date are used when present."""
    df = df.dropna(subset=["r"])
    n = len(df)
    if n == 0:
        return {"n": 0, "evidence": evidence(0)}
    r = df["r"]
    wins, losses = df[r > 0], df[r <= 0]
    lo, hi = _ci(r)
    out = {"n": n, "win_rate": float((r > 0).mean()), "avg_r": float(r.mean()), "median_r": float(r.median()),
           "std_r": float(r.std()) if n > 1 else None, "ci95_mean_r": [lo, hi],
           "risk_adjusted": float(r.mean() / r.std()) if n > 1 and r.std() > 0 else None,
           "avg_win_r": float(wins["r"].mean()) if len(wins) else None,
           "avg_loss_r": float(losses["r"].mean()) if len(losses) else None,
           "profit_factor": float(wins["r"].sum() / -losses["r"].sum()) if len(losses) and losses["r"].sum() < 0 else None}
    if "pnl" in df and df["pnl"].notna().any():
        p = df["pnl"]
        cum = p.loc[df.sort_values("close_date").index].cumsum() if "close_date" in df else p.cumsum()
        out.update({"avg_win": float(p[p > 0].mean()) if (p > 0).any() else None,
                    "avg_loss": float(p[p <= 0].mean()) if (p <= 0).any() else None,
                    "expected_value": float(p.mean()), "total_pnl": float(p.sum()),
                    "return_on_start": float(p.sum() / start_cash),
                    "max_drawdown": float((cum - cum.cummax().clip(lower=0)).min()),
                    "profit_factor_usd": float(p[p > 0].sum() / -p[p <= 0].sum()) if (p <= 0).any() and p[p <= 0].sum() < 0 else None})
    if "holding_days" in df:
        out["avg_holding_days"] = float(df["holding_days"].mean())
    tickers = df["ticker"].nunique() if "ticker" in df else 0
    regimes = df["regime_trend"].nunique() if "regime_trend" in df else 0
    months = df["month"].nunique() if "month" in df else 0
    ex_best = float(r.drop(r.idxmax()).mean()) if n > 2 else None
    top_ticker_share = float(df["ticker"].value_counts(normalize=True).iloc[0]) if tickers else None
    robust = tickers >= 10 and regimes >= 2 and months >= 2 and (ex_best or 0) > 0 and (top_ticker_share or 1) <= 0.2
    out.update({"distinct_tickers": int(tickers), "regimes_covered": int(regimes), "months_covered": int(months),
                "avg_r_ex_best": ex_best, "top_ticker_share": top_ticker_share, "robust": bool(robust),
                "evidence": evidence(n, lo, hi, robust)})
    return out


def group_perf(df: pd.DataFrame, col: str) -> Dict[str, Dict]:
    if df.empty or col not in df:
        return {}
    d = df.explode(col) if df[col].map(lambda v: isinstance(v, list)).any() else df
    return {str(k): perf(g) for k, g in d.groupby(col, dropna=True)}


# ------------------------------------------------------------------ prices
def _prices(tickers: Iterable[str], since: str) -> Dict[str, pd.DataFrame]:
    from desk.market import download_daily

    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    cache = DATA / "universe_daily.pkl"
    have: Dict[str, pd.DataFrame] = {}
    if cache.exists() and pd.Timestamp(cache.stat().st_mtime, unit="s").date() == now_ny().date():
        have = pd.read_pickle(cache)
    need = [t for t in tickers if not have or t not in have["close"]]
    days = (pd.Timestamp(now_ny().date()) - pd.Timestamp(since)).days + 40
    period = "3mo" if days < 85 else "6mo" if days < 175 else "1y" if days < 360 else "2y" if days < 720 else "5y"
    if need:
        new = download_daily(need, period)
        have = new if not have else {f: pd.concat([have[f], new[f]], axis=1) for f in ("open", "high", "low", "close", "volume")}
    return {f: have[f].loc[:, ~have[f].columns.duplicated()] for f in ("open", "high", "low", "close")}


def _bar_after(px: pd.DataFrame, t: str, day, inclusive: bool) -> Optional[pd.Timestamp]:
    s = px[t].dropna() if t in px else pd.Series(dtype=float)
    idx = s.index[s.index >= pd.Timestamp(day)] if inclusive else s.index[s.index > pd.Timestamp(day)]
    return idx[0] if len(idx) else None


# ------------------------------------------------------------------ 1. predictions
def score_predictions(P: Dict[str, pd.DataFrame]) -> List[Dict]:
    ps = memory.predictions()
    c = P.get("close", pd.DataFrame())
    for p in ps:
        if p.get("status") == "scored" or p["ticker"] not in c:
            continue
        s = c[p["ticker"]].dropna()
        bar = _bar_after(c, p["ticker"], p["due"], inclusive=True)
        if bar is None:
            p["status"] = "pending"
            p["mark_return"] = float(s.iloc[-1] / p["start_price"] - 1) if len(s) else None
            continue
        ret = float(s.loc[bar] / p["start_price"] - 1)
        bench = None
        if p.get("benchmark") and p["benchmark"] in c and p.get("bench_start"):
            bench = float(c[p["benchmark"]].dropna().loc[bar] / p["bench_start"] - 1)
        rel = ret - bench if bench is not None else None
        d = p["direction"]
        val = {"UP": ret, "DOWN": -ret, "OUTPERFORM": rel, "UNDERPERFORM": -rel if rel is not None else None}[d]
        if val is None:
            continue
        p.update({"status": "scored", "scored_on": str(bar.date()), "end_price": float(s.loc[bar]),
                  "actual_return": ret, "benchmark_return": bench, "relative_return": rel,
                  "correct": bool(val > 0), "magnitude_met": bool(val >= p["magnitude"]) if p.get("magnitude") else None})
    memory.save_predictions(ps)
    return ps


def prediction_stats(ps: List[Dict]) -> Dict[str, object]:
    df = pd.DataFrame([p for p in ps if p.get("status") == "scored"])
    out = {"total": len(ps), "scored": len(df), "pending": sum(p.get("status") != "scored" for p in ps)}
    if df.empty:
        return out

    def acc(g):
        n = len(g)
        hit = float(g["correct"].mean())
        implied = float(g["confidence"].map(memory.CONFIDENCE_PROB).mean())
        brier = float(((g["confidence"].map(memory.CONFIDENCE_PROB) - g["correct"].astype(float)) ** 2).mean())
        se = math.sqrt(hit * (1 - hit) / n) if n else 0
        return {"n": n, "accuracy": hit, "ci95": [max(0, hit - 1.96 * se), min(1, hit + 1.96 * se)],
                "implied_by_confidence": implied, "brier": brier, "evidence": evidence(n)}

    out["overall"] = acc(df)
    for col in ("confidence", "category", "strategy", "direction", "ticker", "regime_trend", "system_version"):
        if col in df:
            out[f"by_{col}"] = {str(k): acc(g) for k, g in df.groupby(col)}
    return out


# ------------------------------------------------------------------ 2. events
def score_events(P: Dict[str, pd.DataFrame]) -> List[Dict]:
    es = memory.events()
    c = P.get("close", pd.DataFrame())
    for e in es:
        names = ["SPY"] + e.get("tickers", []) + e.get("sectors", [])
        react = _bar_after(c, "SPY", e["date"], inclusive=not e.get("after_close"))
        if react is None:
            e["reaction"] = {"status": "pending"}
            continue
        res = {}
        for t in names:
            if t not in c:
                continue
            s = c[t].dropna()
            prior = s.index[s.index < react]
            if not len(prior) or react not in s.index:
                continue
            base = s.loc[prior[-1]]
            pos = s.index.get_loc(react)
            res[t] = {"1d": float(s.loc[react] / base - 1),
                      "5d": float(s.iloc[pos + 4] / base - 1) if pos + 4 < len(s) else None}
        spy1 = res.get("SPY", {}).get("1d")
        hit = None
        if e.get("expected_spy") and spy1 is not None:
            hit = {"UP": spy1 > 0.003, "DOWN": spy1 < -0.003, "FLAT": abs(spy1) <= 0.003}[e["expected_spy"]]
        e["reaction"] = {"status": "measured" if all(v.get("5d") is not None for v in res.values()) else "partial",
                         "reaction_day": str(react.date()), "returns": res, "spy_expectation_correct": hit}
    memory.save_events(es)
    return es


def event_stats(es: List[Dict]) -> Dict[str, Dict]:
    rows = []
    for e in es:
        rx = (e.get("reaction") or {}).get("returns") or {}
        if "SPY" in rx:
            rows.append({"category": e["category"], "spy_1d": rx["SPY"]["1d"], "spy_5d": rx["SPY"].get("5d"),
                         "hit": (e.get("reaction") or {}).get("spy_expectation_correct")})
    df = pd.DataFrame(rows)
    if df.empty:
        return {}
    out = {}
    for k, g in df.groupby("category"):
        h = g["hit"].dropna()
        out[k] = {"n": len(g), "avg_spy_1d": float(g["spy_1d"].mean()), "avg_abs_spy_1d": float(g["spy_1d"].abs().mean()),
                  "avg_spy_5d": float(g["spy_5d"].dropna().mean()) if g["spy_5d"].notna().any() else None,
                  "expectation_hit_rate": float(h.mean()) if len(h) else None, "evidence": evidence(len(g))}
    return out


# ------------------------------------------------------------------ 3. trades
def trade_frame(book: Dict, P: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    uni = universe().set_index("ticker")
    c, h, l = P.get("close", pd.DataFrame()), P.get("high", pd.DataFrame()), P.get("low", pd.DataFrame())
    rows = []
    for t in book["trades"]:
        opt = t.get("option") or {}
        und = opt.get("underlying") or t["ticker"]
        sign = -1 if t["direction"] == "SHORT" else 1
        u_entry = t.get("underlying_entry") if t["instrument"] == "option" else t["entry_price"]
        x = {"id": t["id"], "ticker": t["ticker"], "status": t["status"], "instrument": t["instrument"],
             "direction": t["direction"], "setup": t.get("setup"), "strategies": t.get("strategies") or
             [memory.SETUP_TO_STRATEGY.get(t.get("setup"), "Unclassified")],
             "catalyst_category": t.get("catalyst_category") or "Unrecorded",
             "regime_trend": t.get("regime_trend") or memory.TREND_MAP.get((t.get("regime") or "").upper(), "Unrecorded"),
             "regime_vol": t.get("regime_vol") or "Unrecorded", "regime_risk": t.get("regime_risk") or "Unrecorded",
             "system_version": t.get("system_version", "1.0"), "sector": uni["sector"].get(und, "Other"),
             "structure": opt.get("structure") or "Stock", "opened": t["opened"], "close_date": t.get("closed"),
             "month": (t.get("closed") or t["opened"])[:7], "pnl": t.get("pnl"), "r": t.get("r_multiple"),
             "holding_days": t.get("holding_days"), "risk": t["risk"]}
        if und in c and u_entry:
            start = pd.Timestamp(t["opened"][:10])
            end = pd.Timestamp(t["closed"][:10]) if t.get("closed") else c.index[-1]
            win = c.index[(c.index > start) & (c.index <= end)]       # from the day after entry (stops are too)
            if len(win):
                hi, lo = h.loc[win, und].max(), l.loc[win, und].min()
                fav = (hi - u_entry) if sign > 0 else (u_entry - lo)
                adv = (u_entry - lo) if sign > 0 else (hi - u_entry)
                ref = abs(u_entry - t["stop"]) or np.nan
                x.update({"mfe_pct": float(fav / u_entry), "mae_pct": float(adv / u_entry),
                          "mfe_r": float(fav / ref), "mae_r": float(adv / ref)})
                cl = c.loc[(c.index >= start) & (c.index <= end), und].dropna()
                if len(cl) > 5:
                    x["realized_vol"] = float(cl.pct_change().std() * math.sqrt(252))
                if t["instrument"] == "option" and t.get("closed"):
                    u_exit = float(c.loc[win[-1], und])
                    x["underlying_exit"] = u_exit
                    x["stock_counterfactual_r"] = float(sign * (u_exit - u_entry) / ref)
            x["underlying_last"] = float(c[und].dropna().iloc[-1])
        if t["instrument"] == "option":
            x.update({k: opt.get(k) for k in ("dte", "long_delta", "iv", "iv_rank", "theta_day", "otm_pct", "expected_move_pct")})
        rows.append(x)
    return pd.DataFrame(rows)


def options_stats(tf: pd.DataFrame) -> Dict[str, object]:
    o = tf[(tf["instrument"] == "option") & (tf["status"] == "closed")] if len(tf) else tf
    out: Dict[str, object] = {"closed_option_trades": int(len(o))}
    if o.empty:
        return out
    o = o.copy()
    o["dte_bucket"] = pd.cut(o["dte"], [0, 14, 30, 60, 120, 9999], labels=["≤14", "15-30", "31-60", "61-120", ">120"])
    o["delta_bucket"] = pd.cut(o["long_delta"].abs(), [0, 0.3, 0.45, 0.6, 1.01], labels=["<0.30", "0.30-0.45", "0.45-0.60", ">0.60"])
    o["otm_bucket"] = pd.cut(o["otm_pct"], [-9, 0, 0.03, 0.07, 9], labels=["ITM", "0-3% OTM", "3-7% OTM", ">7% OTM"])
    for col in ("structure", "dte_bucket", "delta_bucket", "otm_bucket"):
        out[f"by_{col}"] = group_perf(o.assign(**{col: o[col].astype(str)}), col)
    cf = o.dropna(subset=["stock_counterfactual_r"])
    if len(cf):
        out["options_vs_stock"] = {"n": len(cf), "option_avg_r": float(cf["r"].mean()),
                                   "stock_same_thesis_avg_r": float(cf["stock_counterfactual_r"].mean()),
                                   "options_better_share": float((cf["r"] > cf["stock_counterfactual_r"]).mean()),
                                   "evidence": evidence(len(cf))}
    iv = o.dropna(subset=["iv", "realized_vol"])
    if len(iv):
        out["iv_vs_realized"] = {"n": len(iv), "avg_entry_iv": float(iv["iv"].mean()),
                                 "avg_realized_vol": float(iv["realized_vol"].mean()),
                                 "overpaid_share": float((iv["iv"] > iv["realized_vol"]).mean()), "evidence": evidence(len(iv))}
    th = o.dropna(subset=["theta_day", "holding_days"])
    if len(th):
        est = -th["theta_day"] * th["holding_days"]
        out["theta"] = {"n": len(th), "avg_est_theta_cost": float(est.mean()),
                        "theta_cost_vs_risk": float((est / th["risk"]).mean()), "evidence": evidence(len(th))}
    return out


# ------------------------------------------------------------------ 4. screener forward test
_ARR: Dict = {}


def _arrays(P: Dict[str, pd.DataFrame], t: str):
    key = (id(P), t)
    if key not in _ARR:
        c = P["close"][t].dropna()
        _ARR[key] = (c.index, P["open"][t].reindex(c.index).to_numpy(float), P["high"][t].reindex(c.index).to_numpy(float),
                     P["low"][t].reindex(c.index).to_numpy(float), c.to_numpy(float))
    return _ARR[key]


def simulate(P: Dict[str, pd.DataFrame], ticker: str, direction: str, stop: float, target: float, day: str,
             same_day: bool) -> Dict[str, object]:
    """Hypothetical trade used by the forward test, the backtest, rejected trades and opportunity cost (one method for all).

    Entry at the open of `day` (same_day=True, i.e. decided before the open) or of the next session; stop checked before
    target each day; exit at the close after TIMEOUT_DAYS sessions. Returns status, entry, exit, R and days."""
    if ticker not in P["close"] or P["close"][ticker].dropna().empty:
        return {"status": "no data"}
    idx, o, h, l, c = _arrays(P, ticker)
    d = pd.Timestamp(day)
    i0 = idx.searchsorted(d, side="left" if same_day else "right")
    if i0 >= len(idx):
        return {"status": "waiting"}
    long_ = direction == "LONG"
    entry = o[i0]
    risk = (entry - stop) if long_ else (stop - entry)
    if not np.isfinite(entry) or risk <= 0:
        return {"status": "void (opened beyond stop)", "entry": float(entry)}
    end = min(i0 + TIMEOUT_DAYS, len(idx))
    for k in range(i0, end):
        if (l[k] <= stop) if long_ else (h[k] >= stop):
            return _res(entry, stop, "stop", risk, long_, k - i0 + 1, idx[k])
        if (h[k] >= target) if long_ else (l[k] <= target):
            return _res(entry, target, "target", risk, long_, k - i0 + 1, idx[k])
    last = c[end - 1]
    if end - i0 < TIMEOUT_DAYS:
        return {"status": "open", "entry": float(entry), "days": end - i0,
                "mark_r": float(((last - entry) if long_ else (entry - last)) / risk)}
    return _res(entry, last, "timeout", risk, long_, end - i0, idx[end - 1])


def _res(entry, exit_px, why, risk, long_, n, d):
    return {"status": "resolved", "entry": float(entry), "exit": float(exit_px), "exit_reason": why, "days": n,
            "r": float(((exit_px - entry) if long_ else (entry - exit_px)) / risk), "exit_date": str(d.date())}


def independent(df: pd.DataFrame) -> pd.Series:
    """True for signals that don't overlap an earlier signal on the same ticker/direction that was still running.

    Consecutive-day signals on one stock are the same trade, not new evidence; counting them all overstates the
    sample size and narrows the confidence intervals."""
    keep = pd.Series(False, index=df.index)
    busy: Dict = {}
    for i, r in df.sort_values("date").iterrows():
        k = (r["ticker"], r["direction"])
        if k in busy and (busy[k] is None or str(r["date"]) <= busy[k]):
            continue
        if r.get("status") in ("resolved", "open"):
            keep[i] = True
            busy[k] = r.get("exit_date") if r.get("status") == "resolved" else None
    return keep


def forward_test(P: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    if not memory.SIGNALS.exists():
        return pd.DataFrame()
    sig = pd.read_csv(memory.SIGNALS)
    res = [simulate(P, s.ticker, s.direction, s.stop, s.target, s.date,
                    same_day=bool(str(s.generated)[11:16]) and str(s.generated)[11:16] < "09:30")   # pre-open scan
           for s in sig.itertuples()]
    out = pd.concat([sig.reset_index(drop=True), pd.DataFrame(res)], axis=1)
    out["regime_trend"] = out["regime_rule"].map(memory.TREND_MAP)
    out["month"] = out["date"].astype(str).str[:7]
    out["setup_list"] = out["setups"].astype(str).str.split("+")
    out["independent"] = independent(out) if len(out) else False
    return out


# ------------------------------------------------------------------ 5. regimes
def regime_study(P: Dict[str, pd.DataFrame]) -> Dict[str, object]:
    if not memory.REGIME_LOG.exists():
        return {}
    log = pd.read_csv(memory.REGIME_LOG)
    c = P["close"]
    etfs = config()["sector_etfs"]
    rows = []
    for r in log.itertuples():
        d = pd.Timestamp(r.date)
        idx = c["SPY"].dropna().index
        base = idx[idx <= d]
        if not len(base):
            continue
        b = idx.get_loc(base[-1])
        row = {"date": r.date, "regime": r.regime_rule, "trend": memory.TREND_MAP.get(r.regime_rule)}
        for k, n in (("spy_fwd_5d", 5), ("spy_fwd_20d", 20)):
            row[k] = float(c["SPY"].iloc[b + n] / c["SPY"].iloc[b] - 1) if b + n < len(idx) else None
        if b + 5 < len(idx):
            for name, etf in etfs.items():
                if etf in c:
                    row[f"sec::{name}"] = float(c[etf].iloc[b + 5] / c[etf].iloc[b] - 1 - (c["SPY"].iloc[b + 5] / c["SPY"].iloc[b] - 1))
        rows.append(row)
    df = pd.DataFrame(rows)
    out = {"days_logged": len(log), "history": log.tail(60).to_dict("records")}
    if df.empty:
        return out
    by = {}
    for k, g in df.groupby("regime"):
        f5, f20 = g["spy_fwd_5d"].dropna(), g["spy_fwd_20d"].dropna()
        secs = {c_[5:]: float(g[c_].mean()) for c_ in g.columns if c_.startswith("sec::") and g[c_].notna().any()}
        by[k] = {"days": len(g), "spy_fwd_5d_avg": float(f5.mean()) if len(f5) else None, "n_5d": len(f5),
                 "spy_fwd_5d_pos_share": float((f5 > 0).mean()) if len(f5) else None,
                 "spy_fwd_20d_avg": float(f20.mean()) if len(f20) else None, "n_20d": len(f20),
                 "best_sectors_5d_vs_spy": sorted(secs.items(), key=lambda kv: -kv[1])[:3],
                 "worst_sectors_5d_vs_spy": sorted(secs.items(), key=lambda kv: kv[1])[:3],
                 "evidence": evidence(len(f5)) + " (overlapping days are not independent)"}
    out["by_regime"] = by
    return out


# ------------------------------------------------------------------ main
def run(quiet: bool = False) -> Dict[str, object]:
    from desk import paper

    book = paper.load()
    ps, es = memory.predictions(), memory.events()
    sig = pd.read_csv(memory.SIGNALS) if memory.SIGNALS.exists() else pd.DataFrame(columns=["ticker", "date"])
    tickers = {"SPY"} | set(config()["sector_etfs"].values()) | set(sig["ticker"])
    tickers |= {(t.get("option") or {}).get("underlying") or t["ticker"] for t in book["trades"]}
    tickers |= {p["ticker"] for p in ps if p.get("ticker")} | {p["benchmark"] for p in ps if p.get("benchmark")}
    tickers |= {r["ticker"] for r in memory.rejections()} | {"QQQ", "IWM", "^VIX", "^TNX", "DX-Y.NYB", "CL=F", "GC=F"}
    for e in es:
        tickers |= set(e.get("tickers", [])) | set(e.get("sectors", []))
    dates = [t["opened"][:10] for t in book["trades"]] + [p["created"][:10] for p in ps] + [e["date"] for e in es] + \
            list(sig["date"].astype(str))
    since = min(dates + [str(now_ny().date())])
    from desk import backtest, intelligence, regimes

    P = regimes.load_prices()                                   # 3y: market, sector ETFs, whole universe
    extra = sorted(t for t in tickers if t and t not in P["close"])
    if extra:
        more = _prices(extra, since)
        P = {f: pd.concat([P[f], more[f].reindex(columns=[c for c in more[f].columns if c not in P[f]])], axis=1)
             for f in ("open", "high", "low", "close")} | {"volume": P["volume"]}
    _ARR.clear()
    hist = regimes.build(P)
    backtest.generate(P)
    bt = backtest.outcomes(P)

    ps = score_predictions(P)
    es = score_events(P)
    tf = trade_frame(book, P)
    if len(tf):                                                  # composite regime on the entry day
        reg = tf["opened"].str[:10].map(lambda d: regimes.on(d, hist))
        tf["composite_trend"] = reg.map(lambda r: r.get("trend"))
        tf["composite_risk"] = reg.map(lambda r: r.get("risk"))
        tf["composite_vol"] = reg.map(lambda r: r.get("volatility"))
    closed = tf[tf["status"] == "closed"] if len(tf) else tf
    ft = forward_test(P)
    if len(ft) and len(hist):                                    # composite regime on each signal's day
        ft = ft.join(hist.set_index("date")[["trend", "risk", "volatility"]], on="date")
    resolved = ft[(ft["status"] == "resolved") & ft["independent"]] if len(ft) else ft
    bres = bt[(bt["status"] == "resolved") & bt["independent"]] if len(bt) else bt
    out = {
        "generated": now_ny().strftime("%Y-%m-%d %H:%M ET"),
        "data_through": str(P["close"].index[-1].date()) if P else None,
        "system_version": memory.current_version(), "version_check": memory.version_check(),
        "trades": {int(r["id"]): {k: v for k, v in r.items() if k in ("mfe_pct", "mae_pct", "mfe_r", "mae_r", "realized_vol",
                   "underlying_exit", "stock_counterfactual_r", "underlying_last") and v == v and v is not None}
                   for r in tf.to_dict("records")} if len(tf) else {},
        "performance": {"overall": perf(closed), **{f"by_{c}": group_perf(closed, c) for c in
                        ("strategies", "regime_trend", "regime_vol", "regime_risk", "catalyst_category", "sector",
                         "instrument", "direction", "structure", "system_version")}},
        "strategy_by_regime": {str(k): group_perf(g, "regime_trend") for k, g in
                               (closed.explode("strategies").groupby("strategies") if len(closed) else [])},
        "options": options_stats(tf) if len(tf) else {"closed_option_trades": 0},
        "predictions": prediction_stats(ps),
        "events": event_stats(es),
        "forward_test": {"signals": int(len(ft)), "resolved": int(len(resolved)),
                         "open": int((ft["status"] == "open").sum()) if len(ft) else 0,
                         "overall": perf(resolved) if len(resolved) else {"n": 0, "evidence": evidence(0)},
                         "by_setup": group_perf(resolved, "setup_list"), "by_regime": group_perf(resolved, "regime_trend"),
                         "by_direction": group_perf(resolved, "direction"),
                         "setup_by_regime": {str(k): group_perf(g, "regime_trend") for k, g in
                                             (resolved.explode("setup_list").groupby("setup_list") if len(resolved) else [])},
                         "recent": ft.tail(40).replace({np.nan: None}).to_dict("records") if len(ft) else []},
        "regimes": regime_study(P) if P else {},
    }
    out["performance"].update({f"by_{c}": group_perf(closed, c) for c in ("composite_trend", "composite_risk", "composite_vol")
                               if len(closed) and c in closed})
    out["forward_test"]["independent_resolved"] = int(len(resolved))
    out["backtest"] = {
        "period": f"{bt['date'].min()} → {bt['date'].max()}" if len(bt) else None, "signals": int(len(bt)),
        "independent_resolved": int(len(bres)), "overall": perf(bres) if len(bres) else {"n": 0},
        "by_setup": group_perf(bres, "setup_list"), "by_trend": group_perf(bres, "trend"),
        "by_risk": group_perf(bres, "risk"), "by_volatility": group_perf(bres, "volatility"),
        "by_direction": group_perf(bres, "direction"), "by_sector": group_perf(bres, "sector"),
        "setup_by_trend": {str(k): group_perf(g, "trend") for k, g in (bres.explode("setup_list").groupby("setup_list") if len(bres) else [])},
        "biases": "survivorship (today's universe), no costs/slippage, no earnings filter, current screener code; "
                  "overlapping signals removed (one live signal per ticker/direction at a time)"}
    out["regime_now"] = {k: v for k, v in (hist.iloc[-1].to_dict() if len(hist) else {}).items()}
    out["regime_counts"] = {c: hist[c].value_counts().to_dict() for c in ("trend", "risk", "volatility")} if len(hist) else {}
    watch = []
    try:
        from desk.obsidian import vault_dir
        import re as _re
        w = vault_dir()
        if w and (w / "03 - Portfolio" / "Watchlist.md").exists():
            watch = sorted(set(_re.findall(r"\[\[([A-Z.]{1,6})\]\]", (w / "03 - Portfolio" / "Watchlist.md").read_text())))
    except Exception:
        pass
    out["intelligence"] = intelligence.run(P, book, tf, ft, bt, watch)
    JOURNAL.mkdir(exist_ok=True)
    LEARNING.write_text(json.dumps(out, indent=1, default=lambda v: None if v != v else str(v)))
    if not quiet:
        print(f"Learning updated ({out['generated']}, data through {out['data_through']})")
        print(f"  regime today: {out['regime_now'].get('trend')} / {out['regime_now'].get('risk')} / {out['regime_now'].get('volatility')}"
              f" · backtest {out['backtest']['independent_resolved']} independent signals")
        print(f"  trades closed {out['performance']['overall']['n']} · predictions scored {out['predictions']['scored']}"
              f"/{out['predictions']['total']} · events {len(es)} · signals {len(ft)} ({len(resolved)} resolved)")
        if not out["version_check"]["ok"]:
            print("  ⚠", out["version_check"]["problem"], *out["version_check"].get("diffs", []), sep="\n    ")
    return out


def load() -> Dict[str, object]:
    return json.loads(LEARNING.read_text()) if LEARNING.exists() else {}
