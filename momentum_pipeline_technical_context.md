# Momentum Pipeline — Technical Context Document
*Status snapshot for continuing the build. Written to be dropped into any AI model or read by future-you without needing the full conversation history.*

---

## 1. Proposed Architecture

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

### Proposed file/folder structure
```
momentum_pipeline/
├── config.yaml
├── requirements.txt
├── data/
│   └── nifty_500_cleaned.csv          # DONE
├── src/
│   ├── db_utils.py                    # PROPOSED, NOT YET BUILT
│   ├── extract/
│   │   ├── fetch_universe.py          # DONE (ad hoc script, not yet modularized)
│   │   ├── fetch_prices.py            # DONE (get_price + reshape_to_long, working)
│   │   ├── fetch_sector_indices.py    # NOT STARTED
│   │   └── fetch_fundamentals.py      # NOT STARTED
│   ├── transform/
│   │   ├── returns_and_dma.py         # NOT STARTED in this project (exists as tested code from earlier prototype, not yet ported)
│   │   ├── volatility_score.py        # NOT STARTED
│   │   ├── turnover_filter.py         # NOT STARTED
│   │   ├── sector_rank.py             # NOT STARTED
│   │   ├── fundamental_flag.py        # NOT STARTED
│   │   ├── rank_and_shortlist.py      # NOT STARTED in this project (exists as tested code from earlier prototype, not yet ported)
│   │   └── exit_signals.py            # NOT STARTED (rank-exit, DMA-exit, trailing-stop-from-peak)
│   ├── load/
│   │   └── writers.py                 # NOT STARTED (screen_runs insert logic)
│   └── pipeline.py                    # NOT STARTED (orchestrator)
├── backtest/
│   └── engine.py                      # NOT STARTED — requires historical index constituent lists to avoid survivorship bias (flagged, not yet sourced)
├── portfolio/
│   └── trade_log_template.csv         # NOT STARTED
├── n8n/
│   └── momentum_workflow.json         # NOT STARTED
└── powerbi/
    └── momentum_dashboard.pbix        # NOT STARTED
```

---

## 2. Database Schema (Postgres — built and populated)

```sql
CREATE TABLE stocks (
    ticker VARCHAR(20) PRIMARY KEY,
    name TEXT,
    sector TEXT
);
-- STATUS: created, loaded with ~500 rows from nifty_500_cleaned.csv

CREATE TABLE price_history (
    ticker VARCHAR(20) REFERENCES stocks(ticker),
    date DATE,
    close NUMERIC,
    volume NUMERIC,   -- added via ALTER TABLE after initial schema
    PRIMARY KEY (ticker, date)
);
-- STATUS: created, loaded with 555,681 rows (5 years daily, ~500 tickers, ~1237 rows/ticker)

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
-- STATUS: created, EMPTY (no pipeline run has written to this yet)
```

### Schema gaps to add later (proposed, not yet built)
- `screen_runs` needs `volatility_adj_score`, `turnover`, `sector_rank` columns once those transform modules exist
- `portfolio_log` table not yet created — needs entry/exit price & date, qty, running peak price (for trailing stop calc)
- `sector_index_history` table not yet created — needed once sector trend ranking is built

### Known open issue: no upsert logic yet
Current load pattern is `to_sql(if_exists="append")` with a manual `TRUNCATE TABLE` before re-testing. This works for now but will need a real `INSERT ... ON CONFLICT` (upsert) pattern before incremental daily/monthly updates are viable without wiping history each time.

---

## 3. Data Pipeline — Current Working State

### `fetch_universe` (ad hoc script, done)
- Downloaded Nifty 500 constituent CSV from Nifty Indices
- Cleaned columns → `name`, `sector`, `ticker` (ticker = Symbol + `.NS`)
- Validated all ~500 tickers resolve correctly via yfinance (loop ran once, commented out after confirming 0 wrong tickers)
- Validated `Industry` column has no nulls
- Loaded into Postgres `stocks` table — confirmed via `SELECT COUNT(*)`

### `fetch_prices.py` (done, working)
Two functions, tested and working on real data:

```python
def get_price(ticker: list, period: str, interval: str = "1d", chunksize: int = 50):
    # chunked yf.download() calls, catches exceptions per chunk,
    # calls reshape_to_long() inside the try block (fixed bug: was outside originally)
    # returns concatenated long-format DataFrame across all chunks
    ...

def reshape_to_long(data, tickers):
    # converts yfinance's wide multi-ticker output into
    # ticker, date, close, volume long format
    ...
```

**Verified results:**
- Tested first on 5 sample tickers with `period="6mo"` — output shape and dtypes confirmed correct (`date` as datetime64, consistent row counts per ticker)
- Scaled to full universe with `period="5y"` — 555,681 total rows loaded, ~1236-1238 rows per ticker (matches expected ~247 trading days/year × 5)
- Loaded into `price_history` via `to_sql(method="multi", chunksize=1000)`

