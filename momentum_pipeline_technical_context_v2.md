# Momentum Pipeline — Technical Context Document (v2)
*Status snapshot for continuing the build. Written to be dropped into any AI model or read by future-you without needing the full conversation history. Supersedes the earlier v1 context doc.*

---

## 1. Architecture (unchanged from proposal)

```
stock_universe (NSE/Nifty 500) → Postgres [stocks]
                                        │
                                        ▼
                          fetch_prices → Postgres [price_history]
                                        │
                                        ▼
                    db_utils adapter (dict[ticker: DataFrame])
                                        │
                                        ▼
                transform.py (returns, DMA, rank, sector cap, fundamentals)
                                        │
                                        ▼
                          Postgres [screen_runs] ← load step
                                        │
                          ┌─────────────┴─────────────┐
                          ▼                             ▼
                    Power BI dashboard              n8n (scheduling)
                                        │
                          ┌─────────────┴─────────────┐
                          ▼                             ▼
                  portfolio_log (real trades)   backtest engine (historical replay)
```

---

## 2. Component Status

| Component | Status | Notes |
|---|---|---|
| `stocks` table (universe) | ✅ Done, validated | ~500 Nifty 500 tickers, tickers validated against yfinance, sector column populated from Industry field, no nulls |
| `price_history` table | ✅ Done, validated | 5 years daily OHLC+volume, ~555,681 rows, ~1236-1238 rows/ticker |
| Adjustment method decision | ✅ Resolved | `auto_adjust=True` — see Section 4 for full reasoning |
| `db_utils.py` | ✅ Done, working | `get_engine()`, `load_price_dict_from_db()` (supports `as_of_date` and `ticker` filters), `load_universe_from_db()` |
| `compute_returns_and_dma()` | ✅ Done, validated on real data | Cross-checked against Screener.in/Google for multiple stocks (KFinTech, Ather); DMA cross-checked via manual recompute; logic independently verified by multiple AI models |
| `rank_and_shortlist()` | ⏳ Exists (from earlier synthetic-data prototype), not yet run on real full-universe data | Needs `sector_map` from `load_universe_from_db()` |
| Fundamental flag / data source | ❌ Not started | No fundamentals data pulled yet in this project |
| Sector mapping (ticker→sector) | ✅ Partially done | Already have `sector` column in `stocks` table from Nifty 500 Industry field — may still need NSE sectoral-index cross-check for accuracy (not yet done) |
| Sector index price history | ❌ Not started | **Blocking item for backtest — see Section 5** |
| Sector trend ranking | ❌ Not started | Depends on sector index price history |
| Turnover filter | ❌ Not started | Volume data already available in `price_history` — logic not yet written |
| Exit signals (rank-exit, DMA-exit, trailing stop) | ❌ Not started | Trailing stop needs `portfolio_log` table (not yet created) to track entry price / running peak |
| `screen_runs` table | ⚠️ Created, empty | No pipeline run has written to it yet |
| `portfolio_log` table | ❌ Not created | Needed for exit signals and real/paper trade tracking |
| `sector_index_history` table | ❌ Not created | Needed before sector-based backtesting |
| Backtest engine | ❌ Not started | See Section 5 — sectorial data is the current blocker |
| n8n automation | ❌ Not started | |
| Power BI dashboard | ❌ Not started | |

---

## 3. Database Schema (current, as built)

```sql
CREATE TABLE stocks (
    ticker VARCHAR(20) PRIMARY KEY,
    name TEXT,
    sector TEXT
);
-- populated: ~500 rows

CREATE TABLE price_history (
    ticker VARCHAR(20) REFERENCES stocks(ticker),
    date DATE,
    close NUMERIC,
    volume NUMERIC,
    PRIMARY KEY (ticker, date)
);
-- populated: 555,681 rows, auto_adjust=True data, truncated/reloaded twice during validation

CREATE TABLE screen_runs (
    run_date DATE,
    ticker VARCHAR(20) REFERENCES stocks(ticker),
    return_pct NUMERIC,
    dma NUMERIC,
    above_dma BOOLEAN,
    momentum_rank INT,
    fundamental_pass BOOLEAN,
    shortlisted BOOLEAN,
    PRIMARY KEY (run_date, ticker)
);
-- created, empty
```

### Tables still to be created
```sql
-- Needed before backtest can incorporate sector rotation
CREATE TABLE sector_index_history (
    sector TEXT,
    date DATE,
    close NUMERIC,
    PRIMARY KEY (sector, date)
);

-- Needed for exit signals (trailing stop) and real trade tracking
CREATE TABLE portfolio_log (
    trade_id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) REFERENCES stocks(ticker),
    entry_date DATE,
    entry_price NUMERIC,
    qty NUMERIC,
    running_peak_price NUMERIC,
    exit_date DATE,
    exit_price NUMERIC,
    status TEXT  -- 'OPEN' / 'CLOSED'
);
```

---

## 4. Key Decisions & Lessons Learned (worth preserving — these came from real debugging, not just planning)

1. **`auto_adjust=True` is the correct setting, decided after a real bug chase:**
   - Initially suspected adjustment was causing a small DMA mismatch (₹966 external vs ₹990 computed) — confirmed via before/after comparison that dividend adjustment causes small (~1-2%), consistent-direction gaps in absolute price levels (DMA), but does NOT materially affect `return_pct` or `above_dma` since those are ratios/comparisons within a consistent series.
   - Then tested `auto_adjust=False` directly — this exposed a much worse problem: stock splits/bonus issues cause **large, fake return spikes** in raw (non-adjusted) data, since `auto_adjust=False` doesn't back out the mechanical price drop from a split.
   - Verified specific case: Ather Energy showed a 240% "return" — checked independently (no split found, confirmed via Google) — real momentum, not an artifact. But the risk that a split-driven fake number could have been in that list was the deciding factor.
   - **Final decision: `auto_adjust=True` throughout `price_history`.** Small, bounded dividend-driven inaccuracy in absolute price (DMA) is preferable to unbounded, occasionally catastrophic split-driven corruption in returns. This asymmetry (small guaranteed error vs. rare large error) is a reusable principle for future data-source decisions in this build (e.g., fundamentals, turnover data).

