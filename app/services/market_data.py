"""
Fetches 1-minute OHLCV data for trade chart overlays using yfinance.
Generates one arrow marker per execution — BUY=green arrow up, SELL=red arrow down.
"""
import yfinance as yf
from datetime import datetime, timedelta


def get_candles(symbol: str, trade_date, executions: list):
    """
    Returns 1-min OHLCV candles covering the full session window,
    plus one marker per execution.

    executions: list of StgExecution ORM objects, sorted by time asc.
    """
    if not executions:
        return {"error": "No executions provided"}

    first_dt = datetime.combine(trade_date, executions[0].time)
    last_dt  = datetime.combine(trade_date, executions[-1].time)

    # Pad 30 min before first execution and 30 min after last
    fetch_start = (first_dt - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    fetch_end   = (last_dt  + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=fetch_start, end=fetch_end, interval="1m")

        if df.empty:
            return {"error": f"No 1-min data returned by yfinance for {symbol} on {trade_date}. "
                             f"Note: yfinance only provides 1-min data for the last 7 days."}

        candles = []
        for ts, row in df.iterrows():
            candles.append({
                "time":  int(ts.timestamp()),
                "open":  round(float(row["Open"]),  4),
                "high":  round(float(row["High"]),  4),
                "low":   round(float(row["Low"]),   4),
                "close": round(float(row["Close"]), 4),
            })

        # One marker per execution
        markers = []
        for ex in executions:
            exec_dt = datetime.combine(trade_date, ex.time)
            is_buy  = ex.side == "BUY"
            markers.append({
                "time":     int(exec_dt.timestamp()),
                "position": "belowBar" if is_buy else "aboveBar",
                "color":    "#22c55e"  if is_buy else "#ef4444",
                "shape":    "arrowUp"  if is_buy else "arrowDown",
                "text":     f"B {int(ex.quantity)}@${ex.price:.4f}" if is_buy
                            else f"S {int(ex.quantity)}@${ex.price:.4f}",
            })

        # Sort markers by time (required by TradingView Lightweight Charts)
        markers.sort(key=lambda m: m["time"])

        return {"candles": candles, "markers": markers}

    except Exception as e:
        return {"error": str(e)}