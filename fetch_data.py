"""
fetch_data.py
Pulls macro + equities data from Yahoo Finance (via yfinance, no API key needed)
and writes it to data/market_data.json.

Each instrument gets:
  - price, 1D/1W/1M/3M/1Y % change, % from 52W high, 20-day and 3-month sparklines
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
    # Foreign indices removed -- EWU/EWG/EWJ/EWH in the country section are
    # the dollar-priced, tradable versions of the same markets.
    "global_indices": [
        ("^GSPC", "S&P 500"),
    ],
    # 10Y + 30Y; a synthetic "10Y - 3M" curve-spread row is appended in
    # main() from ^TNX and ^IRX (13-week bill, downloaded as an aux ticker).
    "yields": [
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

# Yield tickers get yield-style formatting (4 decimals, 1D change in bps).
# Yahoo used to quote CBOE yield indices at 10x (42.50 = 4.25%) and this
# script divided by 10 to compensate; Yahoo now quotes the real yield
# (4.25), so that division made every yield display 10x too small.
YIELD_TICKERS = {t for t, _ in MACRO["yields"]} | {"10Y-3M"}

EQUITIES = {
    "major_etfs": [
        ("SPY", "S&P 500"),
        ("QQQ", "Nasdaq 100"),
        ("DIA", "Dow 30"),
        ("IWM", "Russell 2000"),
    ],
    "sp500_submarket": [
        ("IWF", "R1000 Growth"),
        ("IWD", "R1000 Value"),
        ("MTUM", "MSCI Momentum"),
        ("USMV", "Min Volatility"),
        ("SPHB", "High Beta"),
        ("SPLV", "Low Volatility"),
    ],
    "sectors": [
        ("XLK", "Technology"),
        ("XLF", "Financials"),
        ("XLV", "Health Care"),
        ("XLY", "Cons Discretionary"),
        ("XLP", "Consumer Staples"),
        ("XLE", "Energy"),
        ("XLI", "Industrials"),
        ("XLB", "Materials"),
        ("XLRE", "Real Estate"),
        ("XLU", "Utilities"),
        ("XLC", "Comm Services"),
    ],
    "themes": [
        # Original set
        ("SOXX", "Semiconductors"),
        ("ARKK", "Disruptive Innov"),
        ("ICLN", "Clean Energy"),
        ("FINX", "Fintech"),
        ("JETS", "Airlines"),
        ("SKYY", "Cloud Computing"),
        # Added -- everything below is new, appended in the order supplied,
        # skipping tickers that already appear above (SMH, SOXX, IBB, SKYY,
        # ICLN, FINX).
        ("GDX", "Gold Miners"),
        ("DRAM", "Memory Chips"),
        ("CIBR", "Cybersecurity"),
        ("BAI", "AI Innovation"),
        ("IGV", "Software"),
        ("PAVE", "US Infra Dev"),
        ("ITA", "Aero & Defense"),
        ("GRID", "Smart-Grid Infra"),
        ("XBI", "Biotechnology"),
        ("AIRR", "US Ind Renaissance"),
        ("GDXJ", "Jr Gold Miners"),
        ("COPX", "Copper Miners"),
        ("SHLD", "Defense Tech"),
        ("KBWB", "Banks"),
        ("QTUM", "Quantum Computing"),
        ("FDN", "Internet Cos"),
        ("SIL", "Silver Miners"),
        ("XME", "Metals & Mining"),
        ("IFRA", "US Infrastructure"),
        ("XOP", "Oil & Gas E&P"),
        ("KRE", "Regional Banks"),
        ("IHI", "Medical Devices"),
        ("BOTZ", "Robotics & AI"),
        ("ITB", "Home Construction"),
        ("ARKG", "Genomics"),
        ("DTCR", "Data Center Infra"),
        ("IYT", "Transportation"),
        ("OIH", "Oil Services"),
        ("URNM", "Uranium Miners"),
        ("PHO", "Water Resources"),
        ("IHE", "Pharmaceuticals"),
        ("LIT", "Lithium & Battery"),
        ("IHF", "HC Providers"),
        ("IAI", "Brokers/Exchanges"),
        ("MOO", "Agribusiness"),
        ("TAN", "Solar"),
        ("KIE", "Insurance"),
        ("XSW", "Software & Svcs"),
        ("XRT", "Retail"),
        ("URNJ", "Jr Uranium Miners"),
        ("BKCH", "Blockchain Cos"),
        ("KBWP", "P&C Insurance"),
        ("ESPO", "Games & Esports"),
        ("BETZ", "Sports Betting"),
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


# Equal-weight twin of each cap-weighted sector ETF. Not shown as rows any
# more -- downloaded only to compute each sector's "EW vs CW" column (is the
# typical stock in the sector keeping up with its mega-caps?). Invesco
# renamed this suite in June 2024 (RYT/RYF/RYH/RCD/RHS/RYE/RGI/RTM/EWRE/RYU/
# EWCO -> RSP*); see data/delisted_instruments.json.
SECTOR_EW_PAIRS = {
    "XLK": ("RSPT", "Technology (EW)"),
    "XLF": ("RSPF", "Financials (EW)"),
    "XLV": ("RSPH", "Health Care (EW)"),
    "XLY": ("RSPD", "Consumer Discretionary (EW)"),
    "XLP": ("RSPS", "Consumer Staples (EW)"),
    "XLE": ("RSPG", "Energy (EW)"),
    "XLI": ("RSPN", "Industrials (EW)"),
    "XLB": ("RSPM", "Materials (EW)"),
    "XLRE": ("RSPR", "Real Estate (EW)"),
    "XLU": ("RSPU", "Utilities (EW)"),
    "XLC": ("RSPC", "Communication Services (EW)"),
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


TREND_ORDER = {"red": 0, "yellow": 1, "green": 2}


def daily_trend_signal(closes, highs, lows):
    """Daily Traffic Light dot: green when 10 EMA > 20 EMA and today's low
    holds above the 20 EMA, red when 10 EMA < 20 EMA and today's high is
    below the 20 EMA, yellow otherwise. Pulled out into a helper so the same
    rule can be re-run on data ending yesterday (for "what changed today")."""
    if len(closes) < 21:
        return None
    ema10 = closes.ewm(span=10, adjust=False).mean().iloc[-1]
    ema20 = closes.ewm(span=20, adjust=False).mean().iloc[-1]
    low, high = float(lows.iloc[-1]), float(highs.iloc[-1])
    if ema10 > ema20 and low > ema20:
        return "green"
    if ema10 <= ema20 and high < ema20:
        return "red"
    return "yellow"


def weekly_frame(closes, highs, lows, opens=None):
    cols = {"close": closes, "high": highs, "low": lows}
    agg = {"close": "last", "high": "max", "low": "min"}
    if opens is not None:
        cols["open"] = opens
        agg["open"] = "first"
    return pd.DataFrame(cols).resample("W-FRI").agg(agg).dropna()


def weekly_trend_signal(weekly):
    """Same rule as the daily dot, on weekly bars (needs a warmed-up 20-week EMA)."""
    if weekly is None or len(weekly) < 21:
        return None
    return daily_trend_signal(weekly["close"], weekly["high"], weekly["low"])


def long_trend_signal(closes):
    """Long-term trend check (Minervini / Weinstein style), daily bars:
    green = price > 50-day SMA > 200-day SMA and the 200-day is rising
    (vs. ~1 month ago); red = price below a falling 200-day; else yellow."""
    if len(closes) < 222:
        return None
    sma50 = closes.rolling(50).mean()
    sma200 = closes.rolling(200).mean()
    c, s50, s200 = float(closes.iloc[-1]), float(sma50.iloc[-1]), float(sma200.iloc[-1])
    rising = s200 > float(sma200.iloc[-22])
    if c > s50 > s200 and rising:
        return "green"
    if c < s200 and not rising:
        return "red"
    return "yellow"


FLAT_ATR = 0.25   # |MA slope| below this many ATRs = "flat" (stalling)


def intermediate_trend_signal(closes, highs, lows):
    """Faster entry filter (daily bars) built to flag stalling early:
    green = close > 20 EMA > 50 SMA, 50 SMA rising over 10 bars and 20 EMA
            rising over 5 bars (each by > 0.25 ATR), a new 20-day closing high
            within the last 10 bars, and price above the 200-day (veto only);
    red   = close < 50 SMA and the 50 SMA falling (by > 0.25 ATR over 10 bars);
    else yellow (stacked but flat / no progress, or mixed)."""
    if len(closes) < 61:
        return None
    ema20 = closes.ewm(span=20, adjust=False).mean()
    sma50 = closes.rolling(50).mean()
    prev_c = closes.shift(1)
    tr = pd.concat([highs - lows, (highs - prev_c).abs(), (lows - prev_c).abs()],
                   axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    if atr <= 0:
        return None
    c, e20, s50 = float(closes.iloc[-1]), float(ema20.iloc[-1]), float(sma50.iloc[-1])
    slope50 = (s50 - float(sma50.iloc[-11])) / atr
    slope20 = (e20 - float(ema20.iloc[-6])) / atr
    last20 = closes.iloc[-20:].to_numpy()
    days_since_20d_high = len(last20) - 1 - int(last20.argmax())
    above_200 = True
    if len(closes) >= 200:
        above_200 = c > float(closes.iloc[-200:].mean())
    if (c > e20 > s50 and slope50 > FLAT_ATR and slope20 > FLAT_ATR
            and days_since_20d_high < 10 and above_200):
        return "green"
    if c < s50 and slope50 < -FLAT_ATR:
        return "red"
    return "yellow"


# IBD-style relative strength: most weight on the latest quarter.
RS_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))


def rs_raw_score(closes, offset=0):
    """Weighted 3/6/9/12-month return ending `offset` trading days ago.
    Turned into a 1-99 percentile rank across the equity universe in main();
    ranking makes it relative (to each other and to SPY, which is in the set)."""
    if len(closes) <= 252 + offset:
        return None
    end = float(closes.iloc[-1 - offset])
    score = 0.0
    for days, weight in RS_WEIGHTS:
        start = float(closes.iloc[-1 - offset - days])
        if start == 0:
            return None
        score += weight * (end / start - 1)
    return score


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

    scale = 1.0  # Yahoo now quotes yields directly (see YIELD_TICKERS note)
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
    six_month_ago = offset_price(closes, 126)
    # 52-week window only -- we download 2 years, so closes.max() over the
    # whole series was quietly a 2-year high.
    window_52w = closes.iloc[-252:]
    high_52w = float(window_52w.max())
    days_since_52w_high = int(len(window_52w) - 1 - int(window_52w.values.argmax()))
    sparkline_20d = [round(v, 2) for v in closes.iloc[-20:].tolist()]
    sparkline_3m = [round(v, 2) for v in closes.iloc[-63:].tolist()]
    sparkline_10d = [round(v, 2) for v in closes.iloc[-10:].tolist()]
    sparkline_6m = [round(v, 2) for v in closes.iloc[-126:].tolist()]
    # 12M: every other close (ending on the latest) -- plenty for a ~90px line
    sparkline_12m = [round(v, 2) for v in closes.iloc[-252:][::-1][::2][::-1].tolist()]

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

    trend = daily_trend_signal(closes, highs, lows) or "yellow"

    # --- Weekly trend signal (+ weekly OHLC history for the 12M chart view) ---
    # Same EMA10-vs-EMA20 logic, just resampled to weekly bars first. Reuses
    # the daily close/high/low(/open) we already have -- no extra Yahoo calls needed.
    weekly = weekly_frame(closes, highs, lows, opens_s)
    weekly_trend = weekly_trend_signal(weekly)

    # --- Long-term trend (price vs 50/200-day SMA, 200-day slope) ---
    long_trend = long_trend_signal(closes)
    inter_trend = intermediate_trend_signal(closes, highs, lows)

    # --- Same three signals as of the previous bar, for "what changed today" ---
    # Recomputed from history rather than diffed against the last saved JSON,
    # so re-running the job (or a manual trigger) never wipes out the feed.
    trend_prev = weekly_trend_prev = long_trend_prev = inter_trend_prev = None
    if len(closes) > 22:
        c1, h1, l1 = closes.iloc[:-1], highs.iloc[:-1], lows.iloc[:-1]
        trend_prev = daily_trend_signal(c1, h1, l1)
        weekly_trend_prev = weekly_trend_signal(weekly_frame(c1, h1, l1))
        long_trend_prev = long_trend_signal(c1)
        inter_trend_prev = intermediate_trend_signal(c1, h1, l1)

    # --- Extension from the 20 EMA in ATR units (Wilder ATR-14) ---
    prev_c = closes.shift(1)
    true_range = pd.concat(
        [highs - lows, (highs - prev_c).abs(), (lows - prev_c).abs()], axis=1
    ).max(axis=1)
    atr14 = float(true_range.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    ext_atr = round((last_price - float(ema20)) / atr14, 2) if atr14 > 0 else None
    atr_pct = round(atr14 / last_price * 100, 2) if last_price else None

    # --- Relative strength raw scores (ranked across the universe in main) ---
    rs_score = rs_raw_score(closes, 0)
    rs_score_1w = rs_raw_score(closes, 5)

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
        "days_since_52w_high": days_since_52w_high,
        "sparkline_20d": sparkline_20d,
        "sparkline_3m": sparkline_3m,
        "sparkline_10d": sparkline_10d,
        "sparkline_6m": sparkline_6m,
        "sparkline_12m": sparkline_12m,
        "trend": trend,
        "weekly_trend": weekly_trend,
        "long_trend": long_trend,
        "inter_trend": inter_trend,
        "trend_prev": trend_prev,
        "weekly_trend_prev": weekly_trend_prev,
        "long_trend_prev": long_trend_prev,
        "inter_trend_prev": inter_trend_prev,
        "ext_atr": ext_atr,
        "atr_pct": atr_pct,
        "rs_score": round(rs_score, 4) if rs_score is not None else None,
        "_rs_score_1w": rs_score_1w,
        # Academic momentum (Jegadeesh-Titman): skip the most recent month,
        # which tends to mean-revert, when measuring the 6/12-month trend.
        "chg_6m_pct": pct_change(last_price, six_month_ago),
        "mom_12_1_pct": pct_change(month_ago, year_ago) if len(closes) > 252 else None,
        "mom_6_1_pct": pct_change(month_ago, six_month_ago) if len(closes) > 126 else None,
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
    missing = []
    for t in tickers:
        try:
            frame = raw if len(tickers) == 1 else raw[t]
            if frame["Close"].dropna().empty:
                raise ValueError("empty frame")
            series[t] = {
                "close": frame["Close"],
                "high": frame["High"],
                "low": frame["Low"],
                "open": frame["Open"],
            }
        except (KeyError, TypeError, ValueError):
            missing.append(t)
            series[t] = {"close": None, "high": None, "low": None, "open": None}

    # The big threaded batch call occasionally drops a handful of otherwise-
    # valid tickers outright (a transient empty response on that ticker's
    # thread, Yahoo rate-limiting, etc.) -- observed happening to the exact
    # same tickers across separate runs, e.g. every "sectors_ew" ETF except
    # RCD. A lone, unthreaded retry per missing ticker uses a simpler
    # request path and reliably recovers them without touching the ones
    # that already succeeded.
    if missing:
        print(f"Batch download dropped {len(missing)} ticker(s), retrying individually: {', '.join(missing)}")
        for t in missing:
            try:
                frame = yf.download(t, period="2y", progress=False, threads=False, auto_adjust=False)
                if frame.empty or frame["Close"].dropna().empty:
                    print(f"  still empty: {t}")
                    continue
                series[t] = {
                    "close": frame["Close"],
                    "high": frame["High"],
                    "low": frame["Low"],
                    "open": frame["Open"],
                }
                print(f"  recovered on retry: {t}")
            except Exception as exc:
                print(f"  retry failed: {t}: {exc}")

    return series


def build_section(pairs, series_by_ticker, histories, section_key, failed):
    """Build display rows for a section, and collect each ticker's daily OHLC
    history into `histories` (keyed by ticker) as a side effect. Any ticker
    that comes back with no usable data is appended to `failed`, tagged with
    which section it belongs to -- this is what the monthly ticker-health
    check reads to find candidates for a rename or a delisting."""
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
            failed.append({"ticker": ticker, "name": name, "section": section_key})
    return rows


# Downloaded only to feed the regime gauge -- not shown as rows anywhere.
REGIME_AUX_TICKERS = [
    ("^VIX3M", "VIX 3-Month"),
    ("HYG", "High Yield Corporate Bond"),
    ("IEF", "7-10 Year Treasury"),
    ("RSP", "S&P 500 Equal Weight"),
    ("^IRX", "13-Week T-Bill"),  # short leg of the 10Y-3M spread row
]


def _close(series_by_ticker, ticker):
    s = series_by_ticker.get(ticker, {}).get("close")
    if s is None:
        return None
    s = s.dropna()
    return s if not s.empty else None


def _ratio_above_sma(a, b, window=50, offset=0):
    """Is the a/b ratio above its own `window`-day SMA (as of `offset` bars ago)?"""
    if a is None or b is None:
        return None, None
    ratio = (a / b).dropna()
    if len(ratio) < window + offset + 1:
        return None, None
    sma = ratio.rolling(window).mean()
    r, s = float(ratio.iloc[-1 - offset]), float(sma.iloc[-1 - offset])
    return r > s, (r / s - 1) * 100


def compute_regime(series_by_ticker, offset=0):
    """Risk-on / risk-off composite: six independent yes/no checks that each
    capture a different piece of "is this a market where momentum works?"."""
    spy = _close(series_by_ticker, "SPY")
    vix = _close(series_by_ticker, "^VIX")
    vix3m = _close(series_by_ticker, "^VIX3M")
    hyg, ief = _close(series_by_ticker, "HYG"), _close(series_by_ticker, "IEF")
    rsp = _close(series_by_ticker, "RSP")
    sphb, splv = _close(series_by_ticker, "SPHB"), _close(series_by_ticker, "SPLV")

    checks = []

    def add(key, label, passed, detail, why):
        checks.append({"key": key, "label": label, "pass": passed, "detail": detail, "why": why})

    for window in (50, 200):
        passed = detail = None
        if spy is not None and len(spy) > window + offset:
            px = float(spy.iloc[-1 - offset])
            sma = float(spy.rolling(window).mean().iloc[-1 - offset])
            passed = px > sma
            detail = f"{(px / sma - 1) * 100:+.1f}% vs {window}D"
        add(f"spy_{window}", f"SPY > {window}D", passed, detail,
            "Trend of the broad market" if window == 50 else "Primary bull/bear line")

    passed = detail = None
    if vix is not None and vix3m is not None:
        both = pd.concat([vix, vix3m], axis=1, join="inner").dropna()
        if len(both) > offset:
            v, v3 = float(both.iloc[-1 - offset, 0]), float(both.iloc[-1 - offset, 1])
            passed = v < v3
            detail = f"VIX {v:.1f} / VIX3M {v3:.1f}"
    add("vix_term", "VIX < VIX3M", passed, detail,
        "Contango = calm; inversion = near-term fear")

    for key, label, a, b, why in (
        ("credit", "HYG/IEF > 50D", hyg, ief, "Credit risk appetite"),
        ("breadth", "RSP/SPY > 50D", rsp, spy, "Equal-weight keeping up = broad participation"),
        ("beta", "SPHB/SPLV > 50D", sphb, splv, "High beta leading low vol = risk appetite"),
    ):
        passed, dev = _ratio_above_sma(a, b, 50, offset)
        add(key, label, passed, f"{dev:+.1f}% vs 50D" if dev is not None else None, why)

    available = [c for c in checks if c["pass"] is not None]
    score = sum(1 for c in available if c["pass"])
    if not available:
        label = None
    else:
        frac = score / len(available)
        label = "Risk-On" if frac >= 5 / 6 - 1e-9 else ("Neutral" if frac >= 0.5 else "Risk-Off")
    return {"score": score, "max": len(available), "label": label, "checks": checks}


def percentile_rank(values):
    """{key: score} -> {key: 1..99}, IBD-style (99 = strongest)."""
    items = [(k, v) for k, v in values.items() if v is not None]
    if not items:
        return {}
    if len(items) == 1:
        return {items[0][0]: 99}
    s = pd.Series(dict(items)).rank(method="average")
    n = len(items)
    return {k: int(round(1 + 98 * (r - 1) / (n - 1))) for k, r in s.items()}


def apply_ranks(equities_data):
    """RS rank (1-99) + its 1-week change, and the 12-1 momentum rank,
    across every equity row (one score per unique ticker)."""
    rows = [r for section in equities_data.values() for r in section]
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], r)
    rs_now = percentile_rank({t: r.get("rs_score") for t, r in by_ticker.items()})
    rs_1w = percentile_rank({t: r.get("_rs_score_1w") for t, r in by_ticker.items()})
    mom = percentile_rank({t: r.get("mom_12_1_pct") for t, r in by_ticker.items()})
    for r in rows:
        t = r["ticker"]
        r["rs_rank"] = rs_now.get(t)
        r["rs_rank_chg"] = (rs_now[t] - rs_1w[t]) if (t in rs_now and t in rs_1w) else None
        r["mom_12_1_rank"] = mom.get(t)


def compute_signal_changes(macro_data, equities_data):
    """Every Daily / Weekly / Intermediate / Long-term dot that flipped vs. the previous bar."""
    changes, seen = [], set()
    sections = [(f"macro.{k}", v) for k, v in macro_data.items()] + \
               [(f"equities.{k}", v) for k, v in equities_data.items()]
    for section_key, rows in sections:
        for r in rows:
            if r["ticker"] in seen:
                continue
            seen.add(r["ticker"])
            for signal, now_f, prev_f in (
                ("Weekly", "weekly_trend", "weekly_trend_prev"),
                ("Daily", "trend", "trend_prev"),
                ("Intermediate", "inter_trend", "inter_trend_prev"),
                ("Long-term", "long_trend", "long_trend_prev"),
            ):
                now, prev = r.get(now_f), r.get(prev_f)
                if now and prev and now != prev:
                    changes.append({
                        "ticker": r["ticker"], "name": r["name"], "section": section_key,
                        "signal": signal, "from": prev, "to": now,
                        "direction": "up" if TREND_ORDER[now] > TREND_ORDER[prev] else "down",
                        "rs_rank": r.get("rs_rank"),
                    })
    return changes


RRG_WINDOW = 10   # weeks
RRG_TAIL = 6      # weekly points per tail (oldest -> newest)


def compute_rrg(series_by_ticker, groups, benchmark="SPY"):
    """Relative Rotation Graph coordinates on weekly bars, vs. SPY.

    JdK's exact RS-Ratio / RS-Momentum formulas are proprietary; this is the
    common open approximation: RS = price / benchmark, RS-Ratio = 100 + the
    z-score of RS over a rolling window, RS-Momentum = 100 + the z-score of
    RS-Ratio over the same window. >100/>100 = Leading, >100/<100 =
    Weakening, <100/<100 = Lagging, <100/>100 = Improving.
    """
    bench = _close(series_by_ticker, benchmark)
    out = {}
    if bench is None:
        return out
    bench_w = bench.resample("W-FRI").last().dropna()
    for group_key, pairs in groups.items():
        pts_out = []
        for ticker, name in pairs:
            c = _close(series_by_ticker, ticker)
            if c is None:
                continue
            w = pd.concat([c.resample("W-FRI").last(), bench_w], axis=1, join="inner").dropna()
            if len(w) < RRG_WINDOW * 2 + RRG_TAIL:
                continue
            rs = 100 * w.iloc[:, 0] / w.iloc[:, 1]
            inf = [float("inf"), float("-inf")]
            ratio = (100 + (rs - rs.rolling(RRG_WINDOW).mean()) / rs.rolling(RRG_WINDOW).std()).replace(inf, float("nan"))
            mom = (100 + (ratio - ratio.rolling(RRG_WINDOW).mean()) / ratio.rolling(RRG_WINDOW).std()).replace(inf, float("nan"))
            both = pd.concat([ratio, mom], axis=1).dropna().iloc[-RRG_TAIL:]
            if both.empty:
                continue
            pts_out.append({
                "ticker": ticker,
                "name": name,
                "points": [[round(float(a), 2), round(float(b), 2)] for a, b in both.values],
            })
        out[group_key] = pts_out
    return out


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

    aux_pairs = REGIME_AUX_TICKERS + list(SECTOR_EW_PAIRS.values())
    aux_pairs = [p for p in aux_pairs if p[0] not in seen]
    download_pairs = unique_pairs + aux_pairs
    series_by_ticker = fetch_all(download_pairs)

    histories = {}
    failed_tickers = []
    macro_data = {
        key: build_section(pairs, series_by_ticker, histories, f"macro.{key}", failed_tickers)
        for key, pairs in MACRO.items()
    }
    equities_data = {
        key: build_section(pairs, series_by_ticker, histories, f"equities.{key}", failed_tickers)
        for key, pairs in EQUITIES.items()
    }

    # Aux tickers aren't rows, but if one stops returning data the regime
    # gauge / EW column silently degrades -- log it for the health check too.
    for t, n in aux_pairs:
        if _close(series_by_ticker, t) is None:
            print(f"SKIP {t:12s} {n} (aux, no data)")
            failed_tickers.append({"ticker": t, "name": n, "section": "aux"})

    # 10Y - 3M curve spread as its own yields row (synthetic series).
    tnx, irx = _close(series_by_ticker, "^TNX"), _close(series_by_ticker, "^IRX")
    if tnx is not None and irx is not None:
        spread = (tnx - irx).dropna()
        o = spread.shift(1).fillna(spread)
        try:
            row = build_instrument(
                "10Y-3M", "10Y − 3M Spread", spread,
                pd.concat([o, spread], axis=1).max(axis=1),
                pd.concat([o, spread], axis=1).min(axis=1),
                opens=o, is_yield=True,
            )
        except Exception as exc:
            print(f"FAIL 10Y-3M spread: {exc}")
            row = None
        if row:
            # % changes of a spread that can sit near zero are meaningless.
            for f in ("chg_1d_pct", "chg_1w_pct", "chg_1m_pct", "chg_3m_pct", "chg_1y_pct",
                      "pct_from_52w_high", "chg_6m_pct", "mom_12_1_pct", "mom_6_1_pct"):
                row[f] = None
            row["link"] = "https://fred.stlouisfed.org/series/T10Y3M"
            hist = row.pop("_history", None)
            if hist:
                histories["10Y-3M"] = hist
            macro_data["yields"].append(row)

    # EW vs CW: 3-month change in each sector's equal-weight / cap-weight ratio.
    for r in equities_data.get("sectors", []):
        pair = SECTOR_EW_PAIRS.get(r["ticker"])
        cw = _close(series_by_ticker, r["ticker"])
        ew = _close(series_by_ticker, pair[0]) if pair else None
        r["ew_ticker"] = pair[0] if pair else None
        r["ew_vs_cw_3m"] = None
        if cw is not None and ew is not None:
            ratio = (ew / cw).dropna()
            if len(ratio) > 63:
                r["ew_vs_cw_3m"] = round((float(ratio.iloc[-1]) / float(ratio.iloc[-64]) - 1) * 100, 2)

    apply_ranks(equities_data)
    for section in list(macro_data.values()) + list(equities_data.values()):
        for r in section:
            r.pop("_rs_score_1w", None)

    regime = compute_regime(series_by_ticker, 0)
    regime_prev = compute_regime(series_by_ticker, 1)
    regime["prev_score"] = regime_prev["score"]
    regime["prev_label"] = regime_prev["label"]

    signal_changes = compute_signal_changes(macro_data, equities_data)

    rrg = compute_rrg(series_by_ticker, {
        "sectors": EQUITIES["sectors"],
        "themes": EQUITIES["themes"],
        "countries": EQUITIES["countries_developed"] + EQUITIES["countries_emerging"],
    })

    spy_close = _close(series_by_ticker, "SPY")
    as_of = spy_close.index[-1].strftime("%Y-%m-%d") if spy_close is not None else None
    prev_as_of = spy_close.index[-2].strftime("%Y-%m-%d") if spy_close is not None and len(spy_close) > 1 else None


    output = {
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "macro": macro_data,
        "equities": equities_data,
        "regime": regime,
        "signal_changes": {"as_of": as_of, "prev_as_of": prev_as_of, "changes": signal_changes},
        "rrg": {"benchmark": "SPY", "period": "weekly", "window": RRG_WINDOW, "groups": rrg},
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

    # Tickers with no usable data this run -- read by the monthly ticker
    # health-check task, which researches whether each one was renamed
    # (and patches this file's ticker) or delisted (and moves it to
    # data/delisted_instruments.json instead). Written every run, even when
    # empty, so the health check always has a current, authoritative list
    # rather than a stale one from whenever a ticker last failed.
    failed_path = os.path.join(os.path.dirname(__file__), "data", "failed_tickers.json")
    with open(failed_path, "w") as f:
        json.dump(
            {"checked_utc": datetime.now(timezone.utc).isoformat(), "failed": failed_tickers},
            f,
            indent=2,
        )

    total_rows = sum(len(v) for v in macro_data.values()) + sum(len(v) for v in equities_data.values())
    print(f"\nWrote {total_rows} instrument rows to {output_path}")
    print(f"Wrote {len(histories)} per-ticker history files to {history_dir}")
    print(f"Wrote {len(failed_tickers)} failed ticker(s) to {failed_path}")


if __name__ == "__main__":
    main()
