# Momentum Pipeline — Technical Context Document (v3)
*Reflects actual reviewed code state (from uploaded `momentum.7z`), not just planning. Supersedes v1 and v2 context docs.*

---

## 1. Architecture (as actually implemented so far)

```
stock_universe (Nifty 500 CSV, cleaned) → Postgres [stocks]
                                        │
                                        ▼
                    fetch_prices.py → Postgres [price_history]  (5y daily, close+volume)
                                        │
                                        ▼
                db_utils.load_price_dict_from_db() → dict[ticker: DataFrame]
                                        │
                                        ▼
        returns_and_dma.compute_returns_and_dma()  (return_pct, DMA, above_dma — filter now applied)
                                        │
                                        ▼
        returns_and_dma.transform_to_dict() + populate_sectors()  (attach sector label per stock)
                                        │
                                        ▼
        returns_and_dma.sector_selection()  (sector cap, now sorted correctly)
                                        │
                                        ▼
        get_top_companies()  ← single callable entry point tying the above together
                                        │
                                        ▼
        backtest_engine.py  (weights, allocation — date-stepping loop present but NOT yet wired to replay strategy)
```

---

## 2. File-by-File Status (as reviewed from actual code)

### `src/db_utils.py` — ✅ Working, no issues found
- `get_engine()` — Postgres connection
- `load_price_dict_from_db(engine, as_of_date=None, lookback_days=400, ticker=None)` — returns `{ticker: DataFrame[Close]}`. Empty-list `ticker=[]` is treated as "search entire universe" (Python falsy-list behavior) — intentional per user, though reads ambiguously in code; flagged as a minor readability note, not a bug.
- `load_universe_from_db(engine)` — returns full `stocks` table

### `src/extract/fetch_prices.py` — ✅ Working, validated
- `get_price()` — chunked `yf.download()`, 50-ticker chunks, `time.sleep(5)` between chunks, try/except per chunk
- `reshape_to_long()` — converts wide yfinance output to long format (ticker, date, close, volume)
- `write_to_db()` — pulls ticker list from `stocks` table, fetches 5y history, writes to `price_history`
- `populate_sectors(ticker_dict, engine)` — attaches sector label to each ticker in a result dict. **Inefficient**: one SQL query per ticker in a loop (fine at current scale of ~10-30 stocks post-filtering, would need batching — single `WHERE ticker IN (...)` query — if ever called on larger sets)
- **Note:** top-level module code (`test = pd.read_sql(...)`, print statements) still executes on import — same pattern as flagged for `returns_and_dma.py`, low priority but worth cleaning into a `__main__` guard eventually

### `src/extract/extract.py` — Stub, unused
- Just `import yfinance` — appears to be leftover scaffolding, nothing currently imports from this file

