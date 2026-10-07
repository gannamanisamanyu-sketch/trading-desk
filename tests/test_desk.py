"""Offline unit tests for the deterministic parts of the desk."""
import math

import numpy as np
import pandas as pd
import pytest

from desk import options as O
from desk import paper, risk
from desk.analysis import market_regime, rsi
from desk.news import score, source_weight, tag


# ------------------------------------------------------------------ risk engine
def test_stock_sizing_respects_one_percent_rule():
    s = risk.size_stock(30_000, entry=100, stop=95, target=112)
    assert s.ok and s.quantity == 45            # floor(300 / 5) = 60, but 15% cap = 45 shares
    assert s.binding_limit == "15% max position"
    s2 = risk.size_stock(30_000, entry=100, stop=90, target=125)
    assert s2.quantity == 30 and s2.risk == pytest.approx(300)   # 1% rule binds
    assert s2.reward_risk == pytest.approx(2.5)


def test_stock_sizing_rejects_bad_stops_and_full_book():
    assert not risk.size_stock(30_000, 100, 101, 110, direction="LONG").ok
    assert risk.size_stock(30_000, 100, 101, 90, direction="SHORT").ok
    full = risk.size_stock(30_000, 100, 90, 125, open_risk=1_500)
    assert not full.ok and "5%" in full.binding_limit


def test_option_sizing_uses_full_premium_as_risk():
    s = risk.size_option(30_000, max_loss_per_contract=243, profit_at_target_per_contract=318)
    assert s.quantity == 1 and s.risk == 243
    too_big = risk.size_option(30_000, max_loss_per_contract=560, profit_at_target_per_contract=700)
    assert not too_big.ok and too_big.notes
    capped = risk.size_option(30_000, 100, 200, options_exposure=4_450)
    assert capped.quantity == 0                 # 15% options cap ($4,500) nearly used


def test_portfolio_check_flags_breaches():
    issues = risk.portfolio_check(30_000, [{"ticker": "X", "instrument": "stock", "risk": 400, "market_value": 6_000}])
    assert any("1% limit" in i for i in issues) and any("15% limit" in i for i in issues)


# ------------------------------------------------------------------ options math
def test_black_scholes_put_call_parity_and_iv_roundtrip():
    S, K, T, r, v = 100, 105, 0.5, 0.04, 0.3
    c, p = O.bs_price(S, K, T, r, v, True), O.bs_price(S, K, T, r, v, False)
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-9)
    assert O.implied_vol(c, S, K, T, r, True) == pytest.approx(v, abs=1e-6)
    g = O.bs_greeks(S, K, T, r, v, True)
    assert 0 < g["delta"] < 1 and g["gamma"] > 0 and g["theta"] < 0 and g["vega"] > 0
    assert O.implied_vol(0.0, S, K, T, r, True) is None   # below intrinsic / no price


def test_probability_beyond_is_half_at_the_forward_for_zero_vol_drift():
    assert O.prob_beyond(100, 100, 0.25, 0.2, True) == pytest.approx(0.48, abs=0.02)
    assert O.prob_beyond(100, 150, 0.1, 0.2, True) < 0.01


# ------------------------------------------------------------------ news tagging
def test_ambiguous_tickers_need_explicit_form():
    assert tag("RBI MPC hikes repo rate")["tickers"] == []
    assert tag("Visa rules tighten for students")["tickers"] == []
    assert tag("Nvidia (NVDA) jumps on AI demand")["tickers"] == ["NVDA"]
    assert "TGT" in tag("Target Corp shares slump")["tickers"]


def test_news_score_prefers_major_sources_and_fades_with_age():
    assert source_weight("Reuters") > source_weight("Some Blog") > source_weight("디지털투데이")
    fresh = score("Nvidia shares surge on earnings beat", ["earnings"], ["NVDA"], 1, "Reuters")
    old = score("Nvidia shares surge on earnings beat", ["earnings"], ["NVDA"], 40, "Reuters")
    assert fresh > old


