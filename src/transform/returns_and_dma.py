import pandas as pd
import numpy as np
from src.db_utils import get_engine, load_price_dict_from_db
from src.extract.fetch_prices import populate_sectors
import yfinance as yf
from sqlalchemy import text



def compute_returns_and_dma(price_histories: dict[str, pd.DataFrame],
                             return_window_months: int,
                             dma_window_days: int,
                             skip_recent_days: int = 0,
                             top_n: int = 30) -> pd.DataFrame:
    rows = []
    approx_trading_days_per_month = 21

    for ticker, df in price_histories.items():
        closes = df["Close"].dropna()
        if len(closes) < dma_window_days:
            continue  # not enough history to compute a reliable 200 DMA

        latest_price = closes.iloc[-1]
        dma = closes.rolling(window=dma_window_days).mean().iloc[-1]

        lookback_days = return_window_months * approx_trading_days_per_month
        end_idx = len(closes) - 1 - skip_recent_days
        start_idx = end_idx - lookback_days
        if start_idx < 0:
            continue

        start_price = closes.iloc[start_idx]
        end_price = closes.iloc[end_idx]
        period_return = (end_price - start_price) / start_price
        #print(closes.index[start_idx], closes.index[end_idx], start_price, end_price, period_return)

        # Volatility of daily returns over the SAME window used for period_return
        # above — this is the denominator half of the "volatility-adjusted momentum
        # score" from the build-plan doc (return / volatility). Used here as a
        # WEIGHTING input (see weights.vol_adjusted_weights), not (yet) as the
        # ranking signal itself — the screen still ranks by raw return_pct.
        window_closes = closes.iloc[start_idx:end_idx + 1]
        daily_returns = window_closes.pct_change().dropna()
        volatility_pct = round(float(daily_returns.std() * 100), 4) if len(daily_returns) >= 2 else None

        rows.append({
            "ticker": ticker,
            "latest_price": round(float(latest_price), 2),
            "dma": round(float(dma), 2),
            "above_dma": bool(latest_price > dma),
            "return_pct": round(float(period_return) * 100, 2),
            "volatility_pct": volatility_pct,
        })

    result_df = pd.DataFrame(rows)
    if result_df.empty:
        return result_df

    # DMA trend filter: only keep stocks currently trading above their DMA.
    # (Previously computed into a discarded local variable and never applied —
    # fixed here so the filter actually affects the returned pool.)
    result_df = result_df[result_df["above_dma"] == True]

    return (
        result_df
        .sort_values(by="return_pct", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )





data_engine = get_engine()

# NOTE: the block below was previously top-level module code that ran on
# every import (hits the DB, prints results). Guarded behind __main__ so
# importing get_top_companies/compute_returns_and_dma elsewhere (e.g. from
# backtest_engine.py) doesn't trigger it repeatedly or unexpectedly.
if __name__ == "__main__":
    dma_window_days = 200
    return_window_months = 12
    ticker = []
    df = pd.read_sql("SELECT ticker FROM stocks", data_engine)
    for t in df['ticker']:
        ticker.append(t)
    dma = compute_returns_and_dma(load_price_dict_from_db(data_engine, ticker=ticker), return_window_months, dma_window_days)
    print(type(dma))

def transform_to_dict(df: pd.DataFrame,engine) -> dict[str, list]:
    final_dict = {}
    for i in range(len(df)):
        final_dict[df.iloc[i]['ticker']] = [df.iloc[i]['latest_price'], df.iloc[i]['return_pct'], df.iloc[i]['dma'], df.iloc[i]['above_dma']]
    result = populate_sectors(final_dict, data_engine)
    return result


def sector_selection(engine, top_stocks,stock_count=10,max_per_sector=2):
    total_count = 0
    sector_count = {}
    stock_dict = {} 
    sorted_stocks = sorted(top_stocks.items(), key=lambda x: x[1][1], reverse=True)
    
    for ticker, data in sorted_stocks:
        if total_count >= stock_count:
            break
            
        sector = data[-1]
        
        if sector not in sector_count:
            sector_count[sector] = 0
            
        if sector_count[sector] < max_per_sector:
            sector_count[sector] += 1
            total_count += 1
            stock_dict[ticker] = data
            
    return stock_dict

def get_top_companies(ticker: list[str], return_window_months: int, dma_window_days: int, data_engine,
                       as_of_date=None, stock_count: int = 10, max_per_sector: int = 2,
                       return_volatility: bool = False):
    """as_of_date restricts price history to <= that date — required for
    historical backtesting so each rebalance only sees data that would
    actually have been available at that point in time (no look-ahead).
    Leave as_of_date=None for live/current-day use.

    stock_count/max_per_sector default to the values sector_selection() used
    to hardcode (10 / 2) — override these to match the agreed baseline
    (top-20, max 2-3 per sector) rather than letting them silently drift.

    return_volatility=True additionally returns {ticker: volatility_pct} for
    every ticker in the FINAL (post-sector-cap) shortlist, sourced from the
    same top-30 pool computed here — needed by weight_type='vol_adjusted' in
    backtest_engine/live_run. Default False preserves the exact previous
    return type (just the shortlist dict) for every existing caller."""
    # Enough calendar days to cover whichever is longer: the return-window
    # lookback or the DMA window, in trading days, plus a buffer for
    # weekends/holidays (~1.6x calendar:trading ratio on NSE) and margin.
    required_trading_days = max(dma_window_days, return_window_months * 21)
    lookback_days = int(required_trading_days * 1.6) + 30

    price_histories = load_price_dict_from_db(data_engine, as_of_date=as_of_date, lookback_days=lookback_days, ticker=ticker)
    dma = compute_returns_and_dma(price_histories, return_window_months, dma_window_days, top_n=30)
    result = transform_to_dict(dma, data_engine)
    sector_wise = sector_selection(engine=data_engine, top_stocks=result,
                                    stock_count=stock_count, max_per_sector=max_per_sector)

    if return_volatility:
        vol_lookup = dma.set_index("ticker")["volatility_pct"].to_dict()
        volatility_dict = {t: vol_lookup.get(t) for t in sector_wise.keys()}
        return sector_wise, volatility_dict
    return sector_wise

def update_running_peak():
    query = '''
        UPDATE PORTFOLIO_LOG A
        SET RUNNING_PEAK_PRICE = (
            SELECT MAX(CLOSE)
            FROM PRICE_HISTORY P
            WHERE P.DATE >= A.ENTRY_DATE
              AND P.TICKER = A.TICKER
        )
        WHERE A.STATUS = 'OPEN';
    '''
    engine = get_engine()
    try:
        with engine.begin() as conn:  # begin() auto-commits on success, rolls back on exception
            result = conn.execute(text(query))
            print(f"update_running_peak: {result.rowcount} open position(s) updated.")
    except Exception as e:
        print(f"update_running_peak failed, no changes committed: {e}")
        raise

def check_trail_exit(drop_pct: float = 20.0):
    """Trailing-stop-from-peak exit check — this is the "x% fall from top"
    logic. Run this manually/weekly, on your own schedule (unlike
    live_run.py's quarterly-gated rebalance, this is meant to be run any
    time you want).

    drop_pct: how far a stock may fall from its RUNNING_PEAK_PRICE (highest
    close since entry, tracked in portfolio_log — updated by
    update_running_peak() below) before it's flagged. Build-plan doc's
    default was 15-20%; 20 is the current default here.

    For each OPEN position where latest close <= peak * (1 - drop_pct/100),
    prints the ticker and asks Y/N to confirm the exit — nothing is closed
    automatically. Confirmed exits get EXIT_DATE/EXIT_PRICE/STATUS updated
    in portfolio_log (EXIT_REASON = 'trailing_stop').
    """
    update_running_peak()
    engine = get_engine()
    threshold = 1 - (drop_pct / 100)
    query = text('''
    SELECT A.TICKER
    FROM PORTFOLIO_LOG A
    WHERE A.STATUS = 'OPEN'
    AND A.RUNNING_PEAK_PRICE * :threshold >= (
        SELECT P.CLOSE
        FROM PRICE_HISTORY P
        WHERE P.TICKER = A.TICKER
        ORDER BY P.DATE DESC
        LIMIT 1
    );''')

    flagged = pd.read_sql(query, engine, params={"threshold": threshold})


    if flagged.empty:
        print(f"No positions breached the {drop_pct:.0f}% trailing stop.")
        return

    exited_tickers = []

    for t in flagged["ticker"]:                     # iterate the column, not the DataFrame
        print(f"\n{t} has fallen {drop_pct:.0f}%+ from its peak.")
        choice = input("Y to confirm exit, N to hold: ").strip().upper()
        if choice == "Y":
            exited_tickers.append(t)

    if not exited_tickers:
        print("No exits confirmed.")
        return

    query2 = '''
        UPDATE PORTFOLIO_LOG P
        SET EXIT_DATE   = CURRENT_DATE,
            EXIT_REASON = 'trailing_stop',
            STATUS      = 'CLOSED',
            EXIT_PRICE  = (
                SELECT A.CLOSE
                FROM PRICE_HISTORY A
                WHERE A.TICKER = P.TICKER
                ORDER BY A.DATE DESC
                LIMIT 1
            )
        WHERE P.TICKER = ANY(:tickers)
          AND P.STATUS = 'OPEN';
    '''

    try:
        with engine.begin() as conn:
            result = conn.execute(text(query2), {"tickers": exited_tickers})
            print(f"\n{result.rowcount} position(s) closed: {exited_tickers}")
    except Exception as e:
        print(f"check_trail_exit failed, no changes committed: {e}")
        raise

if __name__ == "__main__":
    result = get_top_companies(ticker=[], dma_window_days=200, return_window_months=12, data_engine=data_engine)
    print(result)