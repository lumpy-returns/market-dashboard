"""Quarterly pull of each equity ETF's top-10 holdings -> data/holdings.json.

Run by .github/workflows/holdings.yml (Jan/Apr/Jul/Oct) or manually. Holdings
move slowly, so this is deliberately separate from the daily price job.

Source: Yahoo Finance via yfinance (Ticker.funds_data.top_holdings). If a fund
fails to fetch, its previous entry is kept (and flagged in "failed") rather than
being wiped, so one bad run never blanks the dashboard.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

from fetch_data import EQUITIES

OUT = Path(__file__).parent / "data" / "holdings.json"
TOP_N = 10
RETRIES = 3


def equity_tickers():
    seen, out = set(), []
    for rows in EQUITIES.values():
        for ticker, _name in rows:
            if ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def parse_holdings(df):
    """DataFrame (index = symbol; 'Name' and 'Holding Percent' columns) -> list of dicts."""
    if df is None or len(df) == 0:
        return []
    cols = {c.lower(): c for c in df.columns}
    name_col = cols.get("name")
    pct_col = cols.get("holding percent") or cols.get("holdingpercent")
    out = []
    for symbol, row in df.head(TOP_N).iterrows():
        pct = row[pct_col] if pct_col else None
        out.append({
            "s": str(symbol),
            "n": str(row[name_col]) if name_col and row[name_col] == row[name_col] else "",
            "w": round(float(pct) * 100, 2) if pct is not None and pct == pct else None,
        })
    return out


def fetch_one(ticker):
    last_err = None
    for attempt in range(RETRIES):
        try:
            rows = parse_holdings(yf.Ticker(ticker).funds_data.top_holdings)
            if rows:
                return rows
            last_err = "no holdings returned"
        except Exception as exc:  # network, 401/429, parsing...
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(last_err)


def main():
    previous = {}
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text()).get("holdings", {})
        except Exception:
            pass

    tickers = equity_tickers()
    holdings, failed = {}, []
    for i, t in enumerate(tickers, 1):
        try:
            holdings[t] = fetch_one(t)
            print(f"[{i}/{len(tickers)}] {t}: {len(holdings[t])} holdings")
        except Exception as exc:
            failed.append({"ticker": t, "error": str(exc)[:200]})
            if t in previous:
                holdings[t] = previous[t]  # keep stale data rather than blank it
            print(f"[{i}/{len(tickers)}] {t}: FAILED ({exc})")
        time.sleep(0.5)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "holdings": holdings,
        "failed": failed,
    }, separators=(",", ":")))
    print(f"Wrote {len(holdings)} funds to {OUT} ({len(failed)} failed)")

    # Red workflow run if most of the pull failed (e.g. Yahoo blocking the runner).
    if len(failed) > len(tickers) // 2:
        sys.exit(1)


if __name__ == "__main__":
    main()
