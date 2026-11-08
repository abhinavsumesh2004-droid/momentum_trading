# src/portfolio/delta.py
import pandas as pd
from sqlalchemy import text


def record_entry(engine, ticker: str, qty: float, entry_price: float, entry_date=None):
    """Logs a new BUY as a fresh OPEN lot in portfolio_log. Call this right
    after you actually execute a trade from live_run.py's delta table —
    nothing else in this codebase does this, so skipping it means the
    system has no memory of what you actually bought.

    FIFO lot model (per get_current_holdings' docstring): adding to an
    existing position is a NEW row, not an update to the old one — so a
    ticker can have several OPEN rows if you bought it across multiple
    quarters at different prices. running_peak_price starts at entry_price
    (the correct starting point — update_running_peak() will raise it from
    there as real closes come in; it should never START below entry).

    entry_date defaults to today. Pass entry_price as whatever you actually
    paid, not necessarily the day's close, if you want honest P&L later.
    """
    entry_date = entry_date or pd.Timestamp.today().date()
    query = text('''
        INSERT INTO portfolio_log (ticker, entry_date, entry_price, qty, running_peak_price, status)
        VALUES (:ticker, :entry_date, :entry_price, :qty, :entry_price, 'OPEN')
    ''')
    with engine.begin() as conn:
        conn.execute(query, {
            "ticker": ticker, "entry_date": entry_date,
            "entry_price": entry_price, "qty": qty,
        })
    print(f"Logged: BUY {qty} {ticker} @ {entry_price} on {entry_date}")


def record_exit(engine, ticker: str, exit_reason: str = "manual", exit_price: float = None, exit_date=None):
    """Closes ALL open lots for a ticker — for a full exit flagged by
    compute_rebalance_delta as 'NOT in new shortlist' (rank/sector/DMA
    drop-out) that you've reviewed and decided to act on. (Trailing-stop
    exits are handled separately by check_trail_exit(), which already does
    its own confirm-then-update — use that instead for trailing-stop.)

    exit_price defaults to the latest available close if not given.
    Does NOT support partial trims (reducing qty on one lot while keeping
    the rest) — if compute_rebalance_delta shows a partial 'Trim position'
    rather than a full exit, handle that lot-by-lot yourself for now.
    """
    exit_date = exit_date or pd.Timestamp.today().date()
    if exit_price is None:
        price_row = pd.read_sql(
            "SELECT close FROM price_history WHERE ticker = %(t)s ORDER BY date DESC LIMIT 1",
            engine, params={"t": ticker},
        )
        if price_row.empty:
            raise ValueError(f"No price_history for {ticker} — pass exit_price explicitly.")
        exit_price = float(price_row["close"].iloc[0])

    query = text('''
        UPDATE portfolio_log
        SET exit_date = :exit_date, exit_price = :exit_price,
            exit_reason = :exit_reason, status = 'CLOSED'
        WHERE ticker = :ticker AND status = 'OPEN'
    ''')
    with engine.begin() as conn:
        result = conn.execute(query, {
            "ticker": ticker, "exit_date": exit_date,
            "exit_price": exit_price, "exit_reason": exit_reason,
        })
    print(f"Logged: SELL {result.rowcount} lot(s) of {ticker} @ {exit_price} on {exit_date} ({exit_reason})")


def get_current_holdings(engine) -> dict:
    """Sums OPEN lots per ticker from portfolio_log (FIFO lot model —
    a ticker can have multiple OPEN rows if you added to it over time).
    Returns {ticker: total_qty_held}."""
    df = pd.read_sql(
        "SELECT ticker, qty FROM portfolio_log WHERE status = 'OPEN'",
        engine,
    )
    if df.empty:
        return {}
    return df.groupby("ticker")["qty"].sum().to_dict()


def compute_rebalance_delta(engine, target_allocation: dict) -> pd.DataFrame:
    """
    target_allocation: {ticker: target_units} from get_allocation_efficient().

    Returns a DataFrame with one row per ticker touched (union of currently
    held + newly targeted), showing current units, target units, and the
    delta to trade. Tickers currently held but NOT in target_allocation are
    included with target_units=0 and flagged — NOT auto-sold; that's a
    manual rank-exit/sector-drop-out call, per your decision to cross-check
    the full list yourself rather than have this script force a sell.
    """
    current = get_current_holdings(engine)
    all_tickers = set(current) | set(target_allocation)

    rows = []
    for t in sorted(all_tickers):
        held = current.get(t, 0)
        target = target_allocation.get(t, 0)
        delta = target - held

        if t not in target_allocation:
            note = "NOT in new shortlist — review manually (rank/sector drop-out)"
        elif t not in current:
            note = "NEW position"
        elif delta == 0:
            note = "No change"
        elif delta > 0:
            note = "Add to position"
        else:
            note = "Trim position"

        rows.append({
            "ticker": t,
            "current_units": held,
            "target_units": target,
            "delta": delta,
            "note": note,
        })

    return pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)