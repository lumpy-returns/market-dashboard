"""
fetch_data.py
Pulls macro + equities data from Yahoo Finance (via yfinance, no API key needed)
and writes it to data/market_data.json.

Each instrument gets:
  - price, 1D/1W/1M/3M/1Y % change, % from 52W high, 5-day and 20-day sparklines
  - EMA-based trend signal (10 EMA vs 20 EMA, low/high vs 20 EMA) for the
    "Traffic Light" view

Run manually with:  python fetch_data.py
GitHub Actions runs this automatically on a schedule (.github/workflows/refresh.yml).
"""

import json
import os
import re
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Ticker universe, organized the same way the dashboard sections are laid out.
# Each entry is (yahoo_ticker, display_name).
# ---------------------------------------------------------------------------

MACRO = {
    # US index futures -- disabled for now, may reuse this section later.
    # "us_index_futures": [
    #     ("ES=F", "S&P 500 Futures"),
    #     ("NQ=F", "Nasdaq 100 Futures"),
    #     ("YM=F", "Dow Futures"),
    #     ("RTY=F", "Russell 2000 Futures"),
    # ],
    "global_indices": [
        ("^GSPC", "S&P 500"),
        ("^FTSE", "FTSE 100"),
        ("^GDAXI", "DAX"),
        ("^N225", "Nikkei 225"),
        ("^HSI", "Hang Seng"),
        ("^STOXX50E", "Euro Stoxx 50"),
    ],
    "yields": [
        ("^IRX", "13-Week T-Bill"),
        ("^FVX", "5-Year Treasury"),
        ("^TNX", "10-Year Treasury"),
        ("^TYX", "30-Year Treasury"),
    ],
    "energy": [
        ("CL=F", "WTI Crude"),
        ("BZ=F", "Brent Crude"),
        ("NG=F", "Natural Gas"),
    ],
    "vol_dollar": [
        ("^VIX", "VIX"),
        ("DX-Y.NYB", "US Dollar Index"),
    ],
    "metals": [
        ("GC=F", "Gold"),
        ("SI=F", "Silver"),
        ("HG=F", "Copper"),
        ("PL=F", "Platinum"),
    ],
    "crypto": [
        ("BTC-USD", "Bitcoin"),
        ("ETH-USD", "Ethereum"),
        ("SOL-USD", "Solana"),
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
        # Original set
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
        # Added -- everything below is new, appended in the order supplied,
        # skipping tickers that already appear above (SMH, SOXX, IBB, SKYY,
        # ICLN, FINX).
        ("GDX", "Gold Miners"),
        ("DRAM", "Memory Chips"),
        ("CIBR", "Cybersecurity"),
        ("BAI", "AI Innovation"),
        ("IGV", "Software"),
        ("PAVE", "U.S. Infrastructure Development"),
        ("ITA", "Aerospace & Defense"),
        ("GRID", "Smart-Grid Infrastructure"),
        ("AIQ", "Artificial Intelligence"),
        ("XBI", "Biotechnology"),
        ("AIRR", "U.S. Industrial Renaissance"),
        ("GDXJ", "Junior Gold Miners"),
        ("PPA", "Aerospace & Defense"),
        ("COPX", "Copper Miners"),
        ("SHLD", "Defense Technology"),
        ("KBWB", "Banks"),
        ("QTUM", "Quantum Computing"),
        ("XAR", "Aerospace & Defense"),
        ("URA", "Uranium & Nuclear"),
        ("FDN", "Internet Businesses"),
        ("SIL", "Silver Miners"),
        ("XME", "Metals & Mining"),
        ("IFRA", "U.S. Infrastructure"),
        ("XOP", "Oil & Gas Exploration & Production"),
        ("NLR", "Uranium & Nuclear"),
        ("KRE", "Regional Banks"),
        ("IHI", "Medical Devices"),
        ("BOTZ", "Robotics & AI"),
        ("SOXQ", "Semiconductors"),
        ("XSD", "Semiconductors"),
        ("FBT", "Biotechnology"),
        ("PSI", "Semiconductors"),
        ("ITB", "Home Construction"),
        ("ARKG", "Genomics"),
        ("ARKQ", "Autonomous Tech & Robotics"),
        ("DTCR", "Data Centers & Digital Infrastructure"),
        ("IYT", "Transportation"),
        ("BUG", "Cybersecurity"),
        ("OIH", "Oil Services"),
        ("URNM", "Uranium Miners"),
        ("CHAT", "Generative AI"),
        ("PHO", "Water Resources"),
        ("FIW", "Water"),
        ("IHE", "Pharmaceuticals"),
        ("XHB", "Homebuilders"),
        ("KBE", "Banks"),
        ("LIT", "Lithium & Battery Technology"),
        ("IHF", "Health Care Providers"),
        ("IAI", "Broker-Dealers & Exchanges"),
        ("FTXL", "Semiconductors"),
        ("IHAK", "Cybersecurity"),
        ("MOO", "Agribusiness"),
        ("PPH", "Pharmaceuticals"),
        ("FTXR", "Transportation"),
        ("TAN", "Solar"),
        ("ARKF", "Blockchain & Fintech"),
        ("ARKX", "Space & Defense Innovation"),
        ("IEO", "Oil & Gas Exploration & Production"),
        ("MISL", "Aerospace & Defense"),
        ("IAT", "Regional Banks"),
        ("PJP", "Pharmaceuticals"),
        ("XPH", "Pharmaceuticals"),
        ("KIE", "Insurance"),
        ("PNQI", "Internet Businesses"),
        ("KCE", "Capital Markets"),
        ("XSW", "Software & Services"),
        ("IAK", "Insurance"),
        ("PBE", "Biotechnology & Genomics"),
        ("XRT", "Retail"),
        ("CLOU", "Cloud Computing"),
        ("XES", "Oil & Gas Equipment & Services"),
        ("URNJ", "Junior Uranium Miners"),
        ("BKCH", "Blockchain Companies"),
        ("XTN", "Transportation"),
        ("KBWP", "Property & Casualty Insurance"),
        ("SMHX", "Fabless Semiconductors"),
        ("ESPO", "Video Games & Esports"),
        ("XHS", "Health Care Services"),
        ("RTH", "Retail"),
        ("XHE", "Health Care Equipment"),
        ("GNOM", "Genomics & Biotechnology"),
        ("HERO", "Video Games & Esports"),
        ("BETZ", "Sports Betting & Online Gaming"),
    ],
    "countries_developed": [
        ("EWJ", "Japan"),
        ("EWG", "Germany"),
        ("EWU", "United Kingdom"),
        ("EWC", "Canada"),
        ("EWA", "Australia"),
        ("EWS", "Singapore"),
        ("EWH", "Hong Kong"),
        ("EWD", "Sweden"),
        ("ENOR", "Norway"),
    ],
    # Classified per MSCI's market classification. South Korea and Taiwan
    # are Developed under some other providers (e.g. FTSE) but Emerging
    # under MSCI; Vietnam is technically MSCI Frontier Markets, bucketed
    # here under Emerging since this dashboard only has two categories.
    "countries_emerging": [
        ("MCHI", "China"),
        ("INDA", "India"),
        ("EWZ", "Brazil"),
        ("EWY", "South Korea"),
        ("EWT", "Taiwan"),
        ("EPOL", "Poland"),
        ("GREK", "Greece"),
        ("EIDO", "Indonesia"),
        ("EWM", "Malaysia"),
        ("KSA", "Saudi Arabia"),
        ("THD", "Thailand"),
        ("COLO", "Colombia"),
        ("EWW", "Mexico"),
        ("TUR", "Turkey"),
        ("VNM", "Vietnam"),
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


def safe_ticker_filename(ticker):
    """Filesystem- and URL-safe stand-in for a ticker, used to name its
    history file. Deliberately NOT percent-encoding (e.g. urllib.parse.quote,
    which turns "^GSPC" into a file literally named "%5EGSPC.json") -- a
    static file server decodes a requested URL's %5E back to a literal "^"
    and looks for "^GSPC.json" on disk, which doesn't exist, so every ^ or =
    ticker (all indices, yields, and futures) 404s. Replacing the unsafe
    characters outright sidesteps that mismatch entirely; the frontend uses
    the identical substitution when building the fetch URL.
    """
    return re.sub(r"[^A-Za-z0-9.\-]", "_", ticker)


def pct_change(new, old):
    if old in (None, 0) or new is None:
        return None
    return round((new - old) / old * 100, 2)


def offset_price(series, offset):
    """Price `offset` trading days ago. Falls back to the earliest available
    price if the series doesn't go back that far."""
    if series is None or series.empty:
        return None
    if len(series) > offset:
        return float(series.iloc[-(offset + 1)])
    return float(series.iloc[0])


def build_instrument(ticker, name, closes, highs, lows, opens=None, is_yield=False):
    """Compute display stats + trend signal for one ticker."""
    if closes is None or closes.empty:
        return None

    # Align close/high/low(/open) on the same dates before computing anything.
    # Dropping NaNs from each series independently can desync them if Yahoo
    # has a gap in only one of the three columns (common for less-liquid
    # futures) -- that desync was the cause of "today's high" silently
    # pulling a stale, out-of-date value, which made the Red trend condition
    # fire far too often.
    cols = {"close": closes, "high": highs, "low": lows}
    if opens is not None:
        cols["open"] = opens
    combined = pd.DataFrame(cols).dropna()
    if combined.empty:
        return None

    scale = 10.0 if is_yield else 1.0  # correct Yahoo's 10x yield quoting convention
    closes = combined["close"] / scale
    highs = combined["high"] / scale
    lows = combined["low"] / scale
    opens_s = combined["open"] / scale if "open" in combined.columns else None

    last_price = float(closes.iloc[-1])
    prev_close = offset_price(closes, 1)
    week_ago = offset_price(closes, 5)
    month_ago = offset_price(closes, 21)
    three_month_ago = offset_price(closes, 63)
    year_ago = offset_price(closes, 252)
    high_52w = float(closes.max())
    sparkline = [round(v, 2) for v in closes.iloc[-5:].tolist()]
    sparkline_20d = [round(v, 2) for v in closes.iloc[-20:].tolist()]

    # --- EMA-based trend signal ---
    ema10 = closes.ewm(span=10, adjust=False).mean().iloc[-1]
    ema20 = closes.ewm(span=20, adjust=False).mean().iloc[-1]
    today_low = float(lows.iloc[-1]) if len(lows) else last_price
    today_high = float(highs.iloc[-1]) if len(highs) else last_price

    cond_10_gt_20 = bool(ema10 > ema20)
    cond_low_gt_20 = bool(today_low > ema20)
    cond_high_lt_20 = bool(today_high < ema20)
    cond_low_gt_10 = bool(today_low > ema10)
    cond_high_lt_10 = bool(today_high < ema10)

    # Dot state for the "High/Low vs 10 EMA" column: green when today's low
    # is above the 10 EMA (bullish), red when the 10/20 EMAs are bearishly
    # aligned AND today's high is below the 10 EMA, grey otherwise. Low>10EMA
    # and High<10EMA can't both be true (that would need low > high), so
    # green/red are mutually exclusive.
    if cond_low_gt_10:
        state_vs_10ema = "green"
    elif (not cond_10_gt_20) and cond_high_lt_10:
        state_vs_10ema = "red"
    else:
        state_vs_10ema = "grey"

    if cond_10_gt_20 and cond_low_gt_20:
        trend = "green"
    elif (not cond_10_gt_20) and cond_high_lt_20:
        trend = "red"
    else:
        trend = "yellow"

    # --- Weekly trend signal (+ weekly OHLC history for the 12M chart view) ---
    # Same EMA10-vs-EMA20 logic, just resampled to weekly bars first. Reuses
    # the daily close/high/low(/open) we already have -- no extra Yahoo calls needed.
    weekly_cols = {"close": closes, "high": highs, "low": lows}
    weekly_agg = {"close": "last", "high": "max", "low": "min"}
    if opens_s is not None:
        weekly_cols["open"] = opens_s
        weekly_agg["open"] = "first"
    weekly = pd.DataFrame(weekly_cols).resample("W-FRI").agg(weekly_agg).dropna()
    weekly_trend = None
    if len(weekly) >= 21:  # need a warmed-up 20-week EMA
        w_ema10 = weekly["close"].ewm(span=10, adjust=False).mean().iloc[-1]
        w_ema20 = weekly["close"].ewm(span=20, adjust=False).mean().iloc[-1]
        w_low = float(weekly["low"].iloc[-1])
        w_high = float(weekly["high"].iloc[-1])
        w_cond_10_gt_20 = bool(w_ema10 > w_ema20)
        w_cond_low_gt_20 = bool(w_low > w_ema20)
        w_cond_high_lt_20 = bool(w_high < w_ema20)
        if w_cond_10_gt_20 and w_cond_low_gt_20:
            weekly_trend = "green"
        elif (not w_cond_10_gt_20) and w_cond_high_lt_20:
            weekly_trend = "red"
        else:
            weekly_trend = "yellow"

    # --- Daily OHLC + 20 EMA history, for the on-hover candlestick chart ---
    # EMA is computed over the full close series (already warmed up over 2y)
    # then trimmed to the displayed window, so the line is accurate from day 1
    # of the chart rather than restarting cold.
    history = None
    if opens_s is not None:
        dec = 4 if is_yield else 2
        ema20_full = closes.ewm(span=20, adjust=False).mean()
        hist_n = min(252, len(combined))  # ~1 trading year
        hist_dates = combined.index[-hist_n:]
        history = [
            {
                "t": d.strftime("%Y-%m-%d"),
                "o": round(float(opens_s.loc[d]), dec),
                "h": round(float(highs.loc[d]), dec),
                "l": round(float(lows.loc[d]), dec),
                "c": round(float(closes.loc[d]), dec),
                "ema20": round(float(ema20_full.loc[d]), dec),
            }
            for d in hist_dates
        ]

    # --- Weekly OHLC + 20 EMA history, for the 12M (weekly-bar) chart view ---
    weekly_history = None
    if opens_s is not None and len(weekly) >= 2:
        dec = 4 if is_yield else 2
        w_ema20_full = weekly["close"].ewm(span=20, adjust=False).mean()
        w_hist_n = min(104, len(weekly))  # ~2 years of weekly bars
        w_hist_dates = weekly.index[-w_hist_n:]
        weekly_history = [
            {
                "t": d.strftime("%Y-%m-%d"),
                "o": round(float(weekly["open"].loc[d]), dec),
                "h": round(float(weekly["high"].loc[d]), dec),
                "l": round(float(weekly["low"].loc[d]), dec),
                "c": round(float(weekly["close"].loc[d]), dec),
                "ema20": round(float(w_ema20_full.loc[d]), dec),
            }
            for d in w_hist_dates
        ]

    entry = {
        "ticker": ticker,
        "name": name,
        "price": round(last_price, 4 if is_yield else 2),
        "chg_1d_pct": pct_change(last_price, prev_close),
        "chg_1w_pct": pct_change(last_price, week_ago),
        "chg_1m_pct": pct_change(last_price, month_ago),
        "chg_3m_pct": pct_change(last_price, three_month_ago),
        "chg_1y_pct": pct_change(last_price, year_ago),
        "pct_from_52w_high": pct_change(last_price, high_52w),
        "sparkline": sparkline,
        "sparkline_20d": sparkline_20d,
        "trend": trend,
        "weekly_trend": weekly_trend,
        "cond_10_gt_20": cond_10_gt_20,
        "cond_low_gt_20": cond_low_gt_20,
        "cond_low_gt_10": cond_low_gt_10,
        "state_vs_10ema": state_vs_10ema,
    }

    if is_yield and prev_close is not None:
        entry["chg_1d_bps"] = round((last_price - prev_close) * 100, 1)

    if ticker in TOP_HOLDINGS:
        entry["top_holdings"] = TOP_HOLDINGS[ticker]

    # Popped off by build_section and written to its own file under
    # data/history/ rather than shipped inline -- 150+ tickers x ~252 days of
    # OHLC would otherwise multiply the size of market_data.json many times
    # over for a chart most visitors will never open.
    if history is not None or weekly_history is not None:
        entry["_history"] = {"daily": history, "weekly": weekly_history}

    return entry


def fetch_all(all_pairs):
    """Batch-download every ticker in one call, return {ticker: {close, high, low}}."""
    tickers = [t for t, _ in all_pairs]
    print(f"Downloading {len(tickers)} tickers...")
    # 2 years of history: gives enough runway for a real "1 year ago" comparison
    # and a properly warmed-up 20-day EMA.
    raw = yf.download(
        tickers=tickers,
        period="2y",
        group_by="ticker",
        progress=False,
        threads=True,
        auto_adjust=False,
    )

    series = {}
    for t in tickers:
        try:
            frame = raw if len(tickers) == 1 else raw[t]
            series[t] = {
                "close": frame["Close"],
                "high": frame["High"],
                "low": frame["Low"],
                "open": frame["Open"],
            }
        except (KeyError, TypeError):
            series[t] = {"close": None, "high": None, "low": None, "open": None}
    return series


def build_section(pairs, series_by_ticker, histories):
    """Build display rows for a section, and collect each ticker's daily OHLC
    history into `histories` (keyed by ticker) as a side effect."""
    rows = []
    for ticker, name in pairs:
        s = series_by_ticker.get(ticker, {})
        try:
            entry = build_instrument(
                ticker, name, s.get("close"), s.get("high"), s.get("low"),
                opens=s.get("open"), is_yield=(ticker in YIELD_TICKERS),
            )
        except Exception as exc:
            # One bad ticker should never take down the whole run -- log it
            # and move on, rather than crashing the script (which would leave
            # the site silently serving yesterday's stale JSON).
            print(f"FAIL {ticker:12s} {name}: {exc}")
            entry = None
        if entry:
            hist = entry.pop("_history", None)
            if hist:
                histories[ticker] = hist
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

    series_by_ticker = fetch_all(unique_pairs)

    histories = {}
    macro_data = {key: build_section(pairs, series_by_ticker, histories) for key, pairs in MACRO.items()}
    equities_data = {key: build_section(pairs, series_by_ticker, histories) for key, pairs in EQUITIES.items()}
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

    # Per-ticker OHLC history, one small file per instrument, fetched lazily
    # by the frontend only when someone hovers a ticker for the candlestick
    # chart -- keeps market_data.json itself small for every page load.
    history_dir = os.path.join(os.path.dirname(__file__), "data", "history")
    os.makedirs(history_dir, exist_ok=True)
    current_files = set()
    for ticker, hist in histories.items():
        fname = safe_ticker_filename(ticker) + ".json"
        current_files.add(fname)
        with open(os.path.join(history_dir, fname), "w") as f:
            json.dump(hist, f, separators=(",", ":"))
    # Remove history files for tickers no longer in our universe.
    for fname in os.listdir(history_dir):
        if fname not in current_files:
            os.remove(os.path.join(history_dir, fname))

    total_rows = sum(len(v) for v in macro_data.values()) + sum(len(v) for v in equities_data.values())
    print(f"\nWrote {total_rows} instrument rows to {output_path}")
    print(f"Wrote {len(histories)} per-ticker history files to {history_dir}")


if __name__ == "__main__":
    main()