# ------------------------------------------------------------------ regime
def _fake_data(trend: float, n: int = 300):
    idx = pd.bdate_range("2025-01-01", periods=n)
    rng = np.random.default_rng(0)
    cols = {t: 100 * np.exp(np.cumsum(trend + rng.normal(0, 0.008, n))) for t in ["SPY", "AAPL", "MSFT", "XOM"]}
    return {"close": pd.DataFrame(cols, index=idx)}


def test_regime_bull_vs_panic():
    assert market_regime(_fake_data(0.002), vix=13, vix3m=16)["regime"] in ("STRONG BULL", "BULL")
    assert market_regime(_fake_data(-0.002), vix=35, vix3m=28)["regime"] == "PANIC / RISK-OFF"


def test_rsi_bounds():
    x = pd.DataFrame({"a": np.linspace(1, 2, 60)})
    assert rsi(x).iloc[-1, 0] > 95


# ------------------------------------------------------------------ paper ledger
@pytest.fixture()
def tmp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(paper, "EQUITY", tmp_path / "equity.csv")
    return tmp_path


def test_paper_add_close_and_stats(tmp_ledger):
    t = paper.add_trade("ABC", "stock", "LONG", 30, 100.0, 90.0, 125.0, "test", "none", "breakout", "BULL", "medium",
                        risk=300, expected_return=0.25)
    book = paper.load()
    assert paper.account(book)["cash"] == pytest.approx(27_000)
    paper.close_trade(book, book["trades"][0], 125.0, "target", "2026-10-20")
    paper.save(book)
    st = paper.statistics()
    assert st["closed_trades"] == 1 and st["win_rate"] == 1.0 and st["total_pnl"] == pytest.approx(750)
    assert book["trades"][0]["r_multiple"] == pytest.approx(2.5)


def test_paper_rejects_limit_breach(tmp_ledger):
    with pytest.raises(ValueError):
        paper.add_trade("BIG", "stock", "LONG", 100, 100.0, 90.0, 120.0, "too big", "", "x", "", "", risk=1_000,
                        expected_return=0.2)


def test_short_stock_pnl(tmp_ledger):
    paper.add_trade("SHRT", "stock", "SHORT", 20, 50.0, 55.0, 40.0, "t", "", "breakdown", "", "", risk=100,
                    expected_return=0.2)
    book = paper.load()
    acct = paper.account(book, marks={1: 45.0})
    assert acct["unrealized_pnl"] == pytest.approx(100)    # (50 - 45) x 20
    paper.close_trade(book, book["trades"][0], 40.0, "target")
    assert book["trades"][0]["pnl"] == pytest.approx(200)


def _isolate(tmp_path, monkeypatch):
    """Point journal files at a temp dir so tests never touch the real memory."""
    from desk import learning, memory

    for name in ("PREDICTIONS", "EVENTS", "VERSIONS", "SIGNALS", "REGIME_LOG"):
        monkeypatch.setattr(memory, name, tmp_path / getattr(memory, name).name)
    monkeypatch.setattr(learning, "LEARNING", tmp_path / "learning.json")


def _bars(days, ticker, closes, highs=None, lows=None, opens=None):
    import pandas as pd

    idx = pd.bdate_range(days, periods=len(closes))
    f = lambda v: pd.DataFrame({ticker: v if v is not None else closes}, index=idx)
    return {"open": f(opens), "high": f(highs), "low": f(lows), "close": f(closes)}


