# src/load/writers.py
from sqlalchemy import text

def write_screen_run(engine, run_date, top30_df, shortlist_dict):
    """
    top30_df: the DataFrame returned by compute_returns_and_dma() — the
    full top-30 pool BEFORE sector cap, already sorted by return_pct desc.
    shortlist_dict: the dict returned by get_top_companies() — the final
    post-sector-cap shortlist, used only to mark which tickers made it.

    Writes one row per top-30 ticker (shortlisted True/False), so rank-exit
    checks later have the full pool to look up, not just the final picks.
    """
    if top30_df.empty:
        print("write_screen_run: empty top-30 pool, nothing written.")
        return

    # sector isn't on top30_df (it's added later in transform_to_dict),
    # so pull it from shortlist_dict where available, else look it up fresh
    rows = []
    for rank, row in enumerate(top30_df.itertuples(index=False), start=1):
        sector = shortlist_dict.get(row.ticker, [None]*5)[-1]
        rows.append({
            "run_date": run_date,
            "ticker": row.ticker,
            "latest_price": row.latest_price,
            "return_pct": row.return_pct,
            "dma": row.dma,
            "above_dma": row.above_dma,
            "sector": sector,
            "momentum_rank": rank,
            "shortlisted": row.ticker in shortlist_dict,
        })

    query = text("""
        INSERT INTO screen_runs
            (run_date, ticker, latest_price, return_pct, dma, above_dma,
             sector, momentum_rank, shortlisted)
        VALUES
            (:run_date, :ticker, :latest_price, :return_pct, :dma, :above_dma,
             :sector, :momentum_rank, :shortlisted)
        ON CONFLICT (run_date, ticker) DO UPDATE SET
            latest_price = EXCLUDED.latest_price,
            return_pct   = EXCLUDED.return_pct,
            dma          = EXCLUDED.dma,
            above_dma    = EXCLUDED.above_dma,
            sector       = EXCLUDED.sector,
            momentum_rank = EXCLUDED.momentum_rank,
            shortlisted  = EXCLUDED.shortlisted;
    """)

    with engine.begin() as conn:
        conn.execute(query, rows)
    print(f"write_screen_run: {len(rows)} row(s) written for {run_date}.")