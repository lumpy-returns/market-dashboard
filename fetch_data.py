"""
fetch_data.py
Pulls a full set of macro + equities data from Yahoo Finance (via yfinance,
no API key needed) and writes it to data/market_data.json.

Run manually with:  python fetch_data.py
GitHub Actions runs this automatically on a schedule (.github/workflows/refresh.yml).
"""

import json
import os
from datetime import datetime, timezone

import yfinance as yf

# ---------------------------------------------------------------------------
# Ticker universe, organized the same way the dashboard sections are laid out.
# Each entry is (yahoo_ticker, display_name).
# ---------------------------------------------------------------------------

MACRO = {
    "us_index_futures": [
        ("ES=F", "S&P 500 Futures"),
        ("NQ=F", "Nasdaq 100 Futures"),
        ("YM=F", "Dow Futures"),
        ("RTY=F", "Russell 2000 Futures"),
    ],
    "vol_dollar": [
        ("^VIX", "VIX"),
        ("DX-Y.NYB", "US Dollar Index"),
    ],
    "crypto": [
        ("BTC-USD", "Bitcoin"),
        ("ETH-USD", "Ethereum"),
        ("SOL-USD", "Solana"),
    ],
    "metals": [
        ("GC=F", "Gold"),
        ("SI=F", "Silver"),
        ("HG=F", "Copper"),
        ("PL=F", "Platinum"),
    ],
    "energy": [
        ("CL=F", "WTI Crude"),
        ("BZ=F", "Brent Crude"),
        ("NG=F", "Natural Gas"),
    ],
    "yields": [
        ("^IRX", "13-Week T-Bill"),
        ("^FVX", "5-Year Treasury"),
        ("^TNX", "10-Year Treasury"),
        ("^TYX", "30-Year Treasury"),
    ],
    "global_indices": [
        ("^GSPC", "S&P 500"),
        ("^FTSE", "FTSE 100"),
        ("^GDAXI", "DAX"),
        ("^N225", "Nikkei 225"),
        ("^HSI", "Hang Seng"),
        ("^STOXX50E", "Euro Stoxx 50"),
    ],
}

# Yahoo's CBOE-derived yield tickers are quoted at 10x the real yield
# (e.g. a 4.25% 10-year shows as 42.50 on ^TNX). Divide by 10 to correct.
YIELD_TICKERS = {t for t, _ in MACRO["yields"]}

EQUITIES = {
    "major_etfs": [
        ("SPY", "SPY (S&P 500)"),
        ("QQQ", "QQQ (Nasdaq 100)"),
        ("DIA", "DIA (Dow 30)"),
        ("IWM", "IWM (Russell 2000)"),
    ],
    "sp500_submarket": [
        ("IWF", "iShares Russell 1000 Growth"),
        ("IWD", "iShares Russell 1000 Value"),
        ("MTUM", "iShares MSCI Momentum"),
        ("USMV", "iShares Min Volatility"),
        ("SPHB", "Invesco High Beta"),
        ("SPLV", "Invesco Low Volatility"),
    ],
    "sectors": [
        ("XLK", "Technology"),
        ("XLF", "Financials"),
        ("XLV", "Health Care"),
        ("XLY", "Consumer Discretionary"),
        ("XLP", "Consumer Staples"),
        ("XLE", "Energy"),
        ("XLI", "Industrials"),
        ("XLB", "Materials"),
        ("XLRE", "Real Estate"),
        ("XLU", "Utilities"),
        ("XLC", "Communication Services"),
    ],
    "sectors_ew": [
        ("RYT", "Technology (EW)"),
        ("RYF", "Financials (EW)"),
        ("RYH", "Health Care (EW)"),
        ("RCD", "Consumer Discretionary (EW)"),
        ("RHS", "Consumer Staples (EW)"),
        ("RYE", "Energy (EW)"),
        ("RGI", "Industrials (EW)"),
        ("RTM", "Materials (EW)"),
        ("EWRE", "Real Estate (EW)"),
        ("RYU", "Utilities (EW)"),
        ("EWCO", "Communication Services (EW)"),
    ],
    "themes": [
        ("SMH", "Semiconductors"),
        ("SOXX", "Semiconductor Industry"),
        ("ARKK", "Disruptive Innovation"),
        ("ICLN", "Clean Energy"),
        ("HACK", "Cybersecurity"),
        ("FINX", "Fintech"),
        ("IBB", "Biotech"),
        ("JETS", "Airlines"),
        ("SKYY", "Cloud Computing"),
        ("ROBO", "Robotics & AI"),
    ],
    "countries": [
        ("EWJ", "Japan"),
        ("MCHI", "China"),
        ("INDA", "India"),
        ("EWZ", "Brazil"),
        ("EWG", "Germany"),
        ("EWU", "United Kingdom"),
        ("EWC", "Canada"),
        ("EWA", "Australia"),
        ("EWY", "South Korea"),
        ("EWT", "Taiwan"),
    ],
}

