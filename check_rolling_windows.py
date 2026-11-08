"""
check_rolling_windows.py — rolling-window comparison of ONLY config 4
(equal-weight, the current live default) vs config 5 (vol_adjusted), the
two live candidates from the OOS drawdown check. Skips configs 0/1/2/3/6/7
since those are already decided against — keeps this fast and focused on
the actual decision.

Adds a drawdown distribution table alongside CAGR, since
run_rolling_windows_multi's built-in summary only prints CAGR stats
(median/min/max/win-rate) — drawdown is exactly what separated config 4
from config 2 earlier, so it can't be skipped here either.

Run with:  python check_rolling_windows.py
"""
import pandas as pd
from src.db_utils import get_engine
from src.backtest.backtest_engine import run_rolling_windows_multi
from full_backtest_30k import CONFIGS, FULL_START, FULL_END, CAPITAL

CANDIDATE_CONFIGS = [CONFIGS[4], CONFIGS[5]]  # 4 = equal, 5 = vol_adjusted
LABELS = {0: "config 4 (equal)", 1: "config 5 (vol_adjusted)"}  # positional id within this 2-item list

if __name__ == "__main__":
    engine = get_engine()
    result_df = run_rolling_windows_multi(
        data_engine=engine, capital=CAPITAL, configs=CANDIDATE_CONFIGS,
        overall_start=FULL_START, overall_end=FULL_END,
        window_years=1, step_months=3,
    )

    print("\nDRAWDOWN DISTRIBUTION (same windows, not in the built-in summary above):")
    dd_rows = []
    for cfg_id in result_df["config_id"].unique():
        dd = result_df[result_df["config_id"] == cfg_id]["max_drawdown_pct"].dropna()
        if not dd.empty:
            dd_rows.append({
                "config_id": cfg_id, "label": LABELS.get(cfg_id, cfg_id),
                "median_drawdown_pct": dd.median(), "worst_drawdown_pct": dd.min(),
                "best_drawdown_pct": dd.max(), "window_count": len(dd),
            })
    print(pd.DataFrame(dd_rows).set_index("config_id").round(2).to_string())

    print("\nRead both tables together: a config only wins cleanly if it's ahead (or")
    print("roughly tied) on BOTH median CAGR and median/worst drawdown across most")
    print("windows — not just one metric in one window, which is how config 2 fooled")
    print("the very first single-split test.")
