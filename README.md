# Trading Desk: AI-assisted research & paper-trading desk

A personal research desk for a **$30,000 paper portfolio** of U.S. stocks and options. Every morning it
scans markets, the economy, news, filings and social attention, screens ~200 liquid stocks for setups,
evaluates options structures, sizes everything against hard risk limits, and tracks every idea in a
paper ledger, so the record shows which strategies actually work before any real money is involved.

> **Research and paper trading only. Not financial advice.** There is no brokerage connection and no
> code that can place an order. Data is free and delayed; every number carries a timestamp.

## How to use it
Open a Claude session in this folder and ask **"What's the trade today?"** Claude follows
[`CLAUDE.md`](CLAUDE.md):

1. runs `python desk.py scan` (~20 s)
2. fills the gaps with web search (economic calendar and consensus, futures, breaking news)
3. reasons through the news → trade chain
4. checks fundamentals and options for the best candidates
5. sizes the trades
6. writes `reports/<date>_report.md`, replies with a concise answer, and logs the top ideas to the paper ledger

Commands you can also run yourself:
```bash
.venv/bin/python desk.py scan
.venv/bin/python desk.py options NVDA --dir LONG --target 260 --stop 228 --days 21
.venv/bin/python desk.py size --entry 225 --stop 212 --target 250
.venv/bin/python desk.py news CRM
.venv/bin/python desk.py quote SPY QQQ NVDA
.venv/bin/python desk.py paper status | update | stats
```

## Architecture
```
 ┌──────────────── INGESTION ─────────────────┐   ┌──────────── ANALYSIS ────────────┐
 │ market.py   Yahoo: 200 stocks, sector ETFs │──▶│ analysis.py  regime (6 states)    │
 │             indices, VIX, yields, FX, oil, │   │              sector rotation (RS) │
 │             gold, copper, bitcoin          │   │              6 setup screens      │
 │ macro.py    FRED: CPI, PPI, PCE, payrolls, │──▶│ macro.py     Fed-path proxy       │
 │             claims, GDP, retail, yields,   │   │ news.py      materiality score,   │
 │             HY spread, breakevens          │   │              ticker tagging       │
 │ news.py     Google News topics, CNBC,      │──▶│ brief.py     news-vs-price check  │
 │             MarketWatch, Fed press/speeches│   └───────────────┬───────────────────┘
 │ signals_alt SEC 8-K + Form 4 insider buys, │                   ▼
 │             Reddit attention (ApeWisdom),  │   ┌──────── DECISION (Claude) ────────┐
 │             StockTwits sentiment           │   │ CLAUDE.md playbook + web search:   │
 │ options.py  option chains → own IV/Greeks, │──▶│ news→trade chain, ranking, top 3,  │
 │             expected move, IV history      │   │ options trade of the day, verdict  │
 └────────────────────────────────────────────┘   └───────────────┬───────────────────┘
                                                                   ▼
        risk.py (1% / 5% / 15% / 15% limits)  →  paper.py ledger + equity history  →  reports/
```

## Data sources (all free) and their limits
| Need | Source | Status / limitation |
|---|---|---|
| Prices, indices, VIX, FX, commodities, crypto | Yahoo Finance (`yfinance`) | ✅ delayed ~15 min intraday |
| Option chains | Yahoo Finance | ✅ delayed; bid/ask blank outside market hours (labeled STALE); IV and Greeks recomputed with Black-Scholes |
| IV rank / percentile | own history (`data/iv_history.csv`) | ⏳ builds up daily; meaningful after ~20 days, true 1-year rank after a year |
| Economic data | FRED | ✅ latest released values; ❌ no consensus forecasts (web search) |
| Fed odds | 2Y yield − fed funds proxy | ⚠️ direction only; CME FedWatch via web search when cited |
| News | Google News RSS (Reuters, Bloomberg, WSJ headlines…), CNBC, MarketWatch, Fed | ✅ headlines only, no full articles |
| Filings / insiders | SEC EDGAR live 8-K and Form 4 feeds | ✅ set your name/email in `config/desk.yaml` |
| Social | ApeWisdom (Reddit mentions), StockTwits | ✅ ❌ X/Twitter (paid API), ❌ Google Trends (no reliable free API) |
| Fundamentals, estimates, earnings dates, analyst targets, short interest | Yahoo Finance | ✅ current snapshot |
| Unusual options activity | volume > max(500, 2×open interest) from chains | ⚠️ approximation; no paid flow data |

## Risk rules (enforced in code)
1% max loss per trade · 5% max total open risk · 15% max options exposure · 15% max single position.
Long options and debit spreads count their full premium as risk.

## Paper trading & review
Every recommendation is logged with date, ticker, trade, entry, stop, target, size, reason, catalyst, setup and
regime. `paper update` marks positions to market and closes them at stops, targets or expiry. `paper stats`
reports win rate, average win/loss, profit factor, expected value, drawdown, return, Sharpe (once there are
enough data points), and results by setup, regime, instrument and direction. A review is flagged every 20 closed trades.

## Setup
```bash
uv venv --python 3.12 .venv   # or: python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```
