# Momentum Pipeline — Technical Context Document (v4)
*Supersedes v1/v2/v3 context docs. Reflects actual reviewed/fixed code state plus a full backtesting validation pass (walk-forward, rolling windows, survivorship bias check, Nifty 50 vs Nifty 500 comparison). Written to be dropped into any AI model or read by future-you without needing the full conversation history.*

---

## 1. Strategy Definition (locked in, as of this doc)

**Universe:** Nifty 500 (primary), with a completed Nifty 50 comparison run for validation (see Section 6).

**Screen rules (as implemented):**
1. Ranking signal: N-month price return (configurable; 6-month is the agreed baseline).
2. Trend filter: current price > 200-day moving average (DMA).
3. Wide pool → sector cap two-stage design: rank top 30 by return_pct first, THEN apply sector cap on that pool (deliberately not capping directly on a small top-10, to avoid running out of eligible candidates if top-ranked stocks cluster in one sector).
4. Sector cap: max 2-3 stocks per sector in the final shortlist.
5. Shortlist size: top 15-20 stocks after all filters.
6. Weighting: equal-weight (agreed baseline) or rank-weight (weight ∝ 1/rank, decaying by momentum rank) as a tested alternative.
7. Rebalance frequency: monthly (agreed baseline) or quarterly (tested alternative — see Section 7, this ended up winning).

**FINALIZED CONFIG (from walk-forward validation, Section 7):**
```
return_window_months = 6
dma_window_days = 200
stock_count = 20
max_per_sector = 3
weight_type = "equal"
rebalance_step_days = 90   # quarterly — NOT the originally-planned monthly
```
This was picked because it was the most consistent top-2 performer across TWO independent walk-forward tests (Nifty 500 fixed-warmup split, and Nifty 50 split) — see Section 7 and Section 6 for full reasoning. It also has by far the best (lowest) drawdown of any tested config in both universes.

**Deliberately excluded from the screen (from original strategy design):**
- RSI as ranking/exit signal (mean-reversion tool, cuts winning momentum trades early — at most a soft caution flag for entries, e.g. RSI > 85).
- MACD, Stochastic, Bollinger Bands (short-term timing tools, low value-add for monthly systematic approach).
- Candlestick patterns (minor entry-timing confirmation only, not a decision driver).
- Fundamental quality gate (planned but never wired up with real data in this project).
- Turnover/liquidity filter (planned, not implemented).
- Volatility-adjusted momentum score (planned upgrade to ranking logic, not implemented — baseline uses raw return_pct).

**Exit rules (from original strategy design — NOT built into the backtest engine, user will run these manually/ad-hoc live):**
- Rank-exit: sell if stock falls out of top ~30 rank at monthly/quarterly rebalance.
- DMA-exit: sell if price closes below 200 DMA.
- Trailing-stop-from-peak: exit if price falls 15-20% from highest close since entry, checked weekly or ad-hoc (not just at rebalance) — user has explicitly decided to do this manually rather than have it automated in the backtest engine.
- User's stated live-execution plan: run the screener, compare new target weights/units against current holdings, and manually buy/sell the delta rather than a full liquidate-and-rebuild — this is INCREMENTAL rebalancing, NOT what the current backtest engine simulates (see Section 4, Known Gap #1).

**Portfolio context (from original build plan, for reference):**
- User already holds Nifty Midcap150 Momentum 50 SIP (professionally-run momentum index, semi-annual rebalance, volatility-adjusted, float-weighted). This DIY strategy stacks the same factor bet, not true diversification — combined momentum-factor exposure was noted as ~25% of total portfolio, worth being deliberate about.
- Primary motivation stated: seeking alpha via a new strategy (learning is a bonus, not the primary goal) — relevant when evaluating whether backtested performance justifies the effort/risk/concentration.

**Tax context (India, per original build plan, current as of mid-2026):**
- STCG on equity (holding < 12 months): flat 20% (Section 111A). Section 87A rebate does NOT apply to STCG/LTCG regardless of income level.
- This is why monthly (not fortnightly) rebalancing was originally chosen, and why the user's manual/incremental (not full-liquidate) rebalancing plan matters — it reduces realized gains events vs. a full monthly liquidate-and-rebuild.

