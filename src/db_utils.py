from sqlalchemy import create_engine
import pandas as pd

def get_engine():
    return create_engine("postgresql://postgres:1234@localhost:5432/momentum")

def load_price_dict_from_db(engine, as_of_date=None, lookback_days=400, ticker: list[str] = None):
    """Returns {ticker: DataFrame with Close column} — same shape your
    existing transform.py functions already expect.

    lookback_days now actually bounds the query (previously accepted but
    never used — every call loaded the ENTIRE history up to as_of_date,
    which gets expensive fast once price_history spans 10 years and this
    is called ~120 times in a decade-long monthly backtest). Set
    lookback_days=None to opt back into the old "load everything" behavior."""
    anchor_date = pd.to_datetime(as_of_date) if as_of_date else pd.Timestamp.today()
    query = "SELECT ticker, date, close FROM price_history WHERE 1=1"
    query += f" AND date <= '{anchor_date.date()}'"
    if lookback_days:
        lower_bound = (anchor_date - pd.Timedelta(days=lookback_days)).date()
        query += f" AND date >= '{lower_bound}'"
    if ticker:
        query += f" AND ticker IN ({', '.join(f'\'{t}\'' for t in ticker)})"
    df = pd.read_sql(query, engine)
    return {
        t: g.sort_values('date').set_index('date')[['close']].rename(columns={'close': 'Close'})
        for t, g in df.groupby('ticker')
    }

def load_universe_from_db(engine):
    """Returns the stocks table as a DataFrame — ticker, name, sector."""
    return pd.read_sql("SELECT * FROM stocks", engine)