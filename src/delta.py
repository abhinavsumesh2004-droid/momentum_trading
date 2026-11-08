# src/portfolio/delta.py
import pandas as pd

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