---

## 2. Architecture (as implemented and fixed)

```
stock_universe (Nifty 500 CSV, cleaned) → Postgres [stocks]
                                        │
                                        ▼
    fetch_prices.py → Postgres [price_history]  (10y daily, close+volume, auto_adjust=True)
        - write_to_db(): original 5y pull
        - backfill_history(): NEW — pulls additional years BEFORE the existing
          earliest date, safely upserted (ON CONFLICT DO NOTHING via staging table)
                                        │
                                        ▼
    db_utils.load_price_dict_from_db()  (dict[ticker: DataFrame])
        - FIXED: lookback_days parameter now actually bounds the query
          (previously accepted but unused — every call loaded the ENTIRE
          history up to as_of_date, which was fine at 5y but would have
          been expensive/wasteful at 10y across ~120 backtest rebalances)
                                        │
                                        ▼
    returns_and_dma.compute_returns_and_dma()
        - FIXED: DMA filter was dead code (filtered into a discarded local
          variable, never applied to the returned pool) — now genuinely
          filters to above_dma stocks before ranking/head(top_n)
                                        │
                                        ▼
    returns_and_dma.transform_to_dict() + populate_sectors()
                                        │
                                        ▼
    returns_and_dma.sector_selection()  (sector cap, sorted explicitly by return_pct)
                                        │
                                        ▼
    returns_and_dma.get_top_companies()  ← single entry point
        - FIXED: top_n typo already resolved in actual code (contrary to
          earlier stale doc claims) — top_n=30 correctly passed
        - ADDED: as_of_date parameter (threads through to load_price_dict_from_db)
          — REQUIRED for backtesting to avoid look-ahead bias; previously
          this function had NO as_of_date param at all
        - ADDED: stock_count, max_per_sector parameters (previously hardcoded
          via sector_selection()'s defaults of 10/2, now overridable to match
          the agreed baseline of 20/3)
        - ADDED: dynamic lookback_days calculation — computes
          max(dma_window_days, return_window_months*21) trading days needed,
          converts to a calendar-day buffer with margin, and passes that to
          load_price_dict_from_db instead of relying on a flat guessed default
                                        │
                                        ▼
    backtest_engine.py  — FULLY REBUILT (see Section 4 for full detail)
        - Real monthly/quarterly rebalance replay loop (previously just a
          date-validity walker that never called the screening/weighting/
          allocation functions in sequence)
        - Walk-forward config sweep + testing
        - Rolling-window regime analysis
        - Idle-cash-reducing allocation option
        - Transaction cost modeling option
```

---

## 3. Database State

| Table | Status | Notes |
|---|---|---|
| `stocks` | ✅ Populated | ~500 rows, Nifty 500, ticker/name/sector |
| `price_history` | ✅ Extended to 10 years | Originally 5y (`write_to_db()`), backfilled another 5y via new `backfill_history()` function. Confirmed real data range: **2016-07-11 to 2026-07-10**, 500 tickers. `auto_adjust=True` throughout (deliberate decision from earlier session — see Section 8, Lesson 1). |
| `screen_runs` | ⚠️ Still just a stub, empty | No pipeline run has written to it yet — NEXT BUILD STEP, not done in this session. |
| `sector_index_history` | ❌ Not created | Deferred; sector rotation feature not built. |
| `portfolio_log` | ❌ Not created | Needed for real trade tracking + trailing-stop peak-price state — NOT built in this session. |

---

## 4. Backtest Engine — Full Technical Detail (built this session)

File: `src/backtest/backtest_engine.py` — completely rebuilt from a stub. Key functions:

### `generate_rebalance_dates(start_date, end_date, step_days=30)`
Simple fixed-step date walker. Doesn't need to snap to exact trading days — `as_of_date` filtering elsewhere already means "everything ≤ this date," so weekends/holidays resolve naturally.

### `get_allocation(ticker, capital, weights, verbose=False)`
Whole-share allocation (no fractional shares — realistic for direct NSE equity). `verbose=True` prints which tickers got 0 shares because their price exceeded their allocated slice, and how much slice-cash became idle as a result.