# Static top-holdings reference, shown as an expandable detail on rows.
# These drift slowly over time -- update occasionally, no need for daily accuracy.
TOP_HOLDINGS = {
    "XLK": ["AAPL", "MSFT", "NVDA"],
    "XLF": ["BRK.B", "JPM", "V"],
    "XLV": ["LLY", "UNH", "JNJ"],
    "XLY": ["AMZN", "TSLA", "HD"],
    "XLP": ["PG", "COST", "WMT"],
    "XLE": ["XOM", "CVX", "COP"],
    "XLI": ["GE", "CAT", "RTX"],
    "XLB": ["LIN", "SHW", "FCX"],
    "XLRE": ["PLD", "AMT", "EQIX"],
    "XLU": ["NEE", "SO", "DUK"],
    "XLC": ["META", "GOOGL", "NFLX"],
    "SMH": ["NVDA", "TSM", "AVGO"],
    "SOXX": ["NVDA", "AVGO", "AMD"],
    "ARKK": ["TSLA", "ROKU", "COIN"],
    "ICLN": ["FSLR", "ENPH", "VWS.CO"],
    "HACK": ["PANW", "CRWD", "FTNT"],
    "FINX": ["SQ", "SOFI", "AFRM"],
    "IBB": ["AMGN", "GILD", "VRTX"],
    "JETS": ["DAL", "UAL", "LUV"],
    "SKYY": ["AMZN", "MSFT", "GOOGL"],
    "ROBO": ["ISRG", "KEYENCE", "FANUC"],
    "EWJ": ["TOYOTA", "SONY", "MITSUBISHI UFJ"],
    "MCHI": ["TENCENT", "ALIBABA", "PDD"],
    "INDA": ["RELIANCE", "HDFC BANK", "ICICI BANK"],
    "EWZ": ["PETROBRAS", "VALE", "ITAU UNIBANCO"],
    "EWG": ["SAP", "SIEMENS", "ALLIANZ"],
    "EWU": ["SHELL", "ASTRAZENECA", "HSBC"],
    "EWC": ["SHOPIFY", "RBC", "TD BANK"],
    "EWA": ["BHP", "CBA", "CSL"],
    "EWY": ["SAMSUNG", "SK HYNIX", "LG ENERGY"],
    "EWT": ["TSMC", "MEDIATEK", "HON HAI"],
}


def pct_change(new, old):
    if old in (None, 0) or new is None:
        return None
    return round((new - old) / old * 100, 2)


def build_instrument(ticker, name, closes, is_yield=False):
    """Compute display stats for one ticker from its closing-price series."""
    if closes is None or closes.empty:
        return None

    closes = closes.dropna()
    if closes.empty:
        return None

    if is_yield:
        closes = closes / 10.0  # correct Yahoo's 10x quoting convention

    last_price = float(closes.iloc[-1])
    prev_close = float(closes.iloc[-2]) if len(closes) > 1 else None
    week_ago = float(closes.iloc[-6]) if len(closes) > 6 else None
    this_year = closes[closes.index.year == datetime.now().year]
    ytd_start = float(this_year.iloc[0]) if not this_year.empty else None
    high_52w = float(closes.max())
    sparkline = [round(v, 2) for v in closes.iloc[-5:].tolist()]

    entry = {
        "ticker": ticker,
        "name": name,
        "price": round(last_price, 4 if is_yield else 2),
        "chg_1d_pct": pct_change(last_price, prev_close),
        "chg_1w_pct": pct_change(last_price, week_ago),
        "chg_ytd_pct": pct_change(last_price, ytd_start),
        "pct_from_52w_high": pct_change(last_price, high_52w),
        "sparkline": sparkline,
    }

    if is_yield and prev_close is not None:
        entry["chg_1d_bps"] = round((last_price - prev_close) * 100, 1)

    if ticker in TOP_HOLDINGS:
        entry["top_holdings"] = TOP_HOLDINGS[ticker]

    return entry


