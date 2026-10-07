"""Options-data pipeline and structure selector.

Data: Yahoo Finance option chains (free, delayed ~15 min during market hours).
Outside market hours Yahoo often reports bid/ask = 0; we then fall back to the
last traded price and label the quote "last" (stale) instead of "mid".

We do NOT trust Yahoo's implied volatility field. IV is re-computed from the
option's price with Black-Scholes, and Greeks come from that IV:
  delta  = sensitivity to a $1 move in the stock
  gamma  = how fast delta changes
  theta  = value lost per calendar day (time decay)
  vega   = value change per 1 vol-point change in IV
IV RANK needs a year of IV history. We save the at-the-money 30-day IV every
time a ticker is analyzed (data/iv_history.csv), so IV rank becomes available
as history accumulates; until then we compare IV with realized volatility.

Structure selection optimizes payoff-at-target and probability of profit
against MAXIMUM LOSS. It never ranks an option just because its percentage
upside is large.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.optimize import brentq
from scipy.stats import norm

from desk.common import DATA, config, now_ny, stamp

# ------------------------------------------------------------------ Black-Scholes
def bs_price(S, K, T, r, sigma, call: bool) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S, K, T, r, sigma, call: bool) -> Dict[str, float]:
    if T <= 0 or sigma <= 0:
        return {"delta": float(call and S > K) - float((not call) and S < K), "gamma": 0, "theta": 0, "vega": 0}
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf = norm.pdf(d1)
    delta = norm.cdf(d1) if call else norm.cdf(d1) - 1
    gamma = pdf / (S * sigma * math.sqrt(T))
    theta_year = (-S * pdf * sigma / (2 * math.sqrt(T))
                  - (r * K * math.exp(-r * T) * norm.cdf(d2) if call else -r * K * math.exp(-r * T) * norm.cdf(-d2)))
    return {"delta": delta, "gamma": gamma, "theta": theta_year / 365.0, "vega": S * pdf * math.sqrt(T) / 100.0}


def implied_vol(price, S, K, T, r, call: bool) -> Optional[float]:
    intrinsic = max(0.0, (S - K) if call else (K - S))
    if price is None or not np.isfinite(price) or price <= intrinsic + 1e-4 or T <= 0:
        return None
    try:
        return brentq(lambda s: bs_price(S, K, T, r, s, call) - price, 1e-3, 5.0, maxiter=200)
    except ValueError:
        return None


def prob_beyond(S, K, T, sigma, above: bool) -> float:
    """Risk-neutral-ish probability the stock finishes above (or below) K at expiry (lognormal, zero drift)."""
    if T <= 0 or sigma <= 0:
        return float((S > K) if above else (S < K))
    d2 = (math.log(S / K) - 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return float(norm.cdf(d2) if above else norm.cdf(-d2))


# ------------------------------------------------------------------ chain
def _years_to(expiry: str) -> float:
    exp = datetime.combine(date.fromisoformat(expiry), datetime.min.time()).replace(hour=16, tzinfo=now_ny().tzinfo)
    return max((exp - now_ny()).total_seconds() / (365 * 24 * 3600), 1e-6)


def historical_vol(ticker: str) -> Dict[str, float]:
    px = yf.Ticker(ticker).history(period="6mo")["Close"]
    r = np.log(px).diff().dropna()
    return {"hv20": float(r.iloc[-20:].std() * np.sqrt(252)), "hv60": float(r.iloc[-60:].std() * np.sqrt(252)),
            "spot": float(px.iloc[-1]), "spot_time": stamp(px.index[-1].to_pydatetime())}


def load_chain(ticker: str, min_days: Optional[int] = None, max_days: Optional[int] = None,
               r: Optional[float] = None) -> Dict[str, object]:
    ocfg = config()["options"]
    min_days = ocfg["min_days"] if min_days is None else min_days
    max_days = ocfg["max_days"] if max_days is None else max_days
    r = ocfg["risk_free_rate"] if r is None else r
    tk = yf.Ticker(ticker)
    hv = historical_vol(ticker)
    S = hv["spot"]
    rows = []
    for exp in tk.options:
        dte = (date.fromisoformat(exp) - now_ny().date()).days
        if dte < min_days or dte > max_days:
            continue
        ch = tk.option_chain(exp)
        for call, df in ((True, ch.calls), (False, ch.puts)):
            for o in df.itertuples():
                bid, ask, last = float(o.bid or 0), float(o.ask or 0), float(o.lastPrice or 0)
                if bid > 0 and ask > 0:
                    px, quality = (bid + ask) / 2, "mid"
                elif last > 0:
                    px, quality = last, "last"
                else:
                    continue
                rows.append({"expiry": exp, "dte": dte, "type": "call" if call else "put", "strike": float(o.strike),
                             "bid": bid, "ask": ask, "last": last, "price": px, "quality": quality,
                             "volume": float(o.volume) if o.volume == o.volume else 0.0,
                             "open_interest": float(o.openInterest or 0), "contract": o.contractSymbol,
                             "last_trade": str(o.lastTradeDate)})
    chain = pd.DataFrame(rows)
    if chain.empty:
        return {"ticker": ticker, "spot": S, "chain": chain, **hv}
    T = chain["expiry"].map(_years_to)
    chain["T"] = T
    ivs, greeks = [], []
    for row, t in zip(chain.itertuples(), T):
        iv = implied_vol(row.price, S, row.strike, t, r, row.type == "call")
        ivs.append(iv)
        greeks.append(bs_greeks(S, row.strike, t, r, iv, row.type == "call") if iv else
                      {"delta": np.nan, "gamma": np.nan, "theta": np.nan, "vega": np.nan})
    chain["iv"] = ivs
    chain = pd.concat([chain, pd.DataFrame(greeks)], axis=1)
    chain["spread_pct"] = np.where(chain["quality"] == "mid", (chain["ask"] - chain["bid"]) / chain["price"], np.nan)
    chain["unusual"] = (chain["volume"] > np.maximum(500, 2 * chain["open_interest"]))
    out = {"ticker": ticker, "spot": S, "chain": chain, "r": r, **hv, "retrieved": stamp(),
           "quote_quality": "live mid prices" if (chain["quality"] == "mid").mean() > 0.5
           else "STALE: bid/ask unavailable (market closed?), using last trade prices"}
    out.update(term_structure(chain, S))
    _record_iv(ticker, out.get("atm_iv_30d"))
    out["iv_rank"] = iv_rank(ticker, out.get("atm_iv_30d"))
    return out


def term_structure(chain: pd.DataFrame, S: float) -> Dict[str, object]:
    """ATM IV and expected move (ATM straddle) for each expiry."""
    rows = []
    for exp, g in chain.groupby("expiry"):
        calls, puts = g[g.type == "call"], g[g.type == "put"]
        if calls.empty or puts.empty:
            continue
        k = calls.iloc[(calls["strike"] - S).abs().argsort()[:1]]["strike"].iloc[0]
        c = calls[calls.strike == k]
        p = puts[puts.strike == k]
        if c.empty or p.empty:
            continue
        straddle = float(c["price"].iloc[0] + p["price"].iloc[0])
        iv = np.nanmean([c["iv"].iloc[0] or np.nan, p["iv"].iloc[0] or np.nan])
        rows.append({"expiry": exp, "dte": int(g["dte"].iloc[0]), "atm_strike": k, "atm_iv": iv,
                     "expected_move": straddle, "expected_move_pct": straddle / S})
    ts = pd.DataFrame(rows)
    atm30 = None
    if len(ts):
        atm30 = float(ts.iloc[(ts["dte"] - 30).abs().argsort()[:1]]["atm_iv"].iloc[0])
    return {"term": ts, "atm_iv_30d": atm30}


def _record_iv(ticker: str, iv: Optional[float]) -> None:
    if not iv or not np.isfinite(iv):
        return
    path = DATA / "iv_history.csv"
    DATA.mkdir(exist_ok=True)
    today = str(now_ny().date())
    df = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["date", "ticker", "atm_iv_30d"])
    df = df[~((df["date"] == today) & (df["ticker"] == ticker))]
    df = pd.concat([df, pd.DataFrame([{"date": today, "ticker": ticker, "atm_iv_30d": iv}])])
    df.to_csv(path, index=False)


def iv_rank(ticker: str, iv: Optional[float], min_obs: int = 20) -> Dict[str, object]:
    path = DATA / "iv_history.csv"
    if not iv or not path.exists():
        return {"iv_rank": None, "iv_percentile": None, "note": "no IV history yet"}
    h = pd.read_csv(path).query("ticker == @ticker")["atm_iv_30d"].dropna()
    if len(h) < min_obs:
        return {"iv_rank": None, "iv_percentile": None,
                "note": f"only {len(h)} day(s) of IV history (need {min_obs}+); compare IV with realized vol instead"}
    lo, hi = h.min(), h.max()
    return {"iv_rank": float((iv - lo) / (hi - lo)) if hi > lo else None,
            "iv_percentile": float((h < iv).mean()), "note": f"{len(h)} days of history"}


# ------------------------------------------------------------------ structure selection
def evaluate_structures(ch: Dict[str, object], direction: str, target: float, stop: float, horizon_days: int,
                        risk_budget: float) -> pd.DataFrame:
    """Compare long calls/puts and vertical debit spreads for a directional thesis.

    For each candidate (per 1 contract = 100 shares):
      cost / max loss, max profit, breakeven, probability of profit at expiry,
      value if the stock reaches TARGET at the horizon, value if it hits STOP halfway,
      reward/risk at target, theta per day, liquidity.
    """
    chain: pd.DataFrame = ch["chain"]
    S, r = ch["spot"], ch.get("r", 0.04)
    want = "call" if direction == "LONG" else "put"
    ocfg = config()["options"]
    c = chain[(chain["type"] == want) & chain["iv"].notna() & (chain["dte"] >= horizon_days + 10)]
    c = c[(c["open_interest"] >= ocfg["min_open_interest"]) | (c["volume"] >= ocfg["min_open_interest"])]
    if c.empty:
        return pd.DataFrame()
    rows = []
    for exp, g in c.groupby("expiry"):
        g = g.sort_values("strike")
        T = g["T"].iloc[0]
        Th = max(T - horizon_days / 365, 1e-6)
        Tm = max(T - horizon_days / 730, 1e-6)
        for o in g.itertuples():
            if not (0.25 <= abs(o.delta) <= 0.8):
                continue
            liq_ok = (o.quality == "mid" and o.spread_pct <= ocfg["max_spread_pct"]) or o.quality == "last"
            # --- single long option
            cost = o.price * 100
            v_t = bs_price(target, o.strike, Th, r, o.iv, want == "call") * 100
            v_s = bs_price(stop, o.strike, Tm, r, o.iv, want == "call") * 100
            be = o.strike + o.price if want == "call" else o.strike - o.price
            rows.append({"structure": f"Long {want}", "expiry": exp, "dte": o.dte, "long_strike": o.strike,
                         "short_strike": None, "debit": o.price, "cost": cost, "max_loss": cost, "max_profit": np.inf,
                         "breakeven": be, "pop": prob_beyond(S, be, T, o.iv, want == "call"),
                         "value_at_target": v_t, "pnl_at_target": v_t - cost, "pnl_at_stop": v_s - cost,
                         "delta": o.delta, "theta_day": o.theta * 100, "vega": o.vega * 100, "iv": o.iv,
                         "spread_pct": o.spread_pct, "oi": o.open_interest, "volume": o.volume, "quality": o.quality,
                         "liquid": liq_ok, "contract": o.contract, "short_contract": None})
            # --- vertical debit spreads: every short strike between the long strike and the target
            #     (plus the first strike beyond it). Narrower spreads cost less, so some fit the 1% budget.
            if not (0.35 <= abs(o.delta) <= 0.8):
                continue
            full = chain[(chain["type"] == want) & (chain["expiry"] == exp) & chain["iv"].notna()]
            if want == "call":
                beyond = full[full["strike"] >= target].nsmallest(1, "strike")
                shorts = pd.concat([full[(full["strike"] > o.strike) & (full["strike"] < target)], beyond])
            else:
                beyond = full[full["strike"] <= target].nlargest(1, "strike")
                shorts = pd.concat([full[(full["strike"] < o.strike) & (full["strike"] > target)], beyond])
            for _, sk in shorts.drop_duplicates("strike").iterrows():
                debit = o.price - sk["price"]
                width = abs(sk["strike"] - o.strike)
                if width < max(1.0, 0.01 * S):          # too narrow to be meaningful after costs
                    continue
                # A debit spread can never be worth less than its intrinsic value. If it "costs" less,
                # the two legs' prices are inconsistent (stale last trades from different times): skip.
                intrinsic = max(0.0, min((S - o.strike) if want == "call" else (o.strike - S), width))
                if debit <= 0.05 or debit >= width or debit < 0.95 * intrinsic:
                    continue
                cost = debit * 100
                vt = (bs_price(target, o.strike, Th, r, o.iv, want == "call") - bs_price(target, sk["strike"], Th, r, sk["iv"], want == "call")) * 100
                vs = (bs_price(stop, o.strike, Tm, r, o.iv, want == "call") - bs_price(stop, sk["strike"], Tm, r, sk["iv"], want == "call")) * 100
                be = o.strike + debit if want == "call" else o.strike - debit
                rows.append({"structure": f"{'Bull call' if want == 'call' else 'Bear put'} spread", "expiry": exp,
                             "dte": o.dte, "long_strike": o.strike, "short_strike": float(sk["strike"]), "debit": debit,
                             "cost": cost, "max_loss": cost, "max_profit": (width - debit) * 100, "breakeven": be,
                             "pop": prob_beyond(S, be, T, o.iv, want == "call"), "value_at_target": vt,
                             "pnl_at_target": min(vt, (width - debit) * 100 + cost) - cost, "pnl_at_stop": vs - cost,
                             "delta": o.delta - sk["delta"], "theta_day": (o.theta - sk["theta"]) * 100,
                             "vega": (o.vega - sk["vega"]) * 100, "iv": o.iv,
                             "spread_pct": np.nanmax([o.spread_pct, sk["spread_pct"]]),
                             "oi": min(o.open_interest, sk["open_interest"]), "volume": min(o.volume, sk["volume"]),
                             "quality": o.quality if sk["quality"] == "mid" else "last",
                             "liquid": liq_ok and (sk["open_interest"] >= ocfg["min_open_interest"] or sk["volume"] >= ocfg["min_open_interest"]),
                             "contract": o.contract, "short_contract": sk["contract"]})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["rr_at_target"] = df["pnl_at_target"] / df["max_loss"]
    df["contracts_for_budget"] = np.floor(risk_budget / df["max_loss"]).astype(int)
    df["fits_1pct_rule"] = df["contracts_for_budget"] >= 1
    # score: reward at target per $ risked, weighted by probability of profit; penalize illiquidity & theta burn
    burn = (-df["theta_day"] * horizon_days / df["max_loss"]).clip(lower=0)
    df["score"] = (df["rr_at_target"].clip(upper=5) * (0.5 + df["pop"]) - 0.5 * burn
                   - (~df["liquid"]).astype(float) - (~df["fits_1pct_rule"]).astype(float))
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def explain_choice(best: pd.Series, alts: pd.DataFrame, ch: Dict[str, object], entry: float, stop: float,
                   target: float) -> List[str]:
    """Plain-English reasons for the selected structure versus the alternatives."""
    notes = []
    stock_rr = abs(target - entry) / abs(entry - stop)
    notes.append(f"vs. buying the stock: stock reward/risk is {stock_rr:.1f}:1 with risk set by the stop "
                 f"(gaps can exceed it); this structure risks at most ${best['max_loss']:,.0f} per contract "
                 f"and returns {best['rr_at_target']:.1f}× its cost if the target is reached.")
    iv, hv = best["iv"], ch.get("hv20")
    if hv:
        rich = iv > 1.15 * hv
        notes.append(f"volatility: IV {iv:.0%} vs. 20-day realized {hv:.0%}; options look "
                     f"{'EXPENSIVE, which favors spreads (the short leg sells some of that IV)' if rich else 'fairly priced, so a single long option is acceptable'}.")
    same_exp = alts[alts["expiry"] == best["expiry"]]
    other_exp = alts[alts["expiry"] != best["expiry"]].head(1)
    if len(same_exp) > 1:
        o = same_exp.iloc[1]
        notes.append(f"vs. another strike ({o['long_strike']:g}): score {o['score']:.2f} vs. {best['score']:.2f}; "
                     f"delta {o['delta']:.2f} vs. {best['delta']:.2f}.")
    if len(other_exp):
        o = other_exp.iloc[0]
        notes.append(f"vs. another expiration ({o['expiry']}): theta {o['theta_day']:.1f}/day vs. {best['theta_day']:.1f}/day, "
                     f"R at target {o['rr_at_target']:.1f} vs. {best['rr_at_target']:.1f}.")
    other_struct = alts[alts["structure"] != best["structure"]].head(1)
    if len(other_struct):
        o = other_struct.iloc[0]
        notes.append(f"vs. {o['structure'].lower()}: max loss ${o['max_loss']:,.0f}, PoP {o['pop']:.0%}, "
                     f"R at target {o['rr_at_target']:.1f} (score {o['score']:.2f}).")
    return notes
