"""
full_backtest_30k.py — re-runs every backtest validation test from the
context docs at capital=30000 (your real deployment amount) instead of the
₹100,000 every prior test used, across all 8 configs (the original 5 +
vol_adjusted / stock_count=15 variants added since).

WHY THIS ISN'T REDUNDANT: v4 doc Section 2 explicitly flagged an open,
unresolved question — "whether this [whole-share-rounding] bias disappears
at larger capital scale (e.g. ₹10L+) hasn't been tested yet." You're now
going the OTHER direction (smaller, not larger), which is arguably more
directly relevant since it's your actual capital — but it's the same open
question, just finally getting answered. At ₹100k, fixing the leftover-cash
sweep moved one config's CAGR by ~5 points (25.14% -> 30.35%) — that's how
much this friction can matter. At ₹30k it could matter more, less, or
differently across configs; this is what actually tells you.

Runs, in order:
  1. NIFTY 500 walk-forward (Run-2 corrected methodology/dates from v4 §7)
  2. NIFTY 500 rolling windows, 1y/3mo-step, all 8 configs (v4 §7)
  3. NIFTY 50 walk-forward, same dates (survivorship-bias cross-check, v4 §6)

HEAVY RUN WARNING: each walk-forward call sweeps 8 configs x 2 periods,
each config re-screening the full universe at every historical rebalance
date across a ~9-year window. This will take a while — if it's too slow
end-to-end on your machine, comment out sections in __main__ and run them
as separate sessions instead of all three back to back.
"""
import pandas as pd
from src.db_utils import get_engine
from src.backtest.backtest_engine import walk_forward_test, run_rolling_windows_multi

CAPITAL = 30000  # the only thing changed vs. every prior documented run

# Same 8 configs as backtest_engine.py's __main__ sweep (0-4 original, 5-7 new).
# reduce_idle_cash=True on every config — WITHOUT this, backtest_engine()
# silently uses plain get_allocation() (no leftover-cash sweep), not
# get_allocation_efficient(). That's the exact fix v4 doc §2 says moved one
# config's CAGR from 25.14% to 30.35% at ₹100k capital — leaving it off here
# was a bug, not a deliberate choice, and gets worse (not better) at ₹30k.
CONFIGS = [
    dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
         weight_type="equal", rebalance_step_days=30, reduce_idle_cash=True),                                     # 0
    dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=2,
         weight_type="equal", rebalance_step_days=30, reduce_idle_cash=True),                                     # 1
    dict(return_window_months=6, dma_window_days=200, stock_count=15, max_per_sector=3,
         weight_type="rank", rebalance_step_days=30, reduce_idle_cash=True),                                      # 2
    dict(return_window_months=12, dma_window_days=200, stock_count=20, max_per_sector=3,
         weight_type="equal", rebalance_step_days=30, reduce_idle_cash=True),                                     # 3
    dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
         weight_type="equal", rebalance_step_days=90, reduce_idle_cash=True),                                     # 4 — previous winner
    dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
         weight_type="vol_adjusted", rebalance_step_days=90, reduce_idle_cash=True),                              # 5 — new
    dict(return_window_months=6, dma_window_days=200, stock_count=15, max_per_sector=3,
         weight_type="equal", rebalance_step_days=90, reduce_idle_cash=True),                                     # 6 — new
    dict(return_window_months=6, dma_window_days=200, stock_count=15, max_per_sector=3,
         weight_type="vol_adjusted", rebalance_step_days=90, reduce_idle_cash=True),                              # 7 — new
]

# Corrected Run-2 dates (v4 doc §7) — full_start is pushed past the warm-up
# window so every config (including the 12-month-lookback one) gets a valid
# first screen with zero forced-cash warm-up months. Kept IDENTICAL to the
# original run so capital is the only variable that changed.
FULL_START = "2017-11-01"
SPLIT_DATE = "2022-03-01"
FULL_END = "2026-07-10"  # matches the original run exactly for a clean before/after.
# Your price_history now runs through ~2026-08-27 if you'd rather extend this —
# just know that also changes the OOS window length, not just the capital.


def load_nifty50_tickers() -> list[str]:
    df = pd.read_csv("data/ind_nifty50list.csv")
    return (df["Symbol"].str.strip() + ".NS").tolist()


def _print_full_result(merged: pd.DataFrame):
    """walk_forward_test's internal prints only show cagr_pct — max_drawdown_pct
    is computed and returned in the merged DataFrame but was never printed.
    Drawdown is what separated config 4 from config 2 at ₹100k (config 2 won
    on in-sample CAGR, then had the WORST out-of-sample drawdown) — judging
    only on the CAGR-only console output risks repeating exactly that mistake
    in the other direction. Always check this table, not just the CAGR one."""
    print("\nFULL RESULT (CAGR + drawdown, in-sample vs out-of-sample, all configs):")
    cols = ["config_id", "cagr_pct_in_sample", "max_drawdown_pct_in_sample",
            "cagr_pct_out_of_sample", "max_drawdown_pct_out_of_sample",
            "in_sample_rank", "out_of_sample_rank"]
    print(merged[[c for c in cols if c in merged.columns]].to_string(index=False))


def run_nifty500_walk_forward(engine):
    print("\n" + "=" * 80)
    print(f"NIFTY 500 WALK-FORWARD  —  capital=₹{CAPITAL:,}")
    print("=" * 80)
    result = walk_forward_test(
        data_engine=engine, capital=CAPITAL,
        full_start=FULL_START, split_date=SPLIT_DATE, full_end=FULL_END,
        configs=CONFIGS, rank_metric="cagr_pct",
    )
    _print_full_result(result)
    return result


def run_nifty500_rolling(engine):
    print("\n" + "=" * 80)
    print(f"NIFTY 500 ROLLING WINDOWS (1y, 3mo step), all 8 configs  —  capital=₹{CAPITAL:,}")
    print("=" * 80)
    return run_rolling_windows_multi(
        data_engine=engine, capital=CAPITAL, configs=CONFIGS,
        overall_start=FULL_START, overall_end=FULL_END,
        window_years=1, step_months=3,
    )


def run_nifty50_walk_forward(engine):
    print("\n" + "=" * 80)
    print(f"NIFTY 50 WALK-FORWARD (survivorship-bias cross-check)  —  capital=₹{CAPITAL:,}")
    print("=" * 80)
    nifty50_tickers = load_nifty50_tickers()
    result = walk_forward_test(
        data_engine=engine, capital=CAPITAL,
        full_start=FULL_START, split_date=SPLIT_DATE, full_end=FULL_END,
        configs=CONFIGS, rank_metric="cagr_pct", ticker=nifty50_tickers,
    )
    _print_full_result(result)
    return result


if __name__ == "__main__":
    engine = get_engine()

    nifty500_wf = run_nifty500_walk_forward(engine)
    nifty500_roll = run_nifty500_rolling(engine)
    nifty50_wf = run_nifty50_walk_forward(engine)

    print("\n" + "=" * 80)
    print("DONE. The comparison that actually matters: does config_id 4's")
    print("out-of-sample rank/CAGR/drawdown here still beat configs 5/6/7")
    print("(the vol_adjusted / stock_count=15 variants), and does its rank/CAGR/")
    print("drawdown roughly match the ₹100,000 numbers in v4 doc §6/§7? If both")
    print("hold, capital scale isn't changing your conclusion. If either doesn't,")
    print("that's real, new information — not something the earlier docs covered.")
    print("=" * 80)
