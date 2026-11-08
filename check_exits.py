from src.db_utils import get_engine
from sqlalchemy import text
import pandas as pd
from src.extract.fetch_prices import refresh_latest_prices
from src.portfolio.delta import get_current_holdings
from src.transform.returns_and_dma import update_running_peak, check_trail_exit  



def run_exit_check(drop_pct: float = 20.0):
    engine = get_engine()
    held_tickers = list(get_current_holdings(engine).keys())
    if not held_tickers:
        print("No open positions — nothing to check.")
        return

    refresh_latest_prices(engine, tickers=held_tickers)
    check_trail_exit(drop_pct=drop_pct)  # also updates running_peak internally

if __name__ == "__main__":
    run_exit_check()