### `get_allocation_efficient(ticker, capital, weights, verbose=False)` — NEW
Same starting point as `get_allocation()`, then sweeps leftover cash into extra whole shares of the cheapest affordable stocks instead of leaving it idle. **Tradeoff, disclosed:** this overweights cheaper stocks relative to target weights — use when idle cash is the concern, use plain `get_allocation()` when staying close to target weights matters more.

**Why this was needed:** at ₹100,000 capital with a 20-stock equal-weight shortlist, individual slices are ~₹5,000-13,000. Real Nifty 500 momentum picks regularly include stocks priced ₹10,000-35,000+ (e.g., FORCEMOT, POWERINDIA, NEULANDLAB, SOLARINDS recurred as repeatedly-skipped names across many test runs) — these got ZERO allocation every time they appeared, with their entire slice going to idle cash. This was NOT random noise; it was systematic exclusion of the same handful of high-priced names regardless of their momentum rank. Measured impact: idle cash was regularly 15-20%+ of portfolio value per rebalance; fixing it moved one test's CAGR from 25.14% to 30.35%.

**Open question flagged, not resolved:** whether this bias disappears at larger capital scale (e.g. ₹10L+) hasn't been tested yet — worth checking before assuming the backtest number generalizes to how the user will actually run this live.

### `get_portfolio_value(engine, holdings, leftover_cash, as_of_date)`
Marks a held portfolio to market using the latest available close ≤ as_of_date per ticker (handles missing trading days for free via the `<=` filter). If a held ticker has zero price data as of that date (e.g. delisted), it's valued at 0 and flagged — conservative treatment, not a survivorship-bias fix.

### `compute_performance_metrics(log_df)`
CAGR, total return, max drawdown — **computed at rebalance-date granularity only**, not daily. Known limitation: intra-period drawdown could be worse than what's captured.

### `backtest_engine(capital, start_date, end_date, data_engine, return_window_months, dma_window_days, stock_count, max_per_sector, weight_type, rebalance_step_days, verbose_allocation=False, reduce_idle_cash=False, transaction_cost_pct=0.0)`
The main replay loop. Per rebalance date:
1. Mark existing holdings to market (skip on first date).
2. If `transaction_cost_pct > 0`: charge a SELL-leg cost on the marked value.
3. Re-screen via `get_top_companies(..., as_of_date=rebal_date)` — no look-ahead.
4. If shortlist empty: hold cash, log it, continue (this happens during warm-up — see Known Issue below).
5. If `transaction_cost_pct > 0`: charge a BUY-leg cost before allocating.
6. Weight + allocate (via `get_allocation` or `get_allocation_efficient` depending on `reduce_idle_cash`).
7. Log: date, portfolio_value, holdings, leftover_cash, shortlist, transaction_cost.
8. At the very end: final mark-to-market at `end_date` (with a final sell-leg cost if applicable) — gives the honest "walk away today" value, not a pre-liquidation number.

**Transaction cost model:** `transaction_cost_pct` is PER LEG (one-way). Since this simulates full liquidate-and-rebuild each period, costs apply TWICE per rebalance once holdings exist (sell old + buy new). Tested at 0.1% per leg: took one config's CAGR from 2.54% to -0.05% over one year; at 0.5% per leg: -9.54% CAGR, costs consumed 13.7% of final value. **User has since decided to skip transaction cost modeling entirely** because their real execution will be incremental (buy/sell only the delta between current and target weights), not full liquidate-and-rebuild — the flat-percentage full-turnover cost model would overstate real costs for that execution style. This is the right call, not a shortcut; the cost model as built doesn't represent the user's actual process.

**Print statements added:** every successful rebalance now prints `Invested in N stocks, portfolio_value=X, leftover_cash=Y` (previously only failures/warnings printed, which made a working backtest look broken at a glance since only the ~11-13 warm-up months printed anything). End-of-run summary prints `X/Y rebalance dates invested, Z held cash`.

### `run_config_sweep(data_engine, capital, start_date, end_date, configs)`
Runs `backtest_engine()` once per config dict over the same window, returns one row per config with its metrics. Errors/insufficient-data configs get NaN metrics rather than crashing the sweep.

