from src.db_utils import get_engine
from src.backtest.backtest_engine import walk_forward_test,run_rolling_windows,run_rolling_windows_multi

data_engine = get_engine()

configs = [
    dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
         weight_type="equal", rebalance_step_days=30),
    dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=2,
         weight_type="equal", rebalance_step_days=30),
    dict(return_window_months=6, dma_window_days=200, stock_count=15, max_per_sector=3,
         weight_type="rank", rebalance_step_days=30),
    dict(return_window_months=12, dma_window_days=200, stock_count=20, max_per_sector=3,
         weight_type="equal", rebalance_step_days=30),
    dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
         weight_type="equal", rebalance_step_days=90),
]

'''results = walk_forward_test(
    data_engine=data_engine,
    capital=100000,
    full_start="2017-11-01",   # <- moved forward, clears warm-up for ALL configs including config 3
    split_date="2022-03-01",   # <- roughly the midpoint of the remaining span
    full_end="2026-07-10",     # <- matches your confirmed data end
    configs=configs,
    rank_metric="cagr_pct",
)
print(results)'''

'''rolling_results = run_rolling_windows(
        data_engine=data_engine,
        capital=100000,
        config=configs,
        overall_start="2017-08-01",  # skip the ~13-month warm-up so every window has full lookback
        overall_end="2026-07-03",
        window_years=1,
        step_months=3,
    '''

'''rolling_results_multi = run_rolling_windows_multi(
    data_engine=data_engine,
    capital=100000,
    configs=configs,
    overall_start="2017-08-01",  # skip the ~13-month warm-up so every window has full lookback
    overall_end="2026-07-03",
    window_years=1,
    step_months=3,
)'''

# You can now filter `rolling_results_multi` by 'config_id' if you want to inspect specific runs
# print(rolling_results_multi)


import pandas as pd

df = pd.read_csv('data/ind_nifty50list.csv')
ticker = (df['Symbol'] + '.NS').tolist()


results = walk_forward_test(
    data_engine=data_engine,
    capital=100000,
    full_start="2017-11-01",   # <- moved forward, clears warm-up for ALL configs including config 3
    split_date="2022-03-01",   # <- roughly the midpoint of the remaining span
    full_end="2026-07-10",     # <- matches your confirmed data end
    configs=configs,
    rank_metric="cagr_pct",
    ticker=ticker
)
print(results)