**Known limitations / not yet handled:**
- No retry/backoff logic beyond try/except per chunk
- No `time.sleep()` between chunks yet (recommended, not confirmed added)
- Re-running for new dates will hit primary key conflicts — no upsert logic yet, currently relies on `TRUNCATE` between test runs

### `transform.py` — NOT yet built in this project
An earlier prototype (separate zip, built before this Postgres-based version) had these functions **written and validated against synthetic data with passing assertions** (shortlist size, sector cap, DMA filter, fundamental filter all correctly enforced):
- `compute_returns_and_dma(price_histories, return_window_months, dma_window_days, skip_recent_days=0)`
- `apply_fundamental_flag(df, fundamentals, max_debt_to_equity, min_earnings_growth)`
- `rank_and_shortlist(df, sector_map, max_stocks_per_sector, shortlist_size, require_above_dma=True)`

These are **conceptually done and logic-tested**, but **not yet ported into the current Postgres-backed project**, and **not yet run against real data**.

### `db_utils.py` — proposed, not yet built
Purpose: bridge Postgres data back into the `dict[ticker: DataFrame]` shape the transform functions expect, so transform logic doesn't need to change.

Proposed signature:
```python
def get_engine(): ...
def load_price_dict_from_db(engine, as_of_date=None, lookback_days=400): ...
def load_universe_from_db(engine): ...
```
The `as_of_date` parameter is intentional — it's what will make this function reusable for both the live pipeline (no filter = latest) and the backtest engine (pass historical date = no look-ahead bias).

---

## 4. Strategy Logic (fully specified, pending code implementation)

*(Full detail already captured in the earlier `momentum_strategy_build_plan.md` — summarized here for quick reference)*

- **Ranking signal:** 6-month return, upgrading to volatility-adjusted score (return ÷ std dev of daily returns) — not yet implemented in code
- **Trend filter:** price > 200 DMA
- **Sector cap:** max 2-3 stocks per sector
- **Fundamental gate:** light pass/fail (debt/equity, earnings growth, pledging, governance) — data source (Yahoo `.info`) not yet wired up in this project
- **Turnover filter (planned addition):** avg daily value traded ÷ market cap, soft preference for lower turnover — not yet implemented
- **Exit rules:** rank-exit (falls out of top 30 at rebalance) + DMA-exit + trailing-stop-from-peak (15-20%, needs `portfolio_log` running-peak-price state) — none implemented yet
- **Sector trend layer:** rank sector indices by their own 6M return, display-only initially — not started, blocked on `fetch_sector_indices.py`

---

## 5. Backtest Requirements (specified, not started)

- **Critical constraint acknowledged and accepted as a known limitation for now:** using today's Nifty 500 constituent list applied backward in time introduces survivorship bias (today's list excludes stocks that were removed from the index over the 5-year window). Proper fix would require historical constituent lists per rebalance date from Nifty Indices — **flagged but explicitly deprioritized by the user for now**.
- Engine needs to walk monthly rebalance dates across the 5-year `price_history`, calling transform logic with `as_of_date` to prevent look-ahead within each iteration
- Needs assumed transaction cost per rebalance (brokerage + STT + STCG placeholder) to avoid an inflated backtest number
- Needs Nifty 500's own historical price series as benchmark comparison

---

## 6. Automation / Dashboard (specified, not started)

- **n8n**: Schedule Trigger (monthly) → Execute Command (pipeline.py) → Postgres write → notification. Not started.
- **Power BI**: connects natively to Postgres; suggested pages — current shortlist, sector breakdown, rank history over time, screen funnel, portfolio vs theoretical comparison. Not started.

---

## 7. Immediate Next Steps (in dependency order)

1. Build `db_utils.py` — `load_price_dict_from_db()` adapter (unblocks everything below)
2. Sanity-check adapter output against known real numbers (e.g., verify a stock's 6M return against Screener.in)
3. Port/rebuild `transform.py`'s `compute_returns_and_dma()` into this project, run against real Postgres data
4. Add `rank_and_shortlist()` once returns/DMA output is verified
5. Write first real `screen_runs` output to Postgres
6. Only after that: sector indices, fundamentals, turnover filter, exit signals, backtest engine, n8n, Power BI (in roughly that order, per earlier build-order discussion)

---

## 8. Environment Notes

- OS: Windows (PATH issues encountered with `pg_isready` — resolved by using pgAdmin GUI instead of CLI)
- DB access via pgAdmin (GUI) — Query Tool used for DDL, not `psql` CLI
- Postgres connection: `postgresql://postgres:<password>@localhost:5432/momentum` (password currently hardcoded in scripts — flagged earlier to move to `.env`/`python-dotenv`, not yet done)
- Python libraries confirmed working in this environment: `yfinance`, `pandas`, `sqlalchemy`, `psycopg2-binary`

---

*This document reflects progress as of the current build session. Update the "DONE / NOT STARTED" markers as each file is completed.*
