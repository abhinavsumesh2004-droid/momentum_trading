# Momentum Trading — Build Plan
*Personal systematic momentum strategy for Indian equities (NSE/BSE) — designed for a working professional with limited time, no intraday, monthly rebalancing preference.*

---

## 1. Strategy Philosophy

- **Style:** Pure momentum (price-based, systematic), NOT swing trading, NOT discretionary fundamental picking.
- **Why momentum fits:** Low time commitment (once-a-month decisions vs weekly/daily), rules-based (removes emotional override), well-documented academic factor (Jegadeesh & Titman 1993 onward), matches a 1–5 month holding horizon.
- **Why not pure fundamentals:** Past attempts using tips-first + fundamental research produced early gains that reversed on longer holds — root cause diagnosed as (a) late entry (tips surface after price already moved) and (b) no defined exit rule (fundamentals "still look fine" on paper, so no trigger to sell a falling stock).
- **Why not swing trading:** Fortnightly rebalancing + fixed stop-loss = high turnover, high STCG tax drag (20% flat, no exemption below ₹12L income threshold — see Tax Notes), more weekly time commitment, and fixed stops shake out normal momentum volatility rather than genuine trend breaks.
- **Current approach:** Momentum scan first (universe + rank) → light fundamental quality gate second (reject red flags, don't build conviction) → entry/exit decisions driven by momentum rules only, not fundamentals.

---

## 2. The Screen (Stock Selection Rules)

1. **Universe:** Nifty 200 or Nifty 500 (avoid illiquid small caps). Full list to be sourced from NSE archives, not just a sample.
2. **Ranking signal:** 6-month price return (12-month as secondary/alternative). *Planned upgrade:* volatility-adjusted momentum score = period return ÷ volatility of daily returns over the same window (reduces bias toward one violent price spike; matches NSE's own Nifty Midcap150 Momentum 50 methodology).
3. **Trend filter:** Current price > 200-day moving average (avoid buying momentum in a downtrend).
4. **Diversification cap:** Max 2–3 stocks per sector in the final shortlist.
5. **Fundamental quality gate (light-touch, binary pass/fail — not a ranking input):**
   - Debt-to-equity reasonable for sector
   - No promoter pledging / declining promoter holding
   - Positive or stable earnings growth (not spectacular — just not declining sharply)
   - No governance red flags (auditor resignations, related-party transaction issues)
   - Keep this to ~10-15 min per stock, 4-5 fixed metrics — do NOT let this creep back into deep research (that was the old failure mode).
6. **Liquidity/turnover filter (planned addition, cautious):** Prefer lower trading-turnover stocks (avg daily value traded ÷ market cap) among momentum-ranked candidates as a soft filter — high turnover correlates with speculative/retail-driven moves that crash harder. Treat as soft preference, not hard cutoff; likely overlaps somewhat with the fundamental gate.
7. **Shortlist size:** Top 15–20 stocks after all filters, equal-weighted to start (volatility-weighting is a later upgrade once comfortable).

### What was deliberately excluded
- **RSI as a ranking/exit signal** — RSI is a mean-reversion tool; using "overbought" as a sell trigger cuts winning momentum trades early. At most, use as a very soft caution flag for new entries (e.g., RSI > 85).
- **MACD, Stochastic, Bollinger Bands** — short-term timing tools, low value-add for a monthly systematic approach.
- **Candlestick patterns** — minor entry-timing confirmation only (3-4 patterns: engulfing, hammer, doji), not a decision driver. Low ceiling for this strategy type.

---

## 3. Rebalancing & Exit Rules

- **Rebalance frequency:** Monthly, same calendar date each month.
- **Weekly observation checkpoint:** Look at holdings weekly for learning/pattern-recognition only — no trading action based on this.
- **Exit rule #1 — Rank-based (primary, checked at each monthly rebalance):** Sell if stock falls out of top ~30 rank. Catches *relative* weakening even before the stock's own price has visibly declined (since rank is relative to the whole universe).
- **Exit rule #2 — Trend-based:** Sell if price closes below its 200 DMA.
- **Exit rule #3 — Trailing stop from peak (planned addition, checked weekly, not just monthly):** Exit if price falls 15–20% from its highest close since entry (not from entry price). This catches sharp single-stock reversals that a 6-month-averaged rank score reacts to too slowly. Optionally upgrade to an ATR-based ("chandelier") stop that adapts to each stock's own volatility instead of a flat %.
- **Rejected exit method:** Fixed % stop-loss on a fortnightly cycle — too tight, shakes out normal momentum volatility, turns the strategy into swing trading with much higher tax/cost drag.
- **Why two exit rules together matter:** Rank-exit catches relative bleed early but is slow on sudden absolute drops (diluted by 6-month averaging). Trailing stop catches sudden drops fast but doesn't care about relative weakening. They cover each other's blind spots.

### Risk rules to set before going live (fill in your own numbers)
- Max position size per stock: ____%
- Trailing stop trigger: ____% from peak
- Portfolio-level circuit breaker: if Nifty 500 falls below its own 200 DMA → reduce position count / pause new buys

---

## 4. Sector / Top-Down Layer

- **Sector mapping source of truth:** NSE's own sectoral index constituent lists (Nifty IT, Nifty Bank, Nifty Auto, Nifty Pharma, Nifty FMCG, Nifty Metal, Nifty Energy, etc.) — more authoritative than third-party classifications. Fallback: Yahoo Finance `.info` sector/industry field for anything not covered.
- **Sector trend detection (automatable):** Run the same momentum math one level up — pull historical prices for sector indices themselves (Yahoo tickers like `^CNXIT`, `^NSEBANK`, `^CNXAUTO`, `^CNXPHARMA`, `^CNXFMCG`, `^CNXMETAL`, `^CNXENERGY`), compute 6-month return, rank sectors objectively instead of by gut feel.
- **How to use it:** Start as a **soft/display layer only** (context column on the dashboard) rather than a hard filter — avoids adding another concentration axis on top of stock-level bets. A harder version (only shortlist stocks from top-half-ranked sectors) is a possible later escalation, not a starting point.
- **Market regime check (top-down, higher priority than sector rotation):** Is Nifty 500 itself above its own 200 DMA? Momentum strategies historically perform poorly in downtrending/choppy broad markets — this is a genuine "reduce exposure" signal, not just a nice-to-have.

---

## 5. Data Sources

| Data | Source | Notes |
|---|---|---|
| Full stock universe | NSE archives (nseindia.com) | Free CSV; refresh every few months |
| Daily OHLC price history | Yahoo Finance (`yfinance`) | **Not real-time** — Yahoo explicitly labels NSE quotes as delayed. Fine for daily-close/monthly-rebalance use case. `yfinance` is unofficial (scrapes Yahoo), has known intermittent data-quality issues on some NSE tickers — spot-check shortlist against Screener.in or NSE bhavcopy before acting. |
| Sector mapping | NSE sectoral index constituent lists | Authoritative; Yahoo `.info` as fallback |
| Sector index price history | Yahoo Finance sector index tickers | Same return/rank math as stocks |
| Basic fundamentals | Yahoo Finance `.info` | Directional only, inconsistent for Indian small-caps; Screener.in has better fundamental data but no free API |
| Manual screening tool (non-coded alternative) | Screener.in | Sort Nifty 500 by 6M return, filter price > 200 DMA — usable without any code |

---

## 6. Technical Build (Automation Stack)

### Pipeline architecture
```
stock universe (CSV/DB) → Extract (prices, fundamentals) → Transform (returns, DMA, rank, sector join, filters) → Load (DB) → Power BI / n8n
```

### Database (Postgres, not SQLite — decided for n8n + Power BI native connectivity)
Suggested tables:
- `stocks` — ticker, name, sector, isin (static reference, updated occasionally)
- `price_history` — ticker, date, close (indexed on ticker+date)
- `sector_index_history` — sector name, date, close
- `screen_runs` — run_date, ticker, return_pct, volatility_adj_score, dma, above_dma, momentum_rank, turnover, fundamental_pass, sector_rank, shortlisted
- `portfolio_log` — actual trades: entry/exit date & price, qty, running peak price (for trailing stop calc — this state lives here, not in the stock screen, since it's specific to *when you entered*)

### Orchestration
- **n8n** for scheduling: Schedule Trigger (monthly, rebalance date) → Execute Command (runs Python pipeline) or native HTTP Request/Code nodes → Postgres node to write results → optional Telegram/Email notification with the shortlist.

### Dashboard (Power BI)
Suggested pages:
1. Current shortlist (table + bar chart of top picks)
2. Sector breakdown (confirms sector cap is working)
3. Rank history over time (line chart per ticker — spot "falling out of favor" trends)
4. Screen funnel (universe → above-DMA → fundamental-pass → shortlisted, as a funnel chart)
5. *(Future)* Portfolio Log vs theoretical screen performance comparison

### Build order (recommended sequence)
1. Full stock list + sector mapping → Postgres (static, low-risk start)
2. Price history extraction for a handful of stocks, manually verify against a trusted source
3. Return/DMA/rank transform logic on the small set, sanity-check by hand
4. Scale to full universe once logic is verified
5. Add sector trend ranking layer
6. Wire in n8n scheduling last, after manual runs are trusted

---

## 7. Portfolio Context & Allocation

- Already holds **Nifty Midcap150 Momentum 50** SIP (professionally-run momentum index, semi-annual rebalance, volatility-adjusted momentum scoring, float-market-cap weighting, sector caps).
- Adding a self-managed monthly momentum strategy is **not diversification** — it's stacking the same factor bet. Both legs will tend to draw down together in a momentum crash/market reversal.
- Current combined exposure: ~15% (MF) + ~10% (DIY) ≈ **25% of total portfolio in one factor** — worth being deliberate about this concentration level regardless of vehicle.
- Historical index data point (Nifty Midcap150 Momentum 50 vs plain Midcap 150): momentum version outperformed in 13/18 calendar years, but with higher volatility at longer horizons (21.0% vs lower for plain index at 15Y) and a real lag during sharp market recoveries (e.g., 2009: +87.95% momentum vs +113.87% plain Midcap150) — a known momentum-crash pattern at market turning points.
- **Honest framing of the DIY project's edge:** unlikely to out-execute the professionally-run fund on the *same* mechanics (semi-annual, volatility-adjusted, float-weighted). Where a hands-on version could plausibly add value: faster reaction via monthly rebalancing traded off against higher turnover/tax cost, a trailing-stop-from-peak mechanism the fund doesn't have (only checks twice a year), and a turnover/liquidity filter as an additional layer.
- Primary stated motivation: **seeking alpha via a new strategy, with learning as a bonus** (not purely an educational exercise) — worth revisiting position sizing with this in mind given the concentration point above.

---

## 8. Tax Notes (India-specific, current as of mid-2026)

- STCG on equity (holding < 12 months): flat 20% (Section 111A).
- **Section 87A rebate does NOT apply to STCG/LTCG from equity**, even if total income is below the ₹12L rebate threshold — the rebate only offsets tax on slab-rate (salary) income, not special-rate capital gains income. This applies regardless of income level.
- Higher rebalancing frequency = more taxable events = more STCG drag, independent of income bracket. This was a key reason for preferring monthly over fortnightly rebalancing.
- Fund-based momentum exposure (SIP) defers taxation to redemption; DIY monthly rebalancing creates a taxable event on every single exit.

---

## 9. Open Items / Next Steps

- [ ] Source full Nifty 200/500 universe list (not the 50-stock sample)
- [ ] Add volatility-adjusted momentum score to ranking logic
- [ ] Add trailing-stop-from-peak logic (needs running-peak-price state in Portfolio Log)
- [ ] Add turnover/liquidity soft filter
- [ ] Build NSE sectoral-index-based sector mapping (replace manual CSV tagging)
- [ ] Add sector trend ranking as a display-only dashboard layer
- [ ] Set concrete risk rule numbers (max position size %, trailing stop %, market-regime circuit breaker)
- [ ] Decide DIY allocation size relative to existing Momentum 50 MF exposure (revisit the 25%-in-one-factor concentration point)
- [ ] Migrate/build pipeline: Extract → Transform → Load → Postgres → n8n schedule → Power BI dashboard
- [ ] Paper trade (or small real capital) for at least one full monthly cycle before scaling up

---

*Compiled from an iterative strategy design conversation. Written to be portable — usable as context for any AI model or as a personal reference doc while building.*
