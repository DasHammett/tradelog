"""
Market data service — 1-minute OHLCV + indicators via Polygon.io.
Requires POLYGON_API_KEY in .env

Indicators computed server-side:
  - Volume (per bar)
  - EMA 9 and EMA 20 (overlaid on candles)
  - Session VWAP, anchored at 04:00 US/Eastern (premarket open)
  - MACD (12, 26, 9) — signal + histogram

Timezone:
  IBKR execution times are US/Eastern.
  Polygon returns UTC milliseconds.
  TradingView expects UTC unix seconds.
  We localize Eastern → UTC throughout.

Fill snapping:
  Fills are snapped to the nearest candle boundary (minute floor) that
  exists in the returned data, so a fill at 09:30:11 lands on the 09:30
  candle, not 09:31. Each fill is returned as a one-candle-wide segment
  {start, end, price, side}; the page draws it as a short horizontal line at
  the actual fill price.
"""
import requests
from datetime import datetime, time as dtime, timedelta
from flask import current_app
import pytz

VWAP_ANCHOR  = dtime(4, 0)   # premarket open, US/Eastern

EASTERN      = pytz.timezone("US/Eastern")
UTC          = pytz.utc
POLYGON_BASE = "https://api.massive.com/v2/aggs/ticker"
"""
POLYGON_BASE = "https://api.polygon.io/v2/aggs/ticker"
"""


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


def get_candles(symbol: str, trade_date, executions: list, fills: list = None):
    """
    Fetch 1-min OHLCV from Polygon, compute indicators, snap fills.

    executions: raw executions — only used for the time window.
    fills:      what to draw (grouped fills); defaults to `executions`.
                Each needs .time, .price, .side.

    Returns {candles, fills, volume, ema9, ema20, vwap, macd_line, signal_line,
             histogram, view_from, view_to} or {"error": "..."}.
    """
    if not executions:
        return {"error": "No executions provided"}
    if fills is None:
        fills = executions

    api_key = current_app.config.get("POLYGON_API_KEY", "")
    if not api_key:
        return {"error": "POLYGON_API_KEY not configured in .env"}

    first_utc = _to_utc(trade_date, min(e.time for e in executions))
    last_utc  = _to_utc(trade_date, max(e.time for e in executions))

    # Fetch from the 04:00 ET anchor so the session VWAP (and the EMAs) are
    # computed over the whole premarket + regular session, not just around fills.
    # Still a single Polygon call (a full 04:00-20:00 day is ~960 one-minute bars).
    anchor_utc   = _to_utc(trade_date, VWAP_ANCHOR)
    window_start = min(anchor_utc, first_utc - timedelta(minutes=5))
    window_end   = last_utc + timedelta(minutes=30)
    # The page opens zoomed on the trading activity; earlier bars stay scrollable.
    view_from    = int((first_utc - timedelta(minutes=45)).timestamp())
    view_to      = int(window_end.timestamp())

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

        # Build candles, volume and the running session VWAP
        candles = []
        volume  = []
        closes  = []
        times   = []
        vwap    = []
        cum_pv  = 0.0   # running sum of price * volume
        cum_vol = 0.0   # running sum of volume

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

            # Session VWAP. Polygon's per-bar "vw" is the VWAP of that single bar
            # (optional field); fall back to typical price if it is missing.
            bar_vol = float(bar.get("v", 0) or 0)
            bar_px  = bar.get("vw") or (float(bar["h"]) + float(bar["l"]) + float(bar["c"])) / 3
            cum_pv  += float(bar_px) * bar_vol
            cum_vol += bar_vol
            if cum_vol > 0:
                vwap.append({"time": t, "value": round(cum_pv / cum_vol, 4)})

        candle_times = set(times)

        # Compute indicators
        ema9_raw                = _ema(closes, 9)
        ema20_raw               = _ema(closes, 20)
        macd_raw, sig_raw, hist_raw = _macd(closes)

        ema9  = [{"time": t, "value": v} for t, v in zip(times, ema9_raw)  if v is not None]
        ema20 = [{"time": t, "value": v} for t, v in zip(times, ema20_raw) if v is not None]

        macd_line   = [{"time": t, "value": v} for t, v in zip(times, macd_raw) if v is not None]
        signal_line = [{"time": t, "value": v} for t, v in zip(times, sig_raw)  if v is not None]
        histogram   = [
            {"time": t, "value": v,
             "color": "rgba(16,185,129,0.7)" if v >= 0 else "rgba(239,68,68,0.7)"}
            for t, v in zip(times, hist_raw) if v is not None
        ]

        # Build fill segments, one per (grouped) fill, at the actual fill price.
        # Each spans exactly one candle interval using only candle times that
        # exist (this candle -> next candle, or previous -> this for the last
        # candle), so no extra time points are created on the chart.
        fill_segments = []
        for f in fills:
            exec_utc_ts = int(_to_utc(trade_date, f.time).timestamp())
            snapped_ts  = _snap_to_candle(exec_utc_ts, candle_times)
            if snapped_ts not in candle_times:
                continue
            i = times.index(snapped_ts)
            if i + 1 < len(times):
                start, end = times[i], times[i + 1]
            elif i > 0:
                start, end = times[i - 1], times[i]
            else:
                continue   # single candle — cannot draw a segment
            fill_segments.append({
                "start": start,
                "end":   end,
                "price": round(float(f.price), 4),
                "side":  f.side,
            })

        return {
            "candles":     candles,
            "volume":      volume,
            "ema9":        ema9,
            "ema20":       ema20,
            "vwap":        vwap,
            "macd_line":   macd_line,
            "signal_line": signal_line,
            "histogram":   histogram,
            "fills":       fill_segments,
            "view_from":   view_from,
            "view_to":     view_to,
        }

    except requests.exceptions.Timeout:
        return {"error": "Polygon API request timed out. Try again."}
    except requests.exceptions.RequestException as e:
        return {"error": f"Polygon API request failed: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}
