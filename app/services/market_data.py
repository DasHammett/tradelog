"""
Market data service — 1-minute OHLCV candles via Polygon.io.
Requires POLYGON_API_KEY in .env

Free tier: 5 API calls/minute, up to 2 years of 1-min history.
Sign up at https://polygon.io (no credit card required).

Timezone handling:
  - IBKR execution times are US/Eastern (NYSE/NASDAQ market hours)
  - Polygon returns timestamps in UTC milliseconds
  - TradingView Lightweight Charts expects UTC unix seconds
  - We convert everything through UTC to avoid offset errors
"""
import requests
from datetime import datetime, timedelta
from flask import current_app
import pytz

EASTERN = pytz.timezone("US/Eastern")
UTC     = pytz.utc

POLYGON_BASE = "https://api.polygon.io/v2/aggs/ticker"


def _to_eastern_utc(trade_date, t):
    """
    Convert a time object on trade_date (assumed US/Eastern market time)
    to a UTC-aware datetime.
    """
    naive_dt = datetime.combine(trade_date, t)
    eastern_dt = EASTERN.localize(naive_dt)
    return eastern_dt.astimezone(UTC)


def get_candles(symbol: str, trade_date, executions: list):
    """
    Fetch 1-min OHLCV candles from Polygon.io for the session window.
    Returns {"candles": [...], "markers": [...]} or {"error": "..."}.

    executions: list of StgExecution ORM objects sorted by time asc.
    """
    if not executions:
        return {"error": "No executions provided"}

    api_key = current_app.config.get("POLYGON_API_KEY", "")
    if not api_key:
        return {"error": "POLYGON_API_KEY not configured in .env"}

    # Convert execution times to UTC
    first_utc = _to_eastern_utc(trade_date, executions[0].time)
    last_utc  = _to_eastern_utc(trade_date, executions[-1].time)

    # Pad 30 min either side
    window_start = first_utc - timedelta(minutes=30)
    window_end   = last_utc  + timedelta(minutes=30)

    # Polygon expects millisecond timestamps
    from_ms = int(window_start.timestamp() * 1000)
    to_ms   = int(window_end.timestamp()   * 1000)

    url = (
        f"{POLYGON_BASE}/{symbol.upper()}/range/1/minute"
        f"/{from_ms}/{to_ms}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={api_key}"
    )

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        body = resp.json()

        if body.get("status") == "ERROR":
            return {"error": f"Polygon error: {body.get('error', 'unknown')}"}

        results = body.get("results", [])
        if not results:
            return {"error": f"No 1-min data from Polygon for {symbol} on {trade_date}. "
                             f"Check that the symbol is correct and the market was open."}

        # Build candles — Polygon timestamps are UTC milliseconds
        candles = []
        for bar in results:
            candles.append({
                "time":  bar["t"] // 1000,          # ms → seconds (UTC)
                "open":  round(float(bar["o"]), 4),
                "high":  round(float(bar["h"]), 4),
                "low":   round(float(bar["l"]), 4),
                "close": round(float(bar["c"]), 4),
            })

        # One marker per execution — timestamps must be UTC seconds
        markers = []
        for ex in executions:
            exec_utc = _to_eastern_utc(trade_date, ex.time)
            is_buy   = ex.side == "BUY"
            markers.append({
                "time":     int(exec_utc.timestamp()),
                "position": "belowBar" if is_buy else "aboveBar",
                "color":    "#22c55e"  if is_buy else "#ef4444",
                "shape":    "arrowUp"  if is_buy else "arrowDown",
                "text":     f"B {int(ex.quantity)}@${ex.price:.4f}" if is_buy
                            else f"S {int(ex.quantity)}@${ex.price:.4f}",
            })

        # TradingView requires markers sorted by time
        markers.sort(key=lambda m: m["time"])

        return {"candles": candles, "markers": markers}

    except requests.exceptions.Timeout:
        return {"error": "Polygon API request timed out. Try again."}
    except requests.exceptions.RequestException as e:
        return {"error": f"Polygon API request failed: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}
