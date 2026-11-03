━━━━━━━━━━━━━━━━━━━━━━
EVENING WATCHLIST: prepared for Tuesday, November 3, 2026 (Election Day)
*Research and paper trading only. Not financial advice. Written 2026-11-02 ~21:30 ET, after the close.*
*Labels: **[DATA]** verified but delayed (Yahoo daily bars / FRED / SEC) · **[NEWS]** cited report · **[BACKTEST]** historical simulation (biased: today's universe, no costs) · **[MODEL]** forecast or judgment.*
*No trades are entered tonight. Every setup below is conditional on the triggers after Tuesday's open.*
━━━━━━━━━━━━━━━━━━━━━━

DATA QUALITY NOTE
- **[DATA]** Yahoo's Nov 2 daily bar arrived with volume but blank prices for every stock. It was rebuilt from 5-minute bars (222/222 tickers; regular session). Yahoo's daily volume was kept. Rebuilt closes match Yahoo's last-price quotes within ~0.05% (SPY 752.14 vs 752.10; PFE 30.70 vs 30.67).
- An earlier scan run on the raw data showed "breadth 0%, no setups". That was this data error, not the market. It was discarded and the code now repairs or flags this case automatically.

PAPER ACCOUNT
- **[DATA]** Equity **$29,886.95** · cash 100% · open risk $0.
- Trade #1 NVDA (long 19 @ 236.47) **stopped out 2026-10-08 at 230.52: −$113.05 (−1.0R)**. NVDA closed Oct 30 at 204.64, so the stop prevented a much larger loss.
- The desk did not run between Oct 8 and Nov 2 (the scheduled review was stuck on a permission prompt). This is a process failure, not a market one.

MARKET REGIME
- **[DATA]** SPY 752.14 (−0.5% d): below its 20-day (769) and 50-day (767), above its 200-day (726); RSI 38. Breadth: 59% of the universe above its 50-day.
- **[DATA]** QQQ 681.13: RSI 32, sitting on its 200-day (679). The Nasdaq-100 is ~10% off its peak (technical correction). SMH 508: −15.4% in 1 month, below its 200-day (520), RSI 34.
- **[DATA]** VIX 21.15 vs VIX3M 21.60 (barely in contango; +38% in 1 month). 10Y 4.89% (−38 bp in 1 month). WTI $96.55 (−7.3% d). HY spread 3.44% (up from 3.26%). Fed proxy: 2Y − fed funds +0.68 pp → **hikes priced**.
- **[DATA]** Sector leaders over 1 month: Energy, Healthcare, Staples, Materials, Utilities. Laggards: Semis (−15.4%), Communication, Tech. This is defensive rotation out of AI/tech.
- Rule model: **NEUTRAL** (score 1). Composite: **Bull · Mixed risk · Normal volatility** (trend still above the 200-day; risk signals mixed).
- **[MODEL]** My read: late-cycle rotation, not a broad bull. Tech leadership is breaking, defensives lead, and event risk is heavy this week. Confidence: medium.

CATALYSTS
| When (ET) | Event | Why it matters |
|---|---|---|
| Tue pre-market | **Pfizer (PFE)** and **Marathon Petroleum (MPC)** Q3 earnings | Both are on the screener list, so binary gap risk |
| Tue 10:00 | JOLTS (Sep), consensus ~7.06M | Labor cooling vs hikes priced |
| Tue all day / overnight | **US midterm elections**, results after the close | Binary overnight risk for anything held through Tuesday's close |
| Tue after close | **AMD** earnings | Semis sentiment after a −15% month |
| Wed | ADP, ISM services; Qualcomm, AppLovin earnings | |
| Fri 8:30 | **October jobs report**, consensus ~55k, unemployment 4.2% | The week's main macro event |
Sources: CNBC week ahead (Oct 30), CMC Markets, Yahoo Finance and Reuters (Nov 2), TipRanks and 24/7 Wall St. (earnings dates).

Overnight news recorded as events #6–#10:
- Oil −6–7% on reports of renewed US-Iran talks.
- Marvell to buy Lumentum (~$115B); MRVL −12.7%.
- Apple fell after hours on a weak margin forecast.
- ISM manufacturing beat (55.5 vs 54.6).
- Midterms Tuesday.

WHAT THE DESK'S MEMORY SAYS
- **[BACKTEST]** On the **30 historical days most similar to today**, `rs_leader` signals averaged **−0.40R (23% wins, n=65)**. That is statistically negative, though from a biased backtest. In the same regime label (Bull/Mixed), rs_leader is +0.06R (n=89, not proven) and pullback is +0.03R (n=46, not proven). **No setup type has a proven edge in conditions like these.**
- **[BACKTEST]** Breakdown shorts in this label: −0.41R (n=15, insufficient). Experiment X1 is testing whether breakdown shorts lose money.
- Paper record so far: 1 trade, −1R. Too small to mean anything.
- Logged process errors to avoid repeating: E1 (enter or cancel at the open, never drift); E2 (list the strongest contradictory evidence for every idea).

SCREENER OUTPUT AND MY TRIAGE (levels from Nov 2 closes; refreshed after the open)
| Ticker | Setup | Status | Why |
|---|---|---|---|
| **TMO** | rs_leader + laggard in strong sector | **WATCH (A)** | Not extended (RSI 53, at its 20-day), above the 50-day; earnings Jan 28 |
| **MRK** | laggard in strong sector | **WATCH (A)** | RSI 55, just above its 20/50-day; reported Oct 29, so no earnings in the window |
| **ABBV** | rs_leader | **WATCH (B)** | RSI 62, above its 20-day; reported Oct 30 |
| **GILD** | rs_leader | **WATCH (B)**, breakout only | RSI 74 and +9% above its 20-day; needs a clean break above 172.6 / 175.7 |
| **ELV** | pullback | **WATCH (B)** | RSI 47, between its 20-day (423) and 50-day (410) |
| REGN | breakout + rs_leader | **AVOID chasing** | RSI 82, +20% above its 20-day, +19% in 5 days. Only on a pullback that holds ~927 |
| LLY | rs_leader | **AVOID chasing** | RSI 76, +18% above its 20-day, above the mean analyst target |
| MAR | post-earnings breakout | **AVOID** | Closed 416 after a 433 high: a long upper wick (selling into the breakout) |
| PFE | breakout + rs_leader | **NO pre-earnings entry** | Reports Tuesday pre-market; reassess after the reaction settles (≥ 10:30) |
| PSX, MPC, CVX, COP, OXY | energy pullbacks / leaders | **AVOID** | Oil −7% on Iran talks contradicts the long thesis; MPC reports Tue, COP Thu |
| RDDT (short) | breakdown | **REJECT** | RSI 17 after −34% in 5 days (snap-back risk), 10.5% ATR, 15.8% stop distance, negative breakdown backtest |

**Bullish vs bearish:**
- Every qualifying bullish setup is in **healthcare**, so the sector cap applies: **at most 2 positions from the list**.
- The only bearish setup (RDDT) fails on risk. Tech and semis are weak (QQQ on its 200-day, SMH below it), but they are oversold into earnings and the election. **No bearish trade qualifies tonight.**
- **[MODEL]** If QQQ closes clearly below its 200-day (~679) with breadth falling, bearish setups may appear later in the week.

ENTRY TRIGGERS FOR TUESDAY (paper; evaluate at ≥ 10:00 ET, never in the first 30 minutes)
- **General gate:** take nothing if SPY is below 745 at 10:00 (Monday's low was 751.6), VIX is above 23, or VIX rises above VIX3M.
- **TMO:** above 682 and VWAP at 10:00, ideally reclaiming 690. Stop **653.04** (1.5 ATR). Target 1: **741.87** (2R). Target 2: the 711.85 20-day high is the first resistance; scale there.
- **MRK:** above 148.5 and VWAP at 10:00. Stop **141.74**. Target 1: **161.99** (2R). First resistance 154.0 (20-day high).
- **ABBV:** above 289 and VWAP. Stop **278.88**. Target **308.50**. Resistance 294.9 / 298.7.
- **GILD:** only on a 15-minute close above **172.61** with volume ≥ its average. Stop = entry − 6.9 (1.5 ATR). Target = entry + 13.8 (2R).
- **ELV:** only if it holds above 406.9 (Monday's low) and reclaims 413.7. Stop **395.78**. Target **449.62**.
- **Gap rule:** if a name opens more than 1 ATR above Monday's close, skip it. No chasing.

POSITION SIZING (paper)
- **Equity $29,886.95.** The hard limit is 1% risk per trade ($298.87), 15% per position ($4,483) and 5% total open risk ($1,494).
- **[MODEL] Tuesday-only reduction:** I'll use **0.5% risk per trade ($149)** because of the negative similar-day backtest and the overnight election risk. This is a temporary choice within the limits, not a rule change.

| Ticker | Entry (ref) | Stop | Risk/share | Shares at 0.5% | Position $ | Risk $ | Reward/risk |
|---|---|---|---|---|---|---|---|
| TMO | 682.65 | 653.04 | 29.61 | 5 | 3,413 | 148 | 2.0 |
| MRK | 148.49 | 141.74 | 6.75 | 22 | 3,267 | 149 | 2.0 |
| ABBV | 288.75 | 278.88 | 9.87 | 15 | 4,331 | 148 | 2.0 |

- **Costs:** $0 commission. Slippage assumed 0.05% per side (≈ $3–4 per position); SEC/TAF fees apply to sells only (cents).
- **Maximum combined risk if 2 trigger:** ~$298 (1.0% of equity). Cash stays ≥ 70%.

PRELIMINARY DECISION: **HOLD CASH unless Tuesday's triggers confirm; at most SELECTIVE (≤ 2 healthcare positions at half size).**
**What would change it:**
- A broad rally with QQQ holding its 200-day and the VIX falling below 19 → normal size allowed.
- SPY below 745 or the VIX term structure inverting → **HOLD CASH**.
- A PFE or MPC earnings shock spilling into the sector → reassess healthcare or energy.

The full report follows Tuesday after 10:00 ET, with live triggers, scorecards, predictions and any paper entries at real timestamped prices.
