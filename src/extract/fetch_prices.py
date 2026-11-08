from sqlalchemy import create_engine
import pandas as pd
import yfinance as yf
import time

engine = create_engine("postgresql://postgres:1234@localhost:5432/momentum")





def get_price(ticker: list, period: str, interval: str = "1d", chunksize: int = 50,auto_adjust: bool = True) -> pd.DataFrame:
    valid_suffixes = ('d', 'mo', 'y')
    if not period.endswith(valid_suffixes):
        raise ValueError(f"Invalid period format: {period}")
    
    all_data = []
    for i in range(0, len(ticker), chunksize):
        chunk = ticker[i:i + chunksize]
        try:
            data = yf.download(chunk, period=period, interval=interval,
                                group_by='ticker', auto_adjust=auto_adjust, actions=False,
                                progress=True, threads=True)
            data_reshaped = reshape_to_long(data, chunk)
            all_data.append(data_reshaped)
        except Exception as e:
            print(f"Error fetching data for chunk {chunk}: {e}")
            continue
        time.sleep(5)
    return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()


def get_price_range(ticker: list, start: str, end: str, interval: str = "1d", chunksize: int = 50,
                     auto_adjust: bool = True) -> pd.DataFrame:
    """Same chunked-download pattern as get_price(), but for an explicit
    [start, end) date range instead of a 'period back from today' window.
    Needed for backfilling older history — period='5y' always means the
    5 years ending today, so it can't be used to pull the 5 years BEFORE
    what's already in the DB."""
    all_data = []
    for i in range(0, len(ticker), chunksize):
        chunk = ticker[i:i + chunksize]
        try:
            data = yf.download(chunk, start=start, end=end, interval=interval,
                                group_by='ticker', auto_adjust=auto_adjust, actions=False,
                                progress=True, threads=True)
            data_reshaped = reshape_to_long(data, chunk)
            all_data.append(data_reshaped)
        except Exception as e:
            print(f"Error fetching data for chunk {chunk}: {e}")
            continue
        time.sleep(5)
    return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()


def reshape_to_long(data, tickers):
    records = []
    for t in tickers:
        try:
            df = data[t][['Close', 'Volume']].dropna()
            df = df.reset_index().rename(columns={'Date': 'date', 'Close': 'close', 'Volume': 'volume'})
            df['ticker'] = t
            records.append(df)
        except (KeyError, TypeError):
            print(f"No data for {t}, skipping")
    return pd.concat(records, ignore_index=True) if records else pd.DataFrame()







def write_to_db(engine):
    tickers_df=pd.read_sql("SELECT ticker FROM stocks", engine)
    tickdf=tickers_df['ticker'].tolist()
    data = get_price(tickdf, period="5y", interval="1d")
    if not data.empty:
        data.to_sql("price_history", engine, if_exists="append", index=False, method="multi", chunksize=1000)
    else:
        print("No data to write to the database.")


def upsert_price_history(engine, df: pd.DataFrame):
    """Loads df through a staging table then INSERT ... ON CONFLICT DO NOTHING
    into price_history. Needed for backfill: a plain to_sql(append) would
    crash on the (ticker, date) primary key if the new range accidentally
    overlaps existing rows by even a day (holiday/weekend edge cases), and
    silently doing nothing on conflict is the right behavior here — we're
    not trying to overwrite existing validated data, just fill in older gaps."""
    from sqlalchemy import text
    if df.empty:
        print("Nothing to upsert — empty DataFrame.")
        return
    with engine.begin() as conn:
        df.to_sql("price_history_staging", conn, if_exists="replace", index=False, method="multi", chunksize=1000)
        conn.execute(text("""
            INSERT INTO price_history (ticker, date, close, volume)
            SELECT ticker, date, close, volume FROM price_history_staging
            ON CONFLICT (ticker, date) DO NOTHING
        """))
        conn.execute(text("DROP TABLE price_history_staging"))
    print(f"Upsert complete: {len(df)} rows processed (existing dates skipped, not overwritten).")


