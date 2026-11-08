"""
check_data_gaps.py — checks whether every ticker in `stocks` has reasonably
complete price_history coverage across the backtest window. Run this before
trusting ANY backtest number, and especially if two runs of the same
config/period gave meaningfully different results — that's the symptom of
exactly what this catches: a ticker with a silent historical gap flipping
in/out of the 200-day DMA eligibility check between runs, reshuffling the
shortlist at various historical rebalance dates.

Flags tickers whose number of rows within [start, end] is well below what a
fully-populated ticker should have (~247 trading days/year), and any ticker
with a single gap of more than ~10 calendar days (accounts for holidays/
weekends; NSE doesn't go quiet for 10 days under normal circumstances).
"""
import pandas as pd
from src.db_utils import get_engine

START = "2022-03-01"
END = "2026-07-10"
EXPECTED_ROWS_PER_YEAR = 247
MIN_ACCEPTABLE_FRACTION = 0.85  # flag if a ticker has less than this fraction of expected rows
MAX_GAP_DAYS = 10

if __name__ == "__main__":
    engine = get_engine()
    df = pd.read_sql(
        "SELECT ticker, date FROM price_history WHERE date BETWEEN %(s)s AND %(e)s ORDER BY ticker, date",
        engine, params={"s": START, "e": END},
    )
    universe = pd.read_sql("SELECT ticker FROM stocks", engine)["ticker"].tolist()

    years = (pd.Timestamp(END) - pd.Timestamp(START)).days / 365.25
    expected_rows = EXPECTED_ROWS_PER_YEAR * years

    present_tickers = set(df["ticker"].unique())
    missing_entirely = sorted(set(universe) - present_tickers)

    print(f"Window: {START} to {END} ({years:.1f} years, ~{expected_rows:.0f} rows expected per fully-covered ticker)\n")

    if missing_entirely:
        print(f"{len(missing_entirely)} ticker(s) with ZERO rows in this window (likely listed after {START}, or never fetched):")
        for t in missing_entirely:
            print(f"  {t}")
        print()

    sparse = []
    gapped = []
    for t, g in df.groupby("ticker"):
        n = len(g)
        if n < expected_rows * MIN_ACCEPTABLE_FRACTION:
            sparse.append((t, n))
        dates = pd.to_datetime(g["date"]).sort_values()
        gaps = dates.diff().dt.days.dropna()
        max_gap = gaps.max() if not gaps.empty else 0
        if max_gap > MAX_GAP_DAYS:
            gapped.append((t, int(max_gap), dates[gaps.idxmax()].date() if not gaps.empty else None))

    if sparse:
        print(f"{len(sparse)} ticker(s) with suspiciously few rows (< {MIN_ACCEPTABLE_FRACTION:.0%} of expected):")
        for t, n in sorted(sparse, key=lambda x: x[1]):
            print(f"  {t}: {n} rows (expected ~{expected_rows:.0f})")
        print()

    if gapped:
        print(f"{len(gapped)} ticker(s) with a single gap > {MAX_GAP_DAYS} days somewhere in this window:")
        for t, gap, gap_end in sorted(gapped, key=lambda x: -x[1]):
            print(f"  {t}: {gap}-day gap ending around {gap_end}")
        print()

    if not missing_entirely and not sparse and not gapped:
        print("No gaps found — data looks complete for this window. The run-to-run "
              "difference likely has a different cause; worth re-running check_oos_drawdown.py "
              "twice in a row right now to see if it's even reproducible at all.")
    else:
        print("Any ticker listed above is a candidate for the shifting-shortlist effect described "
              "in the script docstring. Consider a full backfill for these tickers (get_price_range) "
              "before trusting backtest comparisons.")
