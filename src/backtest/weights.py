def equal_weights(count:int,ticker:dict):
    weights = {}
    for t in ticker.keys():
        weights[t] = 1 / count
    return weights


def rank_weights(count: int, ticker: dict):
    """Weights decay by momentum rank (highest return_pct = rank 1 = highest weight).
    ticker values are [latest_price, return_pct, dma, above_dma, sector] —
    return_pct (index 1) is the ranking signal."""
    ranks = range(1, count + 1)
    weights = {}
    denom = sum(1 / r for r in ranks)

    # sort by return_pct descending (best momentum first)
    sorted_tickers = sorted(ticker.items(), key=lambda x: x[1][1], reverse=True)
    for (name, _data), r in zip(sorted_tickers, ranks):
        weights[name] = (1 / r) / denom
    return weights

def vol_adjusted_weights(ticker: dict, volatility: dict, min_volatility_pct: float = 0.5):
    """Inverse-volatility weighting: less-volatile names in the shortlist get
    MORE capital, more-volatile names get less. This is the sizing half of
    the "volatility-adjusted momentum score" concept from the build-plan doc
    (return / volatility of daily returns over the same window) — same
    methodology family NSE's own Nifty Midcap150 Momentum 50 uses. Note:
    the SCREEN still ranks candidates by raw return_pct, not by this score —
    this function only changes how capital is split across an already-picked
    shortlist, it does not change which stocks get picked. Testing the full
    vol-adjusted RANKING signal is a separate, not-yet-built change.

    volatility: {ticker: volatility_pct}, e.g. from
    get_top_companies(..., return_volatility=True). Expects the same keys as
    `ticker`; a ticker missing from `volatility` (shouldn't normally happen)
    or with volatility_pct=None falls back to min_volatility_pct.

    min_volatility_pct floors near-zero/missing volatility so a single
    barely-traded or data-sparse stock can't swallow almost all the capital
    via a division-by-near-zero.
    """
    inv_vol = {}
    for t in ticker.keys():
        v = volatility.get(t)
        if v is None or v < min_volatility_pct:
            v = min_volatility_pct
        inv_vol[t] = 1 / v
    total = sum(inv_vol.values())
    return {t: w / total for t, w in inv_vol.items()}


def get_weights(weight_type: str, ticker: dict, volatility: dict = None):
    count = len(ticker)
    if weight_type == 'equal':
        return equal_weights(count, ticker)
    elif weight_type == 'rank':
        return rank_weights(count, ticker)
    elif weight_type == 'vol_adjusted':
        if volatility is None:
            raise ValueError(
                "weight_type='vol_adjusted' requires a volatility dict — "
                "call get_top_companies(..., return_volatility=True) to get one, "
                "then pass it as get_weights('vol_adjusted', shortlist, volatility)."
            )
        return vol_adjusted_weights(ticker, volatility)
    else:
        raise ValueError(f"Invalid weight_type: {weight_type}. Must be 'equal', 'rank', or 'vol_adjusted'.")