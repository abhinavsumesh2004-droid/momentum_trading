import datetime
import pandas as pd

from src.db_utils import get_engine, load_price_dict_from_db
from src.transform.returns_and_dma import (
    compute_returns_and_dma, transform_to_dict, sector_selection, get_top_companies
)
from src.backtest.weights import get_weights
from src.backtest.backtest_engine import get_allocation_efficient
from src.load.writers import write_screen_run
from src.portfolio.delta import compute_rebalance_delta, record_entry
from src.backtest.backtest_engine import get_portfolio_value
from src.portfolio.delta import get_current_holdings
from src.extract.fetch_prices import refresh_latest_prices

# --- finalized config (Section 1, v4 doc) ---
RETURN_WINDOW_MONTHS = 6
DMA_WINDOW_DAYS = 200
STOCK_COUNT = 20
MAX_PER_SECTOR = 3
WEIGHT_TYPE = "vol_adjusted"

# Quarterly cadence, enforced here (not just a convention) — see the
# "what happens if you run mid-quarter" discussion. This gates the WRITE to
# screen_runs only; everything else (price refresh, screening, allocation,
# delta preview) still runs every time so you can look at the numbers
# whenever you want without it counting as a rebalance.
MIN_DAYS_BETWEEN_REBALANCES = 80


def _days_since_last_screen_run(engine):
    """Days since the most recent row in screen_runs, or None if the table
    is empty (first-ever run — always allowed through)."""
    result = pd.read_sql("SELECT MAX(run_date) AS last_run FROM screen_runs", engine)
    last_run = result["last_run"].iloc[0]
    if pd.isna(last_run):
        return None
    last_run_date = pd.to_datetime(last_run).date()
    return (datetime.date.today() - last_run_date).days


