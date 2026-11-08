import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from src.db_utils import get_engine, load_price_dict_from_db
from src.backtest.weights import get_weights
from src.transform.returns_and_dma import get_top_companies


def generate_rebalance_dates(start_date: datetime, end_date: datetime, step_days: int = 30) -> list:
    """Monthly (approx, fixed step_days) rebalance dates from start_date to end_date.
    Exact trading-day snapping isn't needed here: get_top_companies()/price lookups
    use as_of_date='<= this date', so whatever the latest available trading day is
    gets picked up automatically — no need to walk day-by-day looking for an exact
    match like the old version of this function did."""
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=step_days)
    return dates


def get_allocation_efficient(ticker: dict, capital: float, weights: dict,
                             verbose: bool = False, max_overweight: float = 1.5):
    """Base allocation, then deploy leftover cash ONE share at a time to the
    affordable stock currently furthest BELOW its target value.
    max_overweight caps any position at (target value * max_overweight), so
    leftover can never pile into a single cheap stock. Cash that can't be
    placed without breaching the cap stays idle (reported honestly)."""
    allocation, leftover = get_allocation(ticker, capital, weights, verbose=verbose)
    prices = {t: ticker[t][0] for t in ticker}
    target_val = {t: capital * weights[t] for t in ticker}

    while True:
        candidates = []
        for t, p in prices.items():
            new_val = (allocation[t] + 1) * p
            if p <= leftover and new_val <= target_val[t] * max_overweight:
                deficit = target_val[t] - allocation[t] * p
                candidates.append((deficit, t))
        if not candidates:
            break
        _, best = max(candidates)
        allocation[best] += 1
        leftover -= prices[best]

    if verbose:
        print(f"    After capped sweep: {leftover:.2f} idle")
    return allocation, leftover


