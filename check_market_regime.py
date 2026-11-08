"""
check_market_regime.py — the portfolio-level circuit breaker flagged in the
original build plan but never implemented: "if Nifty 500 falls below its
own 200 DMA -> reduce position count / pause new buys."

Pulls Nifty 500 (^CRSLDX) directly from yfinance — deliberately NOT from
your price_history table, since that only holds individual stock closes,
not the index itself. Standalone and read-only; touches no DB table.

This turns "wait for the downtrend to pass" from a feeling into a fact you
can check on demand: run this before every live_run.py, and only proceed
with new buys when it says IN REGIME. When it says OUT OF REGIME, that's
your actual, predefined trigger to keep waiting — and when it flips back,
that's your trigger to stop waiting. Re-run it periodically rather than
deciding by eye when "it feels like it's passed."

Run with:  python check_market_regime.py
"""
import yfinance as yf

NIFTY500_TICKER = "^CRSLDX"
DMA_WINDOW_DAYS = 200

if __name__ == "__main__":
    hist = yf.download(NIFTY500_TICKER, period="15mo", interval="1d", auto_adjust=True, progress=False)
    closes = hist["Close"].dropna()

    if len(closes) < DMA_WINDOW_DAYS:
        raise SystemExit(f"Only got {len(closes)} trading days of data — need at least {DMA_WINDOW_DAYS}.")

    latest_close = float(closes.iloc[-1])
    dma_200 = float(closes.iloc[-DMA_WINDOW_DAYS:].mean())
    latest_date = closes.index[-1].date()
    pct_vs_dma = (latest_close - dma_200) / dma_200 * 100

    print(f"\nNifty 500 as of {latest_date}:")
    print(f"  Latest close:  {latest_close:,.2f}")
    print(f"  200-DMA:       {dma_200:,.2f}")
    print(f"  Distance:      {pct_vs_dma:+.2f}%")

    if latest_close > dma_200:
        print("\n  -> IN REGIME (above 200-DMA). The circuit breaker rule does not flag a pause.")
    else:
        print("\n  -> OUT OF REGIME (below 200-DMA). This is the actual, predefined trigger")
        print("     to pause new buys / reduce position count — not a feeling, a fact.")
        print("     Re-run this periodically; resume when it flips back to IN REGIME,")
        print("     not when the market 'feels' better.")