2. **Raw vs adjusted price matters differently depending on use case:** ratios (return_pct, above_dma) are adjustment-agnostic; absolute prices used for anything transactional (future `portfolio_log` entry/exit prices, trailing-stop-from-peak in rupee terms) should ideally use raw traded price, not adjusted — this is a nuance to handle when `portfolio_log` is built, not necessarily by changing `price_history` again.

3. **Validation method that worked well and should be repeated for new data sources:** manual recompute (e.g., `closes.tail(200).mean()`) to isolate calculation bugs from data-source bugs, cross-checking specific dates/prices against Screener.in or Google, and spot-checking a deliberately varied sample (a riser, a faller, a large-cap, a split-prone sector) rather than one random stock.

---

## 5. Backtest — Current Blocker: Sectorial Data

**User's stated next step:** before backtesting can start, sector-level data needs to be populated. This is correct — here's why and what's needed.

### Why sector data is needed for backtesting specifically (not just the live screen)
- The strategy's sector cap rule (max 2-3 stocks per sector) needs to be enforced at **every historical rebalance date** during a backtest replay, not just for today's run — so sector membership needs to be available historically, not just currently.
- If a sector-rank/rotation layer is eventually added to the strategy (discussed as a "soft/display" layer originally, possibly harder filter later), the backtest needs to replay that decision at each historical point too — this needs actual sector index price history, not just a static sector label.

### What "populating sectorial data" concretely means here
1. **Sector index price history** (new — not yet started): pull historical prices for NSE sectoral indices (Nifty IT, Nifty Bank, Nifty Auto, Nifty Pharma, Nifty FMCG, Nifty Metal, Nifty Energy, etc.) via Yahoo Finance tickers (`^CNXIT`, `^NSEBANK`, `^CNXAUTO`, `^CNXPHARMA`, `^CNXFMCG`, `^CNXMETAL`, `^CNXENERGY` — ticker symbols should be individually verified, some NSE sector index Yahoo symbols are inconsistent/hard to find). Store in new `sector_index_history` table, same shape as `price_history` (sector, date, close).
2. **Stock-level sector mapping accuracy check** (partially done): `stocks.sector` currently comes from the Nifty 500 file's `Industry` column — this may use different category granularity/naming than NSE's official sectoral indices. Worth a cross-check: does "Industry" in your file map cleanly 1:1 to an actual NSE sectoral index name, or will some stocks need manual reclassification? This determines whether the existing `sector` column is usable as-is or needs to be rebuilt from NSE's own sectoral index constituent lists (a more authoritative but more manual source, discussed earlier as the preferred long-term source).
3. **Historical constituent membership caveat (already flagged, still open):** sector index constituents change periodically (companies get added/removed from sectoral indices, not just from Nifty 500 overall). For a *true* survivorship-bias-free backtest, sector membership would ideally be tracked per historical date too. **This has already been explicitly deprioritized by the user for the overall backtest** (accepted as a known limitation) — worth deciding whether the same relaxed standard applies to sector data, or whether sector data specifically warrants more rigor since it directly gates which stocks are even eligible at each rebalance.

### Recommended immediate steps
1. Identify correct Yahoo Finance tickers for each NSE sectoral index (verify each one resolves before batch-pulling — some are unreliable/renamed).
2. Build `fetch_sector_indices.py` (same batching pattern as `fetch_prices.py` — chunked `yf.download()`, reshape to long format) — pull matching 5-year history for consistency with `price_history`.
3. Create and populate `sector_index_history` table.
4. Decide: keep existing `stocks.sector` from Industry field, or rebuild from NSE sectoral index constituent lists for tighter alignment with the index price data being pulled in step 1-3.
5. Only then: backtest engine can enforce sector caps historically and (optionally) incorporate sector-rank as a signal.

---

## 6. Immediate Next Steps (priority order)

1. **Sectorial data population** (user's stated current focus — see Section 5 for full breakdown)
2. Run `rank_and_shortlist()` on full real universe (returns/DMA already validated — just needs sector_map wired in)
3. Write first real output to `screen_runs`
4. Build `portfolio_log` table (needed for exit signals regardless of backtest progress)
5. Backtest engine (`backtest/engine.py`) — walks historical rebalance dates using `as_of_date` parameter already built into `db_utils.py`
6. Fundamental data source + flag logic
7. Turnover filter (volume data already available, just needs the calculation: avg daily value traded ÷ market cap)
8. Exit signals (rank-exit, DMA-exit, trailing-stop-from-peak)
9. n8n scheduling, Power BI dashboard

---

## 7. Environment Notes

- OS: Windows, using pgAdmin GUI (not `psql` CLI) for database management
- Postgres connection currently hardcoded in scripts: `postgresql://postgres:<password>@localhost:5432/momentum` — still flagged to move to `.env`, not yet done
- Confirmed working libraries: `yfinance`, `pandas`, `sqlalchemy`, `psycopg2-binary`, `numpy`
- Data reload pattern during testing: `TRUNCATE TABLE` before each full reload (no upsert logic yet — will be needed for genuine incremental updates later)

---

*This document reflects progress as of the current build session, specifically the point where returns/DMA logic has been fully validated on real data and the focus is shifting to sectorial data as a backtest prerequisite.*