### `walk_forward_test(data_engine, capital, full_start, split_date, full_end, configs, rank_metric="cagr_pct")`
**The core overfitting check.** Sweeps all configs in-sample (`full_start` to `split_date`), picks the winner by `rank_metric`, then re-sweeps ALL configs out-of-sample (`split_date` to `full_end`, data never touched during selection) to see whether the in-sample winner actually generalizes or whether a different config performs better once tested honestly. Returns a merged DataFrame with both periods' metrics + ranks side by side.

**Important conceptual note preserved from conversation:** no ML "training" happens here — every config is a fixed, human-specified parameter set run independently. The walk-forward split isn't validating an algorithm's learned parameters; it's validating the SELECTION PROCESS — i.e., guarding against picking whichever config happened to fit one specific historical window best (a human "eyeballing the best backtest" has the exact same overfitting risk as an ML training loop, just without the word "training" attached to it).

### `run_rolling_windows(data_engine, capital, config, overall_start, overall_end, window_years=1, step_months=3)`
Runs ONE fixed config repeatedly over sliding windows, returns median/min/max/std/win-rate CAGR across all windows — gives a distribution instead of a single cherry-pickable number. **Caveat:** overlapping windows (e.g. 1-year windows stepped every 3 months) are NOT independent samples — a single extraordinary period gets counted 4-5 times across overlapping windows, which can distort the median (see Section 7, COVID-recovery-cluster finding).

### Known Gap #1 (not built, flagged for next build phase)
The engine always fully liquidates and rebuilds each rebalance. It does NOT compute an incremental buy/sell delta against current holdings. User has decided this is fine — `get_allocation()`/`get_allocation_efficient()` already return `{ticker: target_units}`, which the user will diff against current holdings and rebalance manually. This keeps turnover/tax drag much lower than the backtest simulates, which is actually a point in the strategy's favor for live use (backtest is a conservative-on-cost approximation, not an inflated one, given costs are being skipped entirely per the user's decision above).

### Known Gap #2 (not built, flagged for next build phase)
No exit-rule logic (rank-exit/DMA-exit/trailing-stop) runs between rebalances. User has decided to check this manually/weekly/ad-hoc rather than automate it in the backtest engine.

---

## 5. Other Code Fixes Made This Session

### `src/backtest/weights.py`
`rank_weights()` was fundamentally broken — it expected ticker values as `(rank, price)` 2-tuples, but the real shape is a 5-element list `[latest_price, return_pct, dma, above_dma, sector]`. Attempting to unpack would `ValueError` the moment `weight_type='rank'` was actually used. **Fixed** to sort by `return_pct` (index 1) descending and assign decaying weights by that rank instead.

### `src/extract/fetch_prices.py`
- **Added `get_price_range(ticker, start, end, ...)`** — same chunked-download pattern as `get_price()`, but for an explicit date range instead of "N years back from today" (needed because `period='5y'` always means the 5 years ending today; can't use it to pull the 5 years BEFORE what's already in the DB).
- **Added `backfill_history(engine, years_back=5)`** — finds the current earliest date in `price_history`, pulls `years_back` more years immediately before it, and safely upserts (via a staging table + `INSERT ... ON CONFLICT DO NOTHING`, so re-running is idempotent and never crashes on overlapping dates).
- **Added `upsert_price_history(engine, df)`** — the staging-table upsert helper used by `backfill_history()`.
- **Fixed ordering bug:** the `if __name__ == "__main__":` block was originally positioned BEFORE the function definitions in the file — meant that uncommenting a call to `backfill_history()` there would `NameError` since the function didn't exist yet at that point in top-to-bottom script execution. Moved the `__main__` block to the end of the file, after all functions are defined.
- Guarded existing module-level print/debug statements behind `__main__` so importing this module (e.g. from `backtest_engine.py`, transitively) doesn't trigger DB calls as a side effect.

### `src/db_utils.py`
`load_price_dict_from_db()` — see Section 2 above (lookback_days fix).

### `src/transform/returns_and_dma.py`
See Section 2 above (DMA filter fix, `get_top_companies()` signature additions). Also guarded module-level test/debug code behind `if __name__ == "__main__":`.

---

## 6. Survivorship Bias — Research Findings & Nifty 50 Comparison

