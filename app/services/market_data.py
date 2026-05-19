"""
Market data service — 1-minute OHLCV + indicators via Polygon.io.
Requires POLYGON_API_KEY in .env

Indicators computed server-side:
  - Volume (per bar)
  - EMA 20 (overlaid on candles)
  - MACD (12, 26, 9) — signal + histogram

Timezone:
  IBKR execution times are US/Eastern.
  Polygon returns UTC milliseconds.
  TradingView expects UTC unix seconds.
  We localize Eastern → UTC throughout.

Marker snapping:
  Markers are snapped to the nearest candle boundary (minute floor) that
  exists in the returned data, so a fill at 09:30:11 lands on the 09:30
  candle, not 09:31.
"""
import requests
from datetime import datetime, timedelta
from flask import current_app
import pytz

EASTERN      = pytz.timezone("US/Eastern")
UTC          = pytz.utc
POLYGON_BASE = "https://api.polygon.io/v2/aggs/ticker"


def _to_utc(trade_date, t):
    """Localize a time on trade_date (US/Eastern) → UTC-aware datetime."""
    naive = datetime.combine(trade_date, t)
    return EASTERN.localize(naive).astimezone(UTC)


def _ema(values, period):
    """Compute EMA for a list of floats. Returns list same length, None for warmup bars."""
    result = [None] * len(values)
    k = 2 / (period + 1)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            # Seed with SMA of first `period` values
            if i >= period - 1:
                seed = [x for x in values[max(0, i-period+1):i+1] if x is not None]
                if len(seed) == period:
                    prev = sum(seed) / period
                    result[i] = round(prev, 4)
        else:
            prev = v * k + prev * (1 - k)
            result[i] = round(prev, 4)
    return result


def _macd(closes, fast=12, slow=26, signal=9):
    """
    Returns (macd_line, signal_line, histogram) as lists of dicts
    {time, value} ready for TradingView, skipping None warmup bars.
    """
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_raw = [
        round(f - s, 4) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    signal_raw = _ema(macd_raw, signal)
    hist_raw = [
        round(m - s, 4) if m is not None and s is not None else None
        for m, s in zip(macd_raw, signal_raw)
    ]
    return macd_raw, signal_raw, hist_raw


def _snap_to_candle(exec_utc_ts: int, candle_times: set) -> int:
    """
    Snap an execution UTC timestamp (seconds) to the candle it belongs to.
    A fill at HH:MM:SS belongs to the HH:MM candle (floor to minute).
    If the exact minute floor isn't in the dataset, find the nearest earlier candle.
    """
    # Floor to minute boundary
    floored = (exec_utc_ts // 60) * 60

    if floored in candle_times:
        return floored

    # Walk backwards up to 5 minutes to find the nearest existing candle
    for offset in range(1, 6):
        candidate = floored - (offset * 60)
        if candidate in candle_times:
            return candidate

    # Fallback: return floored even if not in dataset (TradingView will still render it)
    return floored


def get_candles(symbol: str, trade_date, executions: list):
    """
    Fetch 1-min OHLCV from Polygon, compute indicators, snap markers.
    Returns {candles, markers, volume, ema20, macd_line, signal_line, histogram}
    or {"error": "..."}.
    """
    if not executions:
        return {"error": "No executions provided"}

    api_key = current_app.config.get("POLYGON_API_KEY", "")
    if not api_key:
        return {"error": "POLYGON_API_KEY not configured in .env"}

    first_utc = _to_utc(trade_date, executions[0].time)
    last_utc  = _to_utc(trade_date, executions[-1].time)

    window_start = first_utc - timedelta(minutes=45)   # extra padding for EMA warmup
    window_end   = last_utc  + timedelta(minutes=30)

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
                             "Check symbol and that the market was open."}

        # Build candles and volume
        candles = []
        volume  = []
        closes  = []
        times   = []

        for bar in results:
            t = bar["t"] // 1000   # ms → UTC seconds
            candles.append({
                "time":  t,
                "open":  round(float(bar["o"]), 4),
                "high":  round(float(bar["h"]), 4),
                "low":   round(float(bar["l"]), 4),
                "close": round(float(bar["c"]), 4),
            })
            volume.append({
                "time":  t,
                "value": int(bar.get("v", 0)),
                "color": "rgba(16,185,129,0.5)" if bar["c"] >= bar["o"] else "rgba(239,68,68,0.5)",
            })
            closes.append(round(float(bar["c"]), 4))
            times.append(t)

        candle_times = set(times)

        # Compute indicators
        ema20_raw               = _ema(closes, 20)
        macd_raw, sig_raw, hist_raw = _macd(closes)

        ema20 = [{"time": t, "value": v} for t, v in zip(times, ema20_raw) if v is not None]

        macd_line   = [{"time": t, "value": v} for t, v in zip(times, macd_raw) if v is not None]
        signal_line = [{"time": t, "value": v} for t, v in zip(times, sig_raw)  if v is not None]
        histogram   = [
            {"time": t, "value": v,
             "color": "rgba(16,185,129,0.7)" if v >= 0 else "rgba(239,68,68,0.7)"}
            for t, v in zip(times, hist_raw) if v is not None
        ]

        # Build markers — snapped to candle boundaries
        markers = []
        for ex in executions:
            exec_utc_ts = int(_to_utc(trade_date, ex.time).timestamp())
            snapped_ts  = _snap_to_candle(exec_utc_ts, candle_times)
            is_buy      = ex.side == "BUY"

            markers.append({
                "time":     snapped_ts,
                "position": "belowBar" if is_buy else "aboveBar",
                "color":    "#22c55e"  if is_buy else "#ef4444",
                "shape":    "arrowUp"  if is_buy else "arrowDown",
                "text":     f"{int(ex.quantity)}\n${ex.price:.4f}",
            })

        markers.sort(key=lambda m: m["time"])

        return {
            "candles":     candles,
            "volume":      volume,
            "ema20":       ema20,
            "macd_line":   macd_line,
            "signal_line": signal_line,
            "histogram":   histogram,
            "markers":     markers,
        }

    except requests.exceptions.Timeout:
        return {"error": "Polygon API request timed out. Try again."}
    except requests.exceptions.RequestException as e:
        return {"error": f"Polygon API request failed: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}