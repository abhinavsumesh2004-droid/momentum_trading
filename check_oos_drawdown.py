"""
check_oos_drawdown.py — quick standalone check: out-of-sample CAGR AND
drawdown for all 8 configs at capital=30000, without rerunning the full
walk-forward + rolling-windows + Nifty50 suite. Reuses the exact same
CONFIGS/SPLIT_DATE/FULL_END/CAPITAL already defined in full_backtest_30k.py.

Run with:  python check_oos_drawdown.py
"""
from src.db_utils import get_engine
from src.backtest.backtest_engine import run_config_sweep
from full_backtest_30k import CONFIGS, SPLIT_DATE, FULL_END, CAPITAL

if __name__ == "__main__":
    engine = get_engine()
    oos = run_config_sweep(engine, CAPITAL, SPLIT_DATE, FULL_END, CONFIGS)
    print("\nOUT-OF-SAMPLE — CAGR and drawdown, all 8 configs:")
    print(oos[["config_id", "cagr_pct", "max_drawdown_pct"]].to_string(index=False))