def test_evidence_levels_never_conclude_on_small_samples():
    import pandas as pd
    from desk import learning

    small = learning.perf(pd.DataFrame({"r": [2.0] * 10, "ticker": ["A"] * 10}))
    assert small["evidence"].startswith("INSUFFICIENT")
    mid = learning.perf(pd.DataFrame({"r": [1.0, -0.5] * 15, "ticker": list("ABCDEFGHIJKLMNO") * 2}))
    assert mid["evidence"].startswith("PRELIMINARY")
    # 60 winners in ONE ticker and ONE regime: positive but not robust -> not "established positive"
    big = learning.perf(pd.DataFrame({"r": [1.0, 0.5, -0.4] * 20, "ticker": ["A"] * 60, "regime_trend": ["BULL"] * 60,
                                      "month": ["2026-10"] * 60}))
    assert big["evidence"].startswith("NOT PROVEN")
    assert not big["robust"]


def test_forward_test_stop_first_and_next_open_entry(tmp_path, monkeypatch):
    import pandas as pd
    from desk import learning, memory

    _isolate(tmp_path, monkeypatch)
    # signal logged after the open on day 0 -> enters at day 1 open (100); day 2 touches both stop and target -> stop first
    pd.DataFrame([{"date": "2026-01-05", "generated": "2026-01-05 15:00 ET", "session": "open", "ticker": "AAA", "direction": "LONG",
                   "setups": "breakout", "entry_ref": 99, "stop": 95, "target": 110, "reward_risk": 2, "score": 1,
                   "regime_rule": "BULL", "system_version": "1.0"}]).to_csv(memory.SIGNALS, index=False)
    P = _bars("2026-01-05", "AAA", [99, 101, 100, 100], highs=[99, 102, 111, 101], lows=[98, 99, 94, 99], opens=[99, 100, 101, 100])
    ft = learning.forward_test(P)
    row = ft.iloc[0]
    assert row["status"] == "resolved" and row["exit_reason"] == "stop"
    assert row["entry"] == 100 and abs(row["r"] - (-1.0)) < 1e-9


def test_prediction_scoring_uses_anchored_price(tmp_path, monkeypatch):
    from desk import learning, memory

    _isolate(tmp_path, monkeypatch)
    memory.save_predictions([{"id": 1, "created": "2026-01-05 10:00", "ticker": "AAA", "direction": "UP", "due": "2026-01-07",
                              "start_price": 100.0, "benchmark": None, "bench_start": None, "magnitude": 0.05,
                              "confidence": "high", "status": "pending"}])
    P = _bars("2026-01-05", "AAA", [101, 102, 103, 104])
    p = learning.score_predictions(P)[0]
    assert p["status"] == "scored" and p["correct"] and abs(p["actual_return"] - 0.03) < 1e-9
    assert p["magnitude_met"] is False


def test_version_check_flags_undocumented_change(tmp_path, monkeypatch):
    from desk import memory

    _isolate(tmp_path, monkeypatch)
    snap = memory.param_snapshot()
    memory._save(memory.VERSIONS, [{"version": memory.current_version(), "params": snap}])
    assert memory.version_check()["ok"]
    changed = {**snap, "risk": {**snap["risk"], "max_risk_per_trade": 0.02}}
    memory._save(memory.VERSIONS, [{"version": memory.current_version(), "params": changed}])
    r = memory.version_check()
    assert not r["ok"] and any("max_risk_per_trade" in d for d in r["diffs"])


def test_obsidian_sync_preserves_manual_notes(tmp_path, monkeypatch):
    from desk import obsidian

    folder = tmp_path / "Trading"
    monkeypatch.setenv("TRADING_DESK_VAULT", str(folder))
    first = obsidian.sync(quiet=True)
    assert (folder / "Trading Dashboard.md").exists() and (folder / "00 - System" / "Trading Constitution.md").exists()
    dash = folder / "Trading Dashboard.md"
    dash.write_text(dash.read_text() + "\n- my own note\n")
    pipe = folder / "09 - Strategy Research" / "Improvement Pipeline.md"
    pipe.write_text(pipe.read_text() + "\n- my observation\n")
    obsidian.sync(quiet=True)
    assert "my own note" in dash.read_text() and dash.read_text().count(obsidian.START) == 1
    assert "my observation" in pipe.read_text()
    assert first["changed"]
