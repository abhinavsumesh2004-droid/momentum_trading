"""
compare_configs.py — runs the 4 candidate configs (the same ones in the
backtest_engine sweep: config 4/5/6/7) against TODAY's real shortlist, at
whatever capital you're actually about to deploy, and prints how each one
handles the whole-share rounding problem.

Purely read/compute — refreshes prices, screens, weights, allocates, prints.
Nothing is written to screen_runs or portfolio_log. Safe to run any time,
as many times as you want, including within the 80-day preview window.

This does NOT replace the backtest_engine sweep. That answers "which config
performed best historically." This answers "which config actually deploys
cleanly at MY real capital, today." You want both before deciding — a
config can win on backtest CAGR/drawdown and still be a poor fit if it
leaves 30% of your capital sitting in leftover cash at your actual size.
"""
import datetime
import pandas as pd

from src.db_utils import get_engine
from src.transform.returns_and_dma import get_top_companies
from src.backtest.weights import get_weights
from src.backtest.backtest_engine import get_allocation_efficient, get_portfolio_value
from src.portfolio.delta import get_current_holdings
from src.extract.fetch_prices import refresh_latest_prices

# Same universe of configs as the backtest_engine sweep (configs 4/5/6/7),
# minus rebalance_step_days/return_window/dma which don't matter for a
# single point-in-time allocation check.
CANDIDATE_CONFIGS = [
    {"label": "20 / equal        (config 4 — current live default)", "stock_count": 20, "weight_type": "equal"},
    {"label": "20 / vol_adjusted (config 5)",                         "stock_count": 20, "weight_type": "vol_adjusted"},
    {"label": "15 / equal        (config 6)",                         "stock_count": 15, "weight_type": "equal"},
    {"label": "15 / vol_adjusted (config 7)",                         "stock_count": 15, "weight_type": "vol_adjusted"},
]

RETURN_WINDOW_MONTHS = 6
DMA_WINDOW_DAYS = 200
MAX_PER_SECTOR = 3


def compare(cash_to_add: float, refresh: bool = True):
    engine = get_engine()
    universe = pd.read_sql("SELECT ticker FROM stocks", engine)["ticker"].tolist()

    if refresh:
        refresh_latest_prices(engine)

    holdings = get_current_holdings(engine)
    portfolio_value, _ = get_portfolio_value(engine, holdings, leftover_cash=0, as_of_date=datetime.date.today())
    capital = portfolio_value + cash_to_add
    print(f"\nCurrent holdings marked to market: {portfolio_value:.2f}")
    print(f"+ cash you're adding: {cash_to_add:.2f}")
    print(f"Total deployable capital: {capital:.2f}\n")
    print(f"{'Config':<45} {'Skipped':>8} {'Invested':>12} {'Leftover':>10} {'Leftover %':>11}")
    print("-" * 90)

    results = []
    for cfg in CANDIDATE_CONFIGS:
        needs_vol = cfg["weight_type"] == "vol_adjusted"
        if needs_vol:
            shortlist, volatility = get_top_companies(
                ticker=universe, return_window_months=RETURN_WINDOW_MONTHS,
                dma_window_days=DMA_WINDOW_DAYS, data_engine=engine,
                stock_count=cfg["stock_count"], max_per_sector=MAX_PER_SECTOR,
                return_volatility=True,
            )
        else:
            shortlist = get_top_companies(
                ticker=universe, return_window_months=RETURN_WINDOW_MONTHS,
                dma_window_days=DMA_WINDOW_DAYS, data_engine=engine,
                stock_count=cfg["stock_count"], max_per_sector=MAX_PER_SECTOR,
            )
            volatility = None

        weights = get_weights(cfg["weight_type"], shortlist, volatility=volatility)
        # verbose=False here on purpose — we just want the summary row per
        # config, not 4x the SKIPPED-line noise. Re-run a single config with
        # verbose=True (see __main__ below) if you want the per-ticker detail.
        allocation, leftover = get_allocation_efficient(shortlist, capital, weights, verbose=False)

        prices = {t: shortlist[t][0] for t in shortlist}
        skipped = sum(1 for t, units in allocation.items() if units == 0)
        invested = capital - leftover

        print(f"{cfg['label']:<45} {skipped:>8} {invested:>12.2f} {leftover:>10.2f} {leftover / capital * 100:>10.2f}%")
        results.append({**cfg, "shortlist": shortlist, "allocation": allocation, "leftover": leftover})

    print("\nLower 'Skipped' and lower 'Leftover %' = more of your target list actually got funded.")
    print("This tells you what deploys cleanly TODAY — it says nothing about which config")
    print("performs better over time. Check the backtest_engine sweep for that before deciding.")
    return results


if __name__ == "__main__":
    compare(cash_to_add=30000)