def fetch_all(all_pairs):
    """Batch-download every ticker in one call, return {ticker: close_series}."""
    tickers = [t for t, _ in all_pairs]
    print(f"Downloading {len(tickers)} tickers...")
    raw = yf.download(
        tickers=tickers,
        period="1y",
        group_by="ticker",
        progress=False,
        threads=True,
        auto_adjust=False,
    )

    closes = {}
    for t in tickers:
        try:
            if len(tickers) == 1:
                closes[t] = raw["Close"]
            else:
                closes[t] = raw[t]["Close"]
        except (KeyError, TypeError):
            closes[t] = None
    return closes


def build_section(pairs, closes_by_ticker):
    rows = []
    for ticker, name in pairs:
        entry = build_instrument(
            ticker, name, closes_by_ticker.get(ticker), is_yield=(ticker in YIELD_TICKERS)
        )
        if entry:
            rows.append(entry)
        else:
            print(f"SKIP {ticker:12s} {name} (no data)")
    return rows


def compute_breadth(equities_data):
    """Synthetic breadth/sentiment panel built from the equities section we already have.
    Not a true NYSE advance/decline line (that needs full constituent data) --
    this measures breadth across the tracked ETF/sector universe instead."""
    all_rows = []
    for section in equities_data.values():
        all_rows.extend(section)

    def pct_positive(field):
        vals = [r[field] for r in all_rows if r.get(field) is not None]
        if not vals:
            return None
        positive = sum(1 for v in vals if v > 0)
        return round(positive / len(vals) * 100, 1)

    return {
        "pct_positive_1d": pct_positive("chg_1d_pct"),
        "pct_positive_1w": pct_positive("chg_1w_pct"),
        "tracked_instruments": len(all_rows),
    }


def main():
    all_pairs = []
    for section in list(MACRO.values()) + list(EQUITIES.values()):
        all_pairs.extend(section)
    # de-duplicate while preserving order (some tickers could repeat across sections)
    seen = set()
    unique_pairs = []
    for pair in all_pairs:
        if pair[0] not in seen:
            seen.add(pair[0])
            unique_pairs.append(pair)

    closes_by_ticker = fetch_all(unique_pairs)

    macro_data = {key: build_section(pairs, closes_by_ticker) for key, pairs in MACRO.items()}
    equities_data = {key: build_section(pairs, closes_by_ticker) for key, pairs in EQUITIES.items()}
    breadth_data = compute_breadth(equities_data)

    # VIX-based sentiment read, if we have it
    vix_entry = next((r for r in macro_data.get("vol_dollar", []) if r["ticker"] == "^VIX"), None)
    if vix_entry:
        vix_level = vix_entry["price"]
        if vix_level < 15:
            sentiment = "Low volatility / risk-on"
        elif vix_level < 25:
            sentiment = "Normal / mixed"
        elif vix_level < 35:
            sentiment = "Elevated volatility / risk-off"
        else:
            sentiment = "Extreme volatility"
        breadth_data["vix_level"] = vix_level
        breadth_data["sentiment_label"] = sentiment

    output = {
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "macro": macro_data,
        "equities": equities_data,
        "breadth": breadth_data,
    }

    output_path = os.path.join(os.path.dirname(__file__), "data", "market_data.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    total_rows = sum(len(v) for v in macro_data.values()) + sum(len(v) for v in equities_data.values())
    print(f"\nWrote {total_rows} instrument rows to {output_path}")


if __name__ == "__main__":
    main()
