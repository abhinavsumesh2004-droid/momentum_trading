"""
Diagnostic script — run this against your real DB to figure out WHY the
backtest CAGR looked high, before trusting the number.

Usage:  python -m diagnose_backtest
(place at project root, alongside src/, or adjust the sys.path line below)
"""
import pandas as pd
from datetime import datetime

from src.db_utils import get_engine
from src.backtest.backtest_engine import backtest_engine, generate_rebalance_dates
from src.transform.returns_and_dma import get_top_companies

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 160)

engine = get_engine()


def check_for_return_outliers(start_date, end_date, return_window_months=6, dma_window_days=200,
                               top_n_pool=30, outlier_threshold_pct=100):
    """Walks the same rebalance dates and prints any stock in the top-30 POOL
    (before sector cap) whose return_pct looks implausibly high — a likely
    sign of a bad split/data artifact rather than genuine momentum."""
    from src.transform.returns_and_dma import compute_returns_and_dma
    from src.db_utils import load_price_dict_from_db

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    dates = generate_rebalance_dates(start_dt, end_dt, step_days=30)

    flagged = []
    for d in dates:
        price_histories = load_price_dict_from_db(engine, as_of_date=d, ticker=[])
        pool = compute_returns_and_dma(price_histories, return_window_months, dma_window_days, top_n=top_n_pool)
        outliers = pool[pool["return_pct"] > outlier_threshold_pct]
        if not outliers.empty:
            for _, row in outliers.iterrows():
                flagged.append({"rebalance_date": d.date(), **row.to_dict()})

    if flagged:
        print(f"\n=== {len(flagged)} outlier return_pct entries (> {outlier_threshold_pct}%) found in top-{top_n_pool} pool ===")
        print(pd.DataFrame(flagged))
    else:
        print(f"\nNo return_pct outliers above {outlier_threshold_pct}% found — data likely clean on this front.")
    return flagged


def print_holdings_over_time(log_df, every_n=5):
    """Prints the actual shortlist tickers + their return_pct/sector at a sample
    of rebalance dates, so you can eyeball whether picks look like real,
    familiar large/mid-cap names or oddities."""
    for i, row in log_df.iloc[::every_n].iterrows():
        print(f"\n--- {row['date']} | portfolio_value={row['portfolio_value']:.0f} ---")
        shortlist = row["shortlist"]
        if not shortlist:
            print("  (empty / cash)")
            continue
        for t, data in shortlist.items():
            # data = [latest_price, return_pct, dma, above_dma, sector]
            print(f"  {t:15s} return_pct={data[1]:8.2f}  sector={data[4]}")


def period_over_period_returns(log_df):
    """Simple monthly return series from the value log — quickly shows if a
    handful of months are doing all the heavy lifting."""
    vals = log_df["portfolio_value"]
    pct_change = vals.pct_change() * 100
    out = pd.DataFrame({"date": log_df["date"], "portfolio_value": vals, "period_return_pct": pct_change})
    print("\n=== Period-over-period returns ===")
    print(out)
    print("\nBiggest single-period jumps:")
    print(out.nlargest(5, "period_return_pct"))


if __name__ == "__main__":
    START = "2022-07-06"
    END = "2026-07-03"

    print("Step 1: checking for return_pct outliers (possible bad splits / data glitches)...")
    check_for_return_outliers(START, END)

    print("\nStep 2: re-running backtest to inspect holdings and period returns...")
    log_df, metrics = backtest_engine(
        capital=100000, start_date=START, end_date=END, data_engine=engine,
        return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
        weight_type="equal",
    )
    print_holdings_over_time(log_df, every_n=6)
    period_over_period_returns(log_df)
    print("\nMetrics:", metrics)