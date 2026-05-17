"""
Fetches 1-minute OHLCV data for trade chart overlays using yfinance.
"""
import yfinance as yf
from datetime import datetime, timedelta


def get_candles(symbol: str, trade_date: datetime, entry_time: datetime, exit_time: datetime = None):
    """
    Returns 1-min OHLCV candles around the trade window, plus markers.
    Pads 30 minutes before entry and 30 minutes after exit.
    """
    start = (entry_time - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    end_dt = (exit_time or entry_time) + timedelta(minutes=30)
    end = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, interval="1m")

        if df.empty:
            return None

        candles = []
        for ts, row in df.iterrows():
            candles.append({
                "time": int(ts.timestamp()),
                "open":  round(float(row["Open"]),  4),
                "high":  round(float(row["High"]),  4),
                "low":   round(float(row["Low"]),   4),
                "close": round(float(row["Close"]), 4),
            })

        markers = [
            {
                "time":     int(entry_time.timestamp()),
                "position": "belowBar",
                "color":    "#22c55e",
                "shape":    "arrowUp",
                "text":     "Entry",
            }
        ]
        if exit_time:
            markers.append({
                "time":     int(exit_time.timestamp()),
                "position": "aboveBar",
                "color":    "#ef4444",
                "shape":    "arrowDown",
                "text":     "Exit",
            })

        return {"candles": candles, "markers": markers}

    except Exception as e:
        return {"error": str(e)}
