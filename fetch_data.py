"""
fetch_data.py
Pulls EOD market data from Yahoo Finance (via yfinance, no API key needed)
and writes it to data/market_data.json for the dashboard to read.

Run manually with:  python fetch_data.py
GitHub Actions runs this automatically on a schedule (see .github/workflows/refresh.yml).
"""

import json
import os
from datetime import datetime, timezone

import yfinance as yf

# ---- Configure your tickers here -------------------------------------
# key = Yahoo Finance ticker, value = display name
TICKERS = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones",
    "^VIX": "VIX",
    "DX-Y.NYB": "US Dollar Index",
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "GC=F": "Gold",
    "SI=F": "Silver",
    "CL=F": "Crude Oil (WTI)",
    "^TNX": "US 10Y Treasury Yield",
    "SPY": "SPY ETF",
    "QQQ": "QQQ ETF",
    "IWM": "IWM ETF (Russell 2000)",
}

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "data", "market_data.json")


def pct_change(new, old):
    if old in (None, 0) or new is None:
        return None
    return round((new - old) / old * 100, 2)


def fetch_one(ticker):
    """Fetch 1-year history for a ticker and compute the stats we display."""
    hist = yf.Ticker(ticker).history(period="1y")

    if hist.empty:
        return None

    closes = hist["Close"]
    last_price = float(closes.iloc[-1])

    # 1-day change: last close vs previous close
    prev_close = float(closes.iloc[-2]) if len(closes) > 1 else None
    # 1-week change: last close vs close ~5 trading days ago
    week_ago = float(closes.iloc[-6]) if len(closes) > 6 else None
    # Year-to-date: last close vs first close of this calendar year
    this_year = closes[closes.index.year == datetime.now().year]
    ytd_start = float(this_year.iloc[0]) if not this_year.empty else None
    # 52-week high
    high_52w = float(closes.max())

    return {
        "price": round(last_price, 2),
        "change_1d_pct": pct_change(last_price, prev_close),
        "change_1w_pct": pct_change(last_price, week_ago),
        "change_ytd_pct": pct_change(last_price, ytd_start),
        "pct_from_52w_high": pct_change(last_price, high_52w),
        "52w_high": round(high_52w, 2),
    }


def main():
    results = {}
    for ticker, name in TICKERS.items():
        try:
            data = fetch_one(ticker)
            if data:
                results[ticker] = {"name": name, **data}
                print(f"OK   {ticker:12s} {name}")
            else:
                print(f"SKIP {ticker:12s} {name} (no data returned)")
        except Exception as exc:
            print(f"FAIL {ticker:12s} {name}: {exc}")

    output = {
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "tickers": results,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWrote {len(results)} tickers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