### `src/transform/returns_and_dma.py` — ⚠️ Fixed this session, verify before trusting further
**Functions:**
- `compute_returns_and_dma(price_histories, return_window_months, dma_window_days, skip_recent_days=0, top_n=30)` — computes return_pct, DMA, above_dma per ticker. **DMA filter now added** (`df = df[df["above_dma"] == True]` before sort/head) — previously this was missing entirely, meaning downtrending stocks with high 12M return could pass through unfiltered. Logic itself (return calc, DMA calc) independently validated earlier against Screener.in/Google for real tickers (KFinTech, Ather Energy).
- `transform_to_dict(df, engine)` — converts ranked DataFrame to `{ticker: [latest_price, return_pct, dma, above_dma]}`, then calls `populate_sectors()` to append sector as the list's last element → final shape `{ticker: [latest_price, return_pct, dma, above_dma, sector]}`
- `sector_selection(engine, top_stocks, stock_count=10, max_per_sector=2)` — walks the ranked dict, enforces sector cap. **Now explicitly re-sorts by return_pct** before iterating (`sorted(top_stocks.items(), key=lambda x: x[1][1], reverse=True)`) rather than silently trusting dict insertion order — this was a fragile implicit dependency, now made explicit.
- `get_top_companies(ticker, return_window_months, dma_window_days, data_engine)` — single entry point: loads prices → computes returns/DMA (with `top_n` pool, should be called with `top_n=30` not the function's parameter-name typo `n=10` that was caught this session — **needs this fix applied**: `compute_returns_and_dma(..., top_n=30)`) → converts to dict + sectors → applies sector cap → returns final shortlist dict

**Known remaining issue:** module-level executable/commented-out code (old test blocks, prior debugging) still present in the file — doesn't break anything currently since it's commented out, but worth a cleanup pass before this file is considered "done."

**Design decision confirmed this session:** the "wide pool → sector cap narrows it" two-stage approach (top 30 → capped shortlist of ~10-20) was deliberately chosen over capping directly on a small top-10 pool, to avoid running out of eligible candidates when the top-ranked stocks cluster in one sector.

### `src/backtest/weights.py` — ✅ Fixed and working
- `equal_weights(count, ticker)` — correct
- `rank_weights(count, ticker)` — correct; ranks generated dynamically from `count` (not hardcoded), explicitly sorts input by rank before assigning weights (fixed a silent dict-ordering bug from earlier draft)
- `get_weights(weight_type, ticker)` — dispatcher, correctly passes `ticker` through to both weighting functions (earlier draft was missing this)
- **Validated:** weights sum to 1.0 confirmed by hand-testing during development

### `src/backtest/backtest_engine.py` — ⚠️ Incomplete, not yet a real backtest
- `get_allocation(ticker, capital, weights)` — correctly computes whole-unit share allocation, tracks and returns leftover cash as `(allocation, total_leftover)` tuple. Math verified correct after two rounds of debugging a double-counting bug in leftover calculation.
- `backtest_engine(ticker, capital, start_date, end_date)` — **function exists but is never called.** Its internal loop only steps through dates checking if price data exists for that date (`WHERE date = ...`) — it does NOT call `get_top_companies()`, does NOT call weighting/allocation, does NOT track portfolio value across iterations. This is currently just a date-validity walker, not a strategy replay loop.
- **Module-level code at the bottom currently runs a single present-day snapshot** (`get_top_companies()` → `get_weights()` → `get_allocation()`) — this proves the individual pieces connect correctly, but this is NOT a backtest; it's one point-in-time allocation calculation.
- **This is the main open item blocking real backtesting** — see Section 4.

### `data/clean.py` — ✅ Done, validated
- Loads raw Nifty 500 CSV, appends `.NS` to symbols, validates tickers resolve via yfinance (run once, confirmed 0 wrong tickers, loop now commented out), validates no null `Industry` values, renames columns to `name/sector/ticker` schema, saves `nifty_500_cleaned.csv`

### `data/clean_sectors.py` — Not yet reviewed in detail this session (exists in project, contents not walked through)

---

## 3. Database State

| Table | Status | Row count / notes |
|---|---|---|
| `stocks` | ✅ Populated | ~500 rows, ticker/name/sector, validated |
| `price_history` | ✅ Populated | 555,681 rows, 5y daily close+volume, `auto_adjust=True` (final decision after split-vs-dividend bug investigation) |
| `screen_runs` | ⚠️ Created, still empty | No pipeline run has written to it yet — current shortlist logic returns a dict in memory, doesn't persist |
| `sector_index_history` | ❌ Not created | Deferred along with sector-momentum feature |
| `portfolio_log` | ❌ Not created | Needed for exit signals (trailing stop) and real trade tracking — not yet started |

---

## 4. Key Open Items (priority order)

1. **Fix the `top_n` parameter typo** in `get_top_companies()` — currently calls `compute_returns_and_dma(..., n=10)`, will throw `TypeError` since the parameter is named `top_n`. Should be `top_n=30` to preserve the wide-pool-before-sector-cap design.
2. **Build the actual backtest replay loop.** All the individual pieces exist and work in isolation (ranking+filter+sector-cap via `get_top_companies()`, weighting via `weights.py`, allocation via `get_allocation()`) — but nothing loops through historical rebalance dates calling them in sequence and tracking portfolio value period-over-period. This is the single biggest gap between "components work" and "backtest produces a CAGR/drawdown number."
3. **Write shortlist output to `screen_runs`** — currently the pipeline computes a shortlist but doesn't persist it anywhere; needed for both live-use record-keeping and for the backtest to log what it selected at each historical date.
4. **`portfolio_log` table + exit signal logic** (rank-exit, DMA-exit, trailing-stop-from-peak) — not started, needed before real/paper trading can be tracked against the system.
5. **Clean up dead/commented code and module-level side effects** in `returns_and_dma.py` and `fetch_prices.py` — low priority, doesn't block functionality, but worth doing before the codebase grows further.

---

## 5. Deferred / Explicitly Out of Scope for Now

- **Sector momentum rotation** (ranking sector indices themselves, picking top 2-3 sectors before stock selection) — explicitly deferred until after the baseline backtest (stock-level momentum + DMA filter + sector-cap-by-label, no rotation) produces results. Decision confirmed this session. Blocked separately on sourcing sector index price history (yfinance doesn't have data for some sectoral indices like Nifty Private Bank — ETF proxies or NSE's own historical index archives are the fallback options discussed).
- **Fundamental quality gate** — not wired up with real data in this project yet.
- **Turnover filter** — not implemented yet; planned as a post-baseline test variant.
- **Volatility-adjusted momentum score** — planned upgrade to ranking logic, not yet implemented; baseline uses raw 6M/12M return_pct.
- **n8n scheduling, Power BI dashboard** — not started.

---

## 6. Baseline Backtest Configuration (agreed, for reference)

| Dimension | Baseline choice |
|---|---|
| Universe | Nifty 500 |
| Ranking lookback | 6-month return (though current test runs have been using 12-month — confirm which is intended for the actual baseline before running) |
| DMA filter | ON (200-day) — now correctly implemented in code as of this session |
| Fundamental gate | OFF |
| Sector cap | ON, label-based, max 2-3 per sector — implemented |
| Sector rotation | OFF — deferred |
| Rebalance frequency | Monthly |
| Shortlist size | Top 20 (current code uses `stock_count=10` default in `sector_selection` — confirm/update to match agreed baseline of 20 before running the real backtest) |
| Weighting | Equal weight for baseline; rank-based as first variation |
| Exit rules | Rank-exit (falls out of top 30) + 15% trailing-stop-from-peak — **not yet implemented in code** |
| Turnover filter | OFF for baseline |
| Transaction costs | OFF for baseline (frictionless), added as second pass |
| Benchmark | Nifty 500 total return, same period |

**Discrepancy to resolve:** the agreed baseline was 6-month return / top-20 shortlist, but current code has been tested with `return_window_months=12` and `stock_count=10` defaults. Worth deciding whether to align the code to the original baseline spec, or consciously treat 12M/top-10 as a valid alternate configuration to test — just needs to be a deliberate choice, not an accidental drift.

---

*This document reflects the actual reviewed code from `momentum.7z`, cross-checked against prior planning documents (v1, v2). Update status markers as the backtest loop and remaining open items are completed.*