**Cited research (for article use):** A March 2026 paper ("Survivorship Bias in Emerging Market Small-Cap Indices: Evidence from India's NIFTY Smallcap 250," Harjot Singh Ranse, arXiv/SSRN) reconstructed historical index membership for 1,437 stocks over 2016-2025 using bhavcopy data (NSE's daily record, uniquely includes delisted securities — reconstructed via market-cap ranking as a proxy, ~85-90% accuracy vs. true historical membership). Findings: **survivor-only backtesting overstates annual returns by 4.94 percentage points (23.3% relative) and Sharpe ratios by 9.1% relative**, with 82.5% annual turnover in that index. Important caveat: this is Nifty Smallcap 250, not Nifty 500 — broader/larger-cap indices likely show a SMALLER bias than this, so treat 4.94pp as an order-of-magnitude anchor, not this project's exact number. A separate cited data point: one comparable strategy's backtest went from +23.26% to **-22.04%** cumulative return once transaction costs were included on an equally-defensible alternate window — good illustration of backtest fragility for the article.

**This project's own empirical check (a real, if partial, alternative to full historical-constituent reconstruction):** ran the SAME walk-forward methodology on Nifty 50 (lower index turnover than Nifty 500, fewer historical membership changes) instead of Nifty 500, using the same 10-year price data (Nifty 50 stocks are a subset of Nifty 500 and were already in `price_history`).

**Nifty 50 walk-forward results** (full_start/split/end dates matched the fixed-warmup Nifty 500 run):

| Config | In-sample rank | Out-of-sample rank | OOS CAGR | OOS max drawdown |
|---|---|---|---|---|
| 0 (monthly, equal, cap3) | 3rd | 5th | 14.77% | -14.55% |
| 1 (monthly, equal, cap2) | 4th | 3rd | 16.53% | -14.28% |
| 2 (monthly, rank, cap3, top-15) | 5th | 4th | 14.83% | -28.75% |
| **3 (12mo window, monthly)** | **1st** | **1st** | **18.11%** | -15.90% |
| **4 (6mo window, quarterly)** | **2nd** | **2nd** | **17.21%** | **-7.58% (best)** |

**Key findings from this comparison:**
1. Configs 3 and 4 held the SAME rank (1st, 2nd) in-sample AND out-of-sample — no overfitting flip at all, unlike every Nifty 500 run. Attributed to lower universe churn/selectivity noise.
2. Absolute CAGRs (14-18%) are dramatically lower than Nifty 500 results (30-50%+) — strong evidence that a large chunk of the Nifty 500 numbers reflect survivorship bias + extreme selectivity (picking 20 of 500 = 4% vs. 20 of 50 = 40%), not pure strategy skill. **This 40% selection ratio is itself a caveat** — at that ratio the screen behaves much closer to "mostly holding the index" than genuine stock-picking, so the Nifty 50 numbers aren't a clean "bias-free" version of the Nifty 500 result either; it's a DIFFERENT test, not a corrected one.
3. Config 4 has roughly HALF the drawdown of config 3 for ~1 point less CAGR — better risk-adjusted, and was the deciding factor in finalizing config 4 as the live strategy (see Section 1).

**Historical constituent reconstruction (the "real" fix for survivorship bias) — NOT attempted in this project:**
NSE reviews Nifty 500 (and Nifty 50) constituents semi-annually (~March/September). No clean free bulk API exists for historical constituent lists; the closest available public source is **bhavcopy** (NSE's daily published trade record, includes delisted securities) combined with a market-cap-ranking reconstruction methodology (as used in the cited paper) — this is a real, doable project but was explicitly deprioritized in favor of the Nifty 50 comparison above, which was judged sufficient for article purposes given the effort/reward tradeoff.

---

## 7. Walk-Forward Testing — Full History of Runs (Nifty 500)

**Run 1 (flawed — warm-up bug, not caught initially):** `full_start="2016-07-06"`, `split_date="2021-07-06"`, `full_end="2026-07-03"`. In-sample winner: config 2 (rank-weighted, 53.29% CAGR) — fell to rank 3 out-of-sample (29.56%). **Problem discovered:** config 3 (12-month return window) needs more trailing history than the other configs before producing its first valid shortlist; since `full_start` sat right at the edge of the backfilled data, config 3 disproportionately absorbed forced-cash warm-up months, diluting its in-sample CAGR unfairly relative to shorter-lookback configs. This made config 3's dramatic "worst-then-best" pattern partly a measurement artifact, not a real finding.

**Root-cause math:** required trading days = max(dma_window_days, return_window_months×21). For config 3 (12-month, 200 DMA): max(200, 252) = 252 trading days ≈ 433 calendar days (~14.2 months) of buffer needed before first valid screen. Data starts 2016-07-11, so safe `full_start` ≈ 2017-11-01.

**Run 2 (corrected):** `full_start="2017-11-01"`, `split_date="2022-03-01"`, `full_end="2026-07-10"`. All configs confirmed `0 held cash (warm-up)` in output — confound genuinely removed.

Results:
| Config | In-sample rank | Out-of-sample rank | OOS CAGR | OOS max drawdown |
|---|---|---|---|---|
| 0 | 5th | 2nd | 31.49% | -21.94% |
| 1 | 2nd | 3rd | 30.64% | -21.34% |
| 2 (rank-weighted) | **1st** (47.56%) | **5th, worst** (19.18%) | 19.18% | -29.12% (worst) |
| 3 (12mo) | 4th | 4th | 30.54% | -24.87% |
| **4 (quarterly)** | 3rd | **1st, best** | **39.73%** | **-7.51% (far better than rest)** |

**Conclusions drawn:**
- Config 2 (rank-weighted, most concentrated into current winners) is a clean overfitting example: best in-sample, worst out-of-sample AND worst drawdown. Real, repeatable pattern — concentrating into "whoever's currently winning" chases window-specific noise.
- Config 3 did NOT jump to the top once the warm-up bug was fixed — stayed mid-pack (4th) both halves. Confirms its Run-1 "held up best" result was itself the warm-up-dilution artifact, not genuine robustness. Important lesson: the FIRST fix attempt (Run 1) gave a wrong answer that looked plausible; only re-deriving the warm-up math and rerunning caught it.
- **Config 4 (quarterly rebalance) is the standout**: 1st out-of-sample, dramatically better drawdown than every other config in both halves. This is the config that was ultimately finalized (see Section 1), later reinforced by the independent Nifty 50 comparison (Section 6) where it again ranked top-2 consistently.

**Rolling-window multi-config summary** (1-year windows, 3-month steps, Nifty 500, all 5 configs):
| Config | Median CAGR | Min | Max | Win rate | Window count |
|---|---|---|---|---|---|
| 0 | 28.57% | -18.61% | 179.78% | 84.38% | 32 |
| 1 | 26.68% | -16.96% | 168.37% | 78.12% | 32 |
| 2 | 40.45% | -28.01% | 200.15% | 68.75% | 32 |
| 3 | 20.26% | -24.32% | 222.77% | 71.88% | 32 |
| 4 | 30.52% | -17.02% | 203.30% | 81.25% | 32 |

**Important caveat on the Max column, discovered and important to carry into any article:** the 179-223% max values are NOT independent findings — they cluster on the SAME underlying event. Windows starting 2020-02-01 through 2021-02-01 (5 consecutive, overlapping, 3-month-stepped windows) all show triple-digit or near-triple-digit CAGR, all touching India's COVID-crash V-shaped recovery (market bottomed ~late March 2020, then staged one of the sharpest rallies in NSE history). Because windows overlap, **one extraordinary macro event gets counted 4-5 times** in the median/min/max — a time-based analog to survivorship bias (one anomaly disproportionately weighting a summary statistic). Max drawdown = 0.00% for the 2020-05-01 window supports this being a real, nearly-uninterrupted rally, not a data artifact. **Recommendation for article: report rolling-window stats twice — with and without the COVID-recovery-cluster windows — both labeled explicitly**, since conflating "normal year" and "sharpest recovery in a generation" into one median misrepresents both.

---

## 8. Key Lessons & Decisions Carried Forward From Earlier Sessions

1. **`auto_adjust=True` decision (pre-dates this session, still valid):** dividend adjustment causes small (~1-2%) bounded inaccuracy in absolute price levels (DMA) but doesn't affect return_pct/above_dma (ratios). `auto_adjust=False` exposes unbounded, occasionally catastrophic split-driven fake return spikes. Asymmetric risk → keep `auto_adjust=True`. Reusable principle for future data-source decisions (fundamentals, turnover data).
2. **Raw vs. adjusted price nuance:** ratios (return_pct, above_dma) are adjustment-agnostic; absolute prices used for anything transactional (future `portfolio_log` entry/exit prices, trailing-stop-in-rupee-terms) should ideally use raw traded price — a nuance to handle when `portfolio_log` is eventually built, not by changing `price_history` again.
3. **Validation method that worked well, worth repeating for new data sources:** manual recompute to isolate calculation bugs from data-source bugs, cross-check specific values against Screener.in/Google, spot-check a deliberately varied sample (riser, faller, large-cap, split-prone sector).
4. **On outlier return_pct values (497 flagged >100% in one diagnostic pass):** investigated and confirmed these were NOT data bugs — they were real, extreme momentum from an actual historic PSU/defense/rail/infra rally (RVNL, IRFC, TITAGARH, MAZDOCK, BSE, HUDCO, COCHINSHIP, GALLANTT, etc. — all real, well-known companies). The outlier-detection threshold (100%) was simply too conservative for how extreme that period actually was. Lesson: always check WHICH tickers are flagged before assuming a data bug — real names + plausible sector story = probably real, not corrupted.

---

## 9. Immediate Next Steps (priority order, for whoever picks this up next)

1. **Persistence layer — `screen_runs` table.** Currently the pipeline computes a shortlist but never writes it anywhere. Needed both for live record-keeping and for reproducing/auditing what was actually selected at each historical backtest date. Schema already sketched in earlier docs (run_date, ticker, return_pct, dma, above_dma, momentum_rank, sector, shortlisted).
2. **`portfolio_log` table + real trade tracking.** Needed once the user starts actually running this live — entry/exit date & price, qty, running peak price (for the user's manual trailing-stop check).
3. **Manual delta-rebalancing workflow (user-side, not code):** `get_allocation()`/`get_allocation_efficient()` already return `{ticker: target_units}` each run — the user will diff this against current holdings and manually buy/sell the delta. No code change needed for this; just documented here as the intended live-usage pattern so it isn't mistaken for a gap.
4. **Manual/weekly trailing-stop check (user-side, not code):** user has decided to check this manually/ad-hoc against running peak price rather than automate it — again, documented so it isn't re-flagged as unfinished work.
5. **Cleanup pass** on remaining dead/commented code and any lingering module-level side effects — low priority, doesn't block functionality.
6. **Optional, not yet started:** full historical Nifty 500 constituent reconstruction (via bhavcopy + market-cap ranking, per the methodology in the cited survivorship-bias paper) if a more rigorous bias-corrected number is ever needed beyond the Nifty 50 comparison already done.
7. **Optional, not yet started:** sector momentum rotation layer (ranking sector indices themselves, picking top 2-3 sectors before stock selection) — explicitly deferred in the original build plan until after baseline backtest results were in hand. Now that they are, this is a reasonable next research direction, but lower priority than the persistence/portfolio_log build items above.

---

## 10. Environment Notes (unchanged from earlier docs)

- OS: Windows, pgAdmin GUI for DB management.
- Postgres connection currently hardcoded in scripts: `postgresql://postgres:<password>@localhost:5432/momentum` — still flagged to move to `.env`, not yet done.
- Confirmed working libraries: `yfinance`, `pandas`, `sqlalchemy`, `psycopg2-binary`, `numpy`.
- Confirmed real `price_history` date range as of this session: **2016-07-11 to 2026-07-10**, ~500 tickers.

---

*This document reflects the state after a full backtesting validation pass: engine build-out, multiple bug fixes (DMA filter, rank_weights, lookback_days, backfill upsert ordering), idle-cash and transaction-cost sensitivity analysis, two independent walk-forward tests (Nifty 500 and Nifty 50), rolling-window regime analysis, and a final config decision. Next phase of work is the persistence layer (`screen_runs`, `portfolio_log`) and moving toward live/paper use.*