def backfill_history(engine, years_back: int = 5):
    """Pulls `years_back` additional years of history immediately BEFORE
    whatever is currently the earliest date in price_history, and safely
    merges it in. Run this once to extend a 5-year table to 10 years for a
    longer backtest window (e.g. one that includes the COVID crash / a
    less momentum-friendly period, per the earlier discussion on how much
    the recent rally window might be inflating backtest CAGR).

    Safe to re-run: existing rows are never touched (ON CONFLICT DO NOTHING).
    """
    tickers_df = pd.read_sql("SELECT ticker FROM stocks", engine)
    tickers = tickers_df["ticker"].tolist()
    if not tickers:
        print("No tickers found in stocks table — nothing to backfill.")
        return

    existing_min = pd.read_sql("SELECT MIN(date) AS min_date FROM price_history", engine)["min_date"].iloc[0]
    if existing_min is None:
        raise ValueError(
            "price_history is empty — run write_to_db() first to establish the "
            "existing baseline before backfilling further history."
        )
    existing_min = pd.to_datetime(existing_min)

    new_end = existing_min - pd.Timedelta(days=1)
    new_start = new_end - pd.DateOffset(years=years_back)

    print(f"Backfilling {years_back}y of history for {len(tickers)} tickers: "
          f"{new_start.date()} to {new_end.date()}")

    data = get_price_range(
        tickers,
        start=new_start.strftime("%Y-%m-%d"),
        end=new_end.strftime("%Y-%m-%d"),
        interval="1d",
    )

    if data.empty:
        print("No data returned for the backfill range — nothing written.")
        return

    upsert_price_history(engine, data)
    print(f"Backfill done. price_history now covers as far back as ~{new_start.date()} "
          f"(subject to each ticker's actual listing history).")


def populate_sectors(ticker:dict,engine):
    sectors = {}
    for i in ticker.keys():
        sector=pd.read_sql("SELECT sector FROM stocks WHERE ticker='{}'".format(i),engine)
        sectors[i] = sector.iloc[0]['sector'] if not sector.empty else None
    for t in ticker.keys():
        ticker[t].append(sectors[t])
    return ticker


def refresh_latest_prices(engine, tickers: list = None):
    """
    Pulls fresh price data from (latest existing date + 1) up to today for
    the given tickers, and safely upserts it — same ON CONFLICT DO NOTHING
    pattern as backfill_history(), just filling forward instead of backward.

    Call this before any live decision (screening, portfolio valuation,
    trailing-stop check) that depends on today's/recent price data, since
    price_history only updates when this is explicitly run — nothing pulls
    fresh data automatically otherwise.

    tickers=None refreshes the entire stocks universe. Pass a specific list
    (e.g. just your currently-held tickers) for a faster, narrower check —
    useful for the trailing-stop script, which only cares about held names.
    """
    if tickers is None:
        tickers = pd.read_sql("SELECT ticker FROM stocks", engine)["ticker"].tolist()
    if not tickers:
        print("No tickers to refresh.")
        return

    existing_max = pd.read_sql(
        "SELECT MAX(date) AS max_date FROM price_history WHERE ticker IN %(t)s",
        engine, params={"t": tuple(tickers)}
    )["max_date"].iloc[0]

    if existing_max is None:
        print("No existing price_history for these tickers — run write_to_db() first "
              "to establish a baseline before incremental refresh.")
        return

    existing_max = pd.to_datetime(existing_max)
    new_start = existing_max + pd.Timedelta(days=1)
    today = pd.Timestamp.today().normalize()

    if new_start > today:
        print(f"price_history already up to date (latest: {existing_max.date()}).")
        return

    print(f"Refreshing {len(tickers)} ticker(s): {new_start.date()} to {today.date()}")

    data = get_price_range(
        tickers,
        start=new_start.strftime("%Y-%m-%d"),
        end=(today + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),  # end is exclusive in yfinance
        interval="1d",
    )

    if data.empty:
        print("No new data returned (could just mean no trading days in this range yet).")
        return

    upsert_price_history(engine, data)
    print(f"Refresh complete. price_history now current as of ~{today.date()}.")


if __name__ == "__main__":
    test = pd.read_sql("SELECT * FROM stocks LIMIT 5", engine)
    print(test)
    count = pd.read_sql("SELECT COUNT(*) FROM stocks", engine)
    print(count)

    # Uncomment to run the backfill (pulls the years_back years BEFORE
    # whatever is currently the earliest date in price_history):
    # backfill_history(engine, years_back=5)