def get_allocation(ticker: dict, capital: float, weights: dict, verbose: bool = False):
    """Unchanged core logic: whole-unit share allocation, tracks leftover
    (un-investable) cash. verbose=True prints which tickers got 0 shares
    because their price exceeded their allocated slice — useful for
    diagnosing why leftover_cash is high (see backtest_engine's cash logging)."""
    allocation = {}
    tota_left = 0
    for t in ticker.keys():
        amount = capital * weights[t]
        units = int(amount // ticker[t][0])
        allocation[t] = units
        leftover = amount - (units * ticker[t][0])
        tota_left += leftover
        if verbose and units == 0:
            print(f"    SKIPPED: {t} price={ticker[t][0]:.2f} > slice={amount:.2f}, "
                  f"entire slice ({amount:.2f}) went to leftover cash")
    return allocation, tota_left


def get_portfolio_value(engine, holdings: dict, leftover_cash: float, as_of_date: datetime):
    """Marks a held portfolio to market as of a given date. Uses the latest
    available close on or before as_of_date for each held ticker (handles
    weekends/holidays/missing days for free, since load_price_dict_from_db
    already filters date <= as_of_date).

    Returns (total_value, {ticker: price_used}). If a held ticker has no
    price data at all as of as_of_date (e.g. delisted), it's valued at 0
    and flagged in the returned per-ticker price dict as None — this is a
    conservative (worst-case) treatment, not a resolution of survivorship
    bias; flagging so it's visible rather than silently absorbed.
    """
    if not holdings:
        return leftover_cash, {}

    price_histories = load_price_dict_from_db(engine, as_of_date=as_of_date, ticker=list(holdings.keys()))
    total_value = leftover_cash
    prices_used = {}
    for t, units in holdings.items():
        df = price_histories.get(t)
        if df is None or df.empty:
            prices_used[t] = None
            continue
        price = float(df["Close"].dropna().iloc[-1])
        prices_used[t] = price
        total_value += units * price
    return total_value, prices_used


def compute_performance_metrics(value_log: pd.DataFrame) -> dict:
    """value_log needs 'date' and 'portfolio_value' columns, one row per
    rebalance date. CAGR and drawdown are computed at rebalance-date
    granularity (monthly), not daily — a known limitation of this first
    pass; a true daily mark-to-market would need daily price joins, which
    isn't built yet (see build-plan doc's open items on this)."""
    if value_log.empty or len(value_log) < 2:
        return {"error": "Not enough data points to compute performance metrics."}

    initial_value = value_log["portfolio_value"].iloc[0]
    final_value = value_log["portfolio_value"].iloc[-1]
    total_days = (value_log["date"].iloc[-1] - value_log["date"].iloc[0]).days
    total_return_pct = (final_value / initial_value - 1) * 100

    cagr = None
    if total_days > 0:
        cagr = ((final_value / initial_value) ** (365.25 / total_days) - 1) * 100

    running_peak = value_log["portfolio_value"].cummax()
    drawdown = (value_log["portfolio_value"] - running_peak) / running_peak
    max_drawdown_pct = drawdown.min() * 100

    return {
        "initial_value": round(float(initial_value), 2),
        "final_value": round(float(final_value), 2),
        "total_return_pct": round(float(total_return_pct), 2),
        "cagr_pct": round(float(cagr), 2) if cagr is not None else None,
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "num_rebalances": len(value_log),
    }


def backtest_engine(capital: float,
                     start_date: str,
                     end_date: str,
                     data_engine,
                     return_window_months: int = 6,
                     dma_window_days: int = 200,
                     stock_count: int = 20,
                     max_per_sector: int = 3,
                     weight_type: str = "equal",
                     rebalance_step_days: int = 30,
                     verbose_allocation: bool = False,
                     reduce_idle_cash: bool = False,
                     ticker=[]):
    """Full monthly-rebalance backtest replay.

    At each rebalance date: re-screen the universe as-of that date (no
    look-ahead), weight the shortlist, allocate the current portfolio value
    across it (full liquidate-and-rebuild each period — matches the agreed
    baseline of no turnover filter / no transaction costs for now), then
    carry the resulting value forward to the next rebalance date.

    Defaults here match the AGREED baseline from the build-plan doc
    (6-month return, top-20, max 3/sector) rather than the drifted code
    defaults (12-month, top-10, max 2/sector) that had been used in ad hoc
    testing — pass different values explicitly to test that config instead.

    Returns (log_df, metrics_dict). log_df has one row per rebalance date:
    date, portfolio_value, holdings (dict), leftover_cash, shortlist (dict).
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    rebalance_dates = generate_rebalance_dates(start_dt, end_dt, step_days=rebalance_step_days)
    if not rebalance_dates:
        raise ValueError("No rebalance dates generated — check start_date/end_date.")

    records = []
    current_capital = capital
    holdings = {}
    leftover_cash = 0.0

    for i, rebal_date in enumerate(rebalance_dates):
        # 1. Mark existing holdings to market as of this rebalance date, to get
        #    the capital available for re-allocation (skip on the very first date).
        if i > 0:
            current_capital, prices_used = get_portfolio_value(data_engine, holdings, leftover_cash, rebal_date)
            missing = [t for t, p in prices_used.items() if p is None]
            if missing:
                print(f"[{rebal_date.date()}] Warning: no price data for {missing}, valued at 0.")

        # 2. Re-screen the universe as of this date (as_of_date prevents look-ahead).
        #    vol_adjusted weighting also needs each shortlisted stock's volatility_pct,
        #    which get_top_companies only computes/returns when asked for it.
        if weight_type == "vol_adjusted":
            shortlist, volatility = get_top_companies(
                ticker=ticker,
                return_window_months=return_window_months,
                dma_window_days=dma_window_days,
                data_engine=data_engine,
                as_of_date=rebal_date,
                stock_count=stock_count,
                max_per_sector=max_per_sector,
                return_volatility=True,
            )
        else:
            shortlist = get_top_companies(
                ticker=ticker,
                return_window_months=return_window_months,
                dma_window_days=dma_window_days,
                data_engine=data_engine,
                as_of_date=rebal_date,
                stock_count=stock_count,
                max_per_sector=max_per_sector,
            )
            volatility = None

        if not shortlist:
            # Nothing passed the screen this period — stay in cash.
            print(f"[{rebal_date.date()}] Warning: empty shortlist, holding cash.")
            holdings = {}
            leftover_cash = current_capital
            records.append({
                "date": rebal_date, "portfolio_value": current_capital,
                "holdings": {}, "leftover_cash": current_capital, "shortlist": {},
            })
            continue

        # 3. Weight and allocate the full current capital across the new shortlist.
        weights = get_weights(weight_type, shortlist, volatility=volatility)
        allocate_fn = get_allocation_efficient if reduce_idle_cash else get_allocation
        holdings, leftover_cash = allocate_fn(shortlist, current_capital, weights, verbose=verbose_allocation)
        print(f"[{rebal_date.date()}] Invested in {len(holdings)} stocks, "
              f"portfolio_value={current_capital:.0f}, leftover_cash={leftover_cash:.0f}")

        records.append({
            "date": rebal_date,
            "portfolio_value": current_capital,
            "holdings": dict(holdings),
            "leftover_cash": leftover_cash,
            "shortlist": shortlist,
        })

    # Final mark-to-market at the last rebalance date's holdings (already captured
    # as portfolio_value going INTO that date's reallocation above). To also see
    # the value of the final holdings at end_date itself (no further reallocation):
    final_value, _ = get_portfolio_value(data_engine, holdings, leftover_cash, end_dt)
    records.append({
        "date": end_dt, "portfolio_value": final_value,
        "holdings": dict(holdings), "leftover_cash": leftover_cash, "shortlist": {},
    })

    log_df = pd.DataFrame(records)
    cash_months = sum(1 for r in records if not r["holdings"])
    invested_months = len(records) - cash_months
    print(f"[{start_date} to {end_date}] {invested_months}/{len(records)} rebalance dates invested, "
          f"{cash_months} held cash (warm-up / no valid shortlist).")

    metrics = compute_performance_metrics(log_df)
    return log_df, metrics


def run_config_sweep(data_engine, capital: float, start_date: str, end_date: str, configs: list[dict],ticker=[]) -> pd.DataFrame:
    """Runs backtest_engine() once per config over the SAME [start_date, end_date]
    window. Each dict in `configs` should hold kwargs matching backtest_engine's
    tunable params (return_window_months, dma_window_days, stock_count,
    max_per_sector, weight_type, rebalance_step_days).

    Returns one row per config: config_id, its params, and its metrics.
    A config that errors (e.g. insufficient data for the window) gets NaN
    metrics rather than crashing the whole sweep."""
    rows = []
    for i, cfg in enumerate(configs):
        try:
            _, metrics = backtest_engine(capital=capital, start_date=start_date, end_date=end_date,
                                          data_engine=data_engine, **cfg,ticker=ticker)
            if "error" in metrics:
                print(f"Config {i} {cfg}: {metrics['error']}")
                rows.append({"config_id": i, **cfg, "cagr_pct": None, "total_return_pct": None,
                             "max_drawdown_pct": None, "final_value": None})
            else:
                rows.append({"config_id": i, **cfg, **metrics})
        except Exception as e:
            print(f"Config {i} {cfg} FAILED: {e}")
            rows.append({"config_id": i, **cfg, "cagr_pct": None, "total_return_pct": None,
                         "max_drawdown_pct": None, "final_value": None, "error": str(e)})
    return pd.DataFrame(rows)


def walk_forward_test(data_engine, capital: float, full_start: str, split_date: str, full_end: str,
                       configs: list[dict], rank_metric: str = "cagr_pct",ticker=[]):
    """The actual walk-forward split:
      1. Sweep every config on [full_start, split_date)  — IN-SAMPLE, used to pick a winner.
      2. Sweep every config on [split_date, full_end]      — OUT-OF-SAMPLE, held back until now.
      3. Compare rankings. The config that wins in-sample is what you'd have
         "chosen" without seeing the future; its out-of-sample result is the
         honest read on whether that choice generalizes.

    All configs (not just the in-sample winner) get run out-of-sample too —
    this is what actually lets you SEE overfitting: if the in-sample #1
    config isn't the out-of-sample #1 too, that's the tell.

    Returns a single merged DataFrame with one row per config, both periods'
    metrics side by side, plus a printed summary of the winner comparison.
    """
    print(f"=== IN-SAMPLE SWEEP: {full_start} to {split_date} (config selection happens here) ===")
    in_sample = run_config_sweep(data_engine, capital, full_start, split_date, configs,ticker=ticker)
    print(in_sample[["config_id", rank_metric]])

    print(f"\n=== OUT-OF-SAMPLE SWEEP: {split_date} to {full_end} (held back until now) ===")
    out_sample = run_config_sweep(data_engine, capital, split_date, full_end, configs,ticker=ticker)
    print(out_sample[["config_id", rank_metric]])

    config_cols = list(configs[0].keys())
    merged = in_sample[["config_id", *config_cols, rank_metric, "max_drawdown_pct"]].merge(
        out_sample[["config_id", rank_metric, "max_drawdown_pct"]],
        on="config_id", suffixes=("_in_sample", "_out_of_sample")
    )
    merged["in_sample_rank"] = merged[f"{rank_metric}_in_sample"].rank(ascending=False, method="min")
    merged["out_of_sample_rank"] = merged[f"{rank_metric}_out_of_sample"].rank(ascending=False, method="min")

    winner = merged.loc[merged["in_sample_rank"] == 1].iloc[0]
    print(f"\n=== SUMMARY ===")
    print(f"In-sample winner: config #{int(winner['config_id'])} "
          f"({ {k: winner[k] for k in config_cols} })")
    print(f"  In-sample {rank_metric}:     {winner[f'{rank_metric}_in_sample']}")
    print(f"  Out-of-sample {rank_metric}: {winner[f'{rank_metric}_out_of_sample']}")
    print(f"  Out-of-sample rank (out of {len(configs)} configs): {int(winner['out_of_sample_rank'])}")
    if winner["out_of_sample_rank"] == 1:
        print("  -> Held up: still #1 out-of-sample. Reasonable evidence this isn't pure overfitting.")
    else:
        print("  -> Did NOT hold up: another config performed better out-of-sample. "
              "Treat the in-sample win as likely overfit to that specific window.")

    return merged


def run_rolling_windows(data_engine, capital: float, config: dict, overall_start: str, overall_end: str,
                         window_years: int = 1, step_months: int = 3) -> pd.DataFrame:
    """Runs the SAME config repeatedly over sliding windows (e.g. every 1-year
    slice, stepped every 3 months) across a longer span. Gives you a
    DISTRIBUTION of outcomes instead of one cherry-pickable window — much
    harder to dismiss than a single 1-year result, and still keeps
    survivorship bias smaller per-window than a single long backtest would.

    Returns one row per window: window_start, window_end, and that config's
    metrics for that window."""
    start_dt = datetime.strptime(overall_start, "%Y-%m-%d")
    end_dt = datetime.strptime(overall_end, "%Y-%m-%d")

    rows = []
    window_start = start_dt
    while True:
        window_end = window_start + pd.DateOffset(years=window_years)
        if window_end > end_dt:
            break
        _, metrics = backtest_engine(
            capital=capital,
            start_date=window_start.strftime("%Y-%m-%d"),
            end_date=window_end.strftime("%Y-%m-%d"),
            data_engine=data_engine,
            **config,
        )
        rows.append({
            "window_start": window_start.date(),
            "window_end": window_end.date(),
            **{k: v for k, v in metrics.items() if k != "error"},
        })
        window_start += pd.DateOffset(months=step_months)

    result = pd.DataFrame(rows)
    if not result.empty and "cagr_pct" in result.columns:
        valid = result["cagr_pct"].dropna()
        print(f"\n=== {len(result)} rolling {window_years}-year windows ===")
        print(result[["window_start", "window_end", "cagr_pct", "max_drawdown_pct"]])
        print(f"\nMedian CAGR: {valid.median():.2f}%  |  Min: {valid.min():.2f}%  |  Max: {valid.max():.2f}%  "
              f"|  Std dev: {valid.std():.2f}%  |  % of windows positive: {(valid > 0).mean()*100:.0f}%")
    return result


def run_rolling_windows_multi(data_engine, capital: float, configs: list[dict], overall_start: str, overall_end: str,
                              window_years: int = 1, step_months: int = 3) -> pd.DataFrame:
    """Runs rolling window tests across multiple configurations. 
    Returns a single DataFrame with results for all configs and prints a 
    comparison table of their distributions.
    """
    start_dt = datetime.strptime(overall_start, "%Y-%m-%d")
    end_dt = datetime.strptime(overall_end, "%Y-%m-%d")

    all_rows = []

    for config_id, config in enumerate(configs):
        print(f"Running rolling windows for Config {config_id}...")
        window_start = start_dt
        
        while True:
            window_end = window_start + pd.DateOffset(years=window_years)
            if window_end > end_dt:
                break
            
            try:
                _, metrics = backtest_engine(
                    capital=capital,
                    start_date=window_start.strftime("%Y-%m-%d"),
                    end_date=window_end.strftime("%Y-%m-%d"),
                    data_engine=data_engine,
                    **config,
                )
                
                # Drop errors to keep valid metrics, append the config parameters
                metrics_filtered = {k: v for k, v in metrics.items() if k != "error"}
                all_rows.append({
                    "config_id": config_id,
                    "window_start": window_start.date(),
                    "window_end": window_end.date(),
                    **config,  # unpacks the specific config settings into columns
                    **metrics_filtered
                })
            except Exception as e:
                print(f"  -> Config {config_id} failed for window {window_start.date()} to {window_end.date()}: {e}")

            window_start += pd.DateOffset(months=step_months)

    result_df = pd.DataFrame(all_rows)

    # Generate and print a comparative summary table
    if not result_df.empty and "cagr_pct" in result_df.columns:
        print(f"\n=== Rolling Windows Multi-Config Summary ({window_years}-year windows, {step_months}-month steps) ===")
        
        summary_stats = []
        for config_id in result_df["config_id"].unique():
            cfg_data = result_df[result_df["config_id"] == config_id]["cagr_pct"].dropna()
            if not cfg_data.empty:
                summary_stats.append({
                    "config_id": config_id,
                    "median_cagr_pct": cfg_data.median(),
                    "min_cagr_pct": cfg_data.min(),
                    "max_cagr_pct": cfg_data.max(),
                    "win_rate_pct": (cfg_data > 0).mean() * 100,
                    "window_count": len(cfg_data)
                })

        summary_df = pd.DataFrame(summary_stats).set_index("config_id")
        print(summary_df.round(2).to_string())

    return result_df



if __name__ == "__main__":
    data_engine = get_engine()

    # Example config grid — sweeping sector cap, rebalance frequency, and
    # weighting scheme. Add/remove entries to widen or narrow the sweep.
    # reduce_idle_cash=True on every config — WITHOUT this, backtest_engine()
    # silently uses plain get_allocation() (no leftover-cash sweep). This was
    # the exact fix that moved a config's CAGR from 25.14% to 30.35% at
    # ₹100k in earlier testing (v4 doc §2) — it needs to be set explicitly.
    configs = [
        dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
             weight_type="equal", rebalance_step_days=30, reduce_idle_cash=True),
        dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=2,
             weight_type="equal", rebalance_step_days=30, reduce_idle_cash=True),
        dict(return_window_months=6, dma_window_days=200, stock_count=15, max_per_sector=3,
             weight_type="rank", rebalance_step_days=30, reduce_idle_cash=True),
        dict(return_window_months=12, dma_window_days=200, stock_count=20, max_per_sector=3,
             weight_type="equal", rebalance_step_days=30, reduce_idle_cash=True),
        dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
             weight_type="equal", rebalance_step_days=90, reduce_idle_cash=True),
        # New: config 5 — same finalized screen (6mo/200DMA/top-20/cap-3/quarterly,
        # the config-4 winner from v4 doc Section 7) but with inverse-volatility
        # weighting instead of equal-weight. Isolates the weighting-scheme question
        # from the screen itself, so this is a clean apples-to-apples comparison
        # against config 4 specifically (not just "some rank-weighted config lost").
        dict(return_window_months=6, dma_window_days=200, stock_count=20, max_per_sector=3,
             weight_type="vol_adjusted", rebalance_step_days=90, reduce_idle_cash=True),
        # Configs 6-8: isolate stock_count=15 cleanly against the winning
        # quarterly baseline (the existing stock_count=15 entry above is
        # confounded with monthly rebalancing + rank-weighting, both
        # factors that already lost — so it doesn't tell you anything
        # about count on its own). These hold everything else at the
        # config-4 winner and vary only what's being asked about here.
        dict(return_window_months=6, dma_window_days=200, stock_count=15, max_per_sector=3,
             weight_type="equal", rebalance_step_days=90, reduce_idle_cash=True),          # config 6: count only
        dict(return_window_months=6, dma_window_days=200, stock_count=15, max_per_sector=3,
             weight_type="vol_adjusted", rebalance_step_days=90, reduce_idle_cash=True),   # config 7: count + vol_adjusted together (the actual combo being considered)
    ]

    # Split your 10-year window roughly in half: 5y in-sample (pick a config),
    # 5y out-of-sample (test it honestly). Adjust dates to match your actual
    # backfilled data range.
    results = walk_forward_test(
        data_engine=data_engine,
        capital=100000,
        full_start="2016-07-06",
        split_date="2021-07-06",
        full_end="2026-07-03",
        configs=configs,
        rank_metric="cagr_pct",
    )
    #print(results)

    # Rolling 1-year windows, stepped every 3 months, across your full 10y
    # history — gives a DISTRIBUTION of 1-year outcomes rather than one
    # cherry-pickable window. Uses whichever single config you want to
    # report on (e.g. the walk-forward winner).
    rolling_results = run_rolling_windows(
        data_engine=data_engine,
        capital=100000,
        config=dict(return_window_months=6, dma_window_days=200, stock_count=20,
                     max_per_sector=3, weight_type="equal", rebalance_step_days=30),
        overall_start="2017-08-01",  # skip the ~13-month warm-up so every window has full lookback
        overall_end="2026-07-03",
        window_years=1,
        step_months=3,
    )