def run_live(cash_to_add: float = 0, force: bool = False):
    """
    cash_to_add: total cash you're deploying this quarter — includes both
    leftover/idle cash from last time AND any fresh money you're adding.
    You already know this number by looking at your brokerage balance,
    so no need to track it separately in the DB.

    Total deployable capital = current holdings marked to market + cash_to_add.
    This is NOT a fixed number across runs — it reflects however much your
    existing position has grown/shrunk, plus whatever cash you're bringing in.

    force=True bypasses the MIN_DAYS_BETWEEN_REBALANCES gate and writes to
    screen_runs regardless of how recently the last real rebalance happened —
    use deliberately (e.g. correcting a mistake), not as a habit, or this
    quietly turns back into ad-hoc rebalancing.
    """
    engine = get_engine()
    run_date = datetime.date.today()

    refresh_latest_prices(engine)

    current_holdings = get_current_holdings(engine)  # {ticker: qty}
    portfolio_value, _ = get_portfolio_value(
        engine, current_holdings, leftover_cash=0, as_of_date=run_date
    )
    capital = portfolio_value + cash_to_add

    print(f"Current holdings marked to market: {portfolio_value:.2f}")
    print(f"+ cash you're adding: {cash_to_add:.2f}")
    print(f"Total deployable capital this rebalance: {capital:.2f}\n")

    universe = pd.read_sql("SELECT ticker FROM stocks", engine)["ticker"].tolist()
    excluded = set()  # in-memory only for this call — never written anywhere, never persists

    required_trading_days = max(DMA_WINDOW_DAYS, RETURN_WINDOW_MONTHS * 21)
    lookback_days = int(required_trading_days * 1.6) + 30

    while True:
        active_universe = [t for t in universe if t not in excluded]

        price_histories = load_price_dict_from_db(engine, lookback_days=lookback_days, ticker=active_universe)
        top30_df = compute_returns_and_dma(price_histories, RETURN_WINDOW_MONTHS, DMA_WINDOW_DAYS, top_n=30)

        # vol_adjusted weighting needs each shortlisted stock's volatility_pct
        # alongside the shortlist itself — only fetched when actually needed.
        if WEIGHT_TYPE == "vol_adjusted":
            shortlist, volatility = get_top_companies(
                ticker=active_universe, return_window_months=RETURN_WINDOW_MONTHS,
                dma_window_days=DMA_WINDOW_DAYS, data_engine=engine,
                stock_count=STOCK_COUNT, max_per_sector=MAX_PER_SECTOR,
                return_volatility=True,
            )
        else:
            shortlist = get_top_companies(
                ticker=active_universe, return_window_months=RETURN_WINDOW_MONTHS,
                dma_window_days=DMA_WINDOW_DAYS, data_engine=engine,
                stock_count=STOCK_COUNT, max_per_sector=MAX_PER_SECTOR,
            )
            volatility = None

        print(f"\nCurrent shortlist ({len(shortlist)} stocks): {', '.join(shortlist.keys())}")
        exclude_input = input(
            "\nExclude any tickers for THIS RUN ONLY (comma-separated, e.g. OLAELEC), "
            "or press Enter to continue: "
        ).strip()
        if not exclude_input:
            break

        new_excludes = set()
        for t in exclude_input.split(","):
            t = t.strip().upper()
            if not t:
                continue
            if not t.endswith(".NS"):
                t += ".NS"
            new_excludes.add(t)
        excluded |= new_excludes
        print(f"\nExcluding (this run only, not saved anywhere): {', '.join(sorted(excluded))}")
        print("Re-screening without them...")

    if excluded:
        print(f"\nExclusions applied this run: {', '.join(sorted(excluded))} "
              f"(one-off — next run starts fresh with the full universe again)\n")

    days_since_last = _days_since_last_screen_run(engine)
    due_for_rebalance = force or (days_since_last is None) or (days_since_last >= MIN_DAYS_BETWEEN_REBALANCES)

    if due_for_rebalance:
        write_screen_run(engine, run_date, top30_df, shortlist)
    else:
        days_left = MIN_DAYS_BETWEEN_REBALANCES - days_since_last
        print(f"PREVIEW ONLY — last rebalance was {days_since_last} day(s) ago "
              f"(next one due in {days_left} day(s), at a {MIN_DAYS_BETWEEN_REBALANCES}-day cadence).")
        print("Nothing written to screen_runs this run. Everything below is computed live "
              "for you to look at, not a committed rebalance record. Pass force=True to "
              "run_live() if you deliberately want to override this.\n")

    weights = get_weights(WEIGHT_TYPE, shortlist, volatility=volatility)
    allocation, leftover = get_allocation_efficient(shortlist, capital, weights, verbose=True)

    delta_df = compute_rebalance_delta(engine, allocation)
    print("\nRebalance delta vs current holdings:")
    print(delta_df.to_string(index=False))
    print(f"\nLeftover cash after this rebalance: {leftover:.2f}  "
          f"(this is real idle cash — fold it into cash_to_add next quarter)")

    # Buy-side commit prompt. This ONLY writes bookkeeping rows to
    # portfolio_log — it does NOT place any real broker order. Only confirm
    # AFTER you've actually bought these on your broker, using the prices
    # you actually got (this logs the screen-time price as an approximation;
    # if your real fill price differed, fix it in portfolio_log afterward).
    #
    # Exits (Trim/NOT-in-new-shortlist rows) are deliberately NOT touched
    # here — those stay manual via record_exit(), per your call to review
    # sells yourself rather than have this auto-commit them.
    if due_for_rebalance:
        buy_rows = delta_df[delta_df["delta"] > 0]
        if buy_rows.empty:
            print("\nNo new buys to log this rebalance.")
        else:
            print(f"\n{len(buy_rows)} buy(s) above (NEW position / Add to position) can be "
                  f"logged to portfolio_log now.")
            confirm = input("Have you ALREADY executed these on your broker? "
                             "Log them now? (Y/N): ").strip().upper()
            if confirm == "Y":
                for _, row in buy_rows.iterrows():
                    t = row["ticker"]
                    price = shortlist.get(t, [None])[0]
                    if price is None:
                        print(f"  SKIPPED {t}: no price on hand — log manually with record_entry().")
                        continue
                    record_entry(engine, t, qty=row["delta"], entry_price=price)
            else:
                print("Not logged. Call record_entry(engine, ticker, qty, entry_price) "
                      "yourself once the trades are actually placed.")

    return shortlist, allocation, leftover, delta_df, due_for_rebalance


if __name__ == "__main__":
    import sys
    cash = float(sys.argv[1]) if len(sys.argv) > 1 else 0
    force = "--force" in sys.argv
    run_live(cash_to_add=cash, force=force)