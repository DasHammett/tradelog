"""
Dashboard metrics — reads from DailySummary (pre-aggregated from ODS).
"""
from app.models import OdsDailySymbol, DailySummary, StgExecution
from app import db
from datetime import date, timedelta
from collections import defaultdict


# ---------------------------------------------------------------------------
# Performance breakdown helpers (read from ODS)
# ---------------------------------------------------------------------------

PRICE_BUCKETS = [
    ("< $2",        0,    2),
    ("$2 - $4.99",  2,    5),
    ("$5 - $9.99",  5,   10),
    ("$10 - $19.99",10,  20),
    ("$20 - $49.99",20,  50),
    ("$50 - $99.99",50, 100),
    ("$100 - $199", 100, 200),
    ("> $200",      200, float("inf")),
]

DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _bucket_pnl(ods_rows):
    buckets = {label: {"pnl": 0.0, "count": 0} for label, _, _ in PRICE_BUCKETS}
    total   = len(ods_rows)
    for r in ods_rows:
        price = r.avg_buy_price or 0
        for label, lo, hi in PRICE_BUCKETS:
            if lo <= price < hi:
                buckets[label]["pnl"]   += r.net_pnl or 0
                buckets[label]["count"] += 1
                break
    return [{"label": label, "pnl": round(buckets[label]["pnl"], 2),
             "pct": round(buckets[label]["count"] / total * 100, 1) if total else 0}
            for label, _, _ in PRICE_BUCKETS]


def _hour_pnl(ods_rows, start, end):
    """Aggregate STG executions by entry hour for the date range."""
    buckets = defaultdict(lambda: {"pnl": 0.0, "count": 0})

    # Get first execution per day+symbol to determine entry hour
    for r in ods_rows:
        first_exec = StgExecution.query\
            .filter_by(date=r.date, symbol=r.symbol, side="BUY")\
            .order_by(StgExecution.time.asc()).first()
        if first_exec:
            h = first_exec.time.hour
            buckets[h]["pnl"]   += r.net_pnl or 0
            buckets[h]["count"] += 1

    total = len(ods_rows)
    return [{"label": f"{h:02d}:00",
             "pnl":   round(buckets[h]["pnl"], 2),
             "pct":   round(buckets[h]["count"] / total * 100, 1) if total else 0}
            for h in sorted(buckets)]


def _dow_pnl(summaries):
    buckets = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    for s in summaries:
        dow_sun = (s.date.weekday() + 1) % 7   # Mon=0 → Sun=0 … Sat=6
        buckets[dow_sun]["pnl"]   += s.net_pnl or 0
        buckets[dow_sun]["count"] += 1
    total = len(summaries)
    return [{"label": DOW_LABELS[i],
             "pnl":   round(buckets[i]["pnl"], 2),
             "pct":   round(buckets[i]["count"] / total * 100, 1) if total else 0}
            for i in range(7)]


def _hold_time(ods_rows):
    """Avg hold time in minutes (last sell - first buy) per ODS row."""
    win_times  = []
    loss_times = []
    for r in ods_rows:
        first_buy = StgExecution.query\
            .filter_by(date=r.date, symbol=r.symbol, side="BUY")\
            .order_by(StgExecution.time.asc()).first()
        last_sell = StgExecution.query\
            .filter_by(date=r.date, symbol=r.symbol, side="SELL")\
            .order_by(StgExecution.time.desc()).first()
        if first_buy and last_sell:
            from datetime import datetime
            dt_buy  = datetime.combine(r.date, first_buy.time)
            dt_sell = datetime.combine(r.date, last_sell.time)
            mins    = (dt_sell - dt_buy).total_seconds() / 60
            if r.net_pnl > 0:
                win_times.append(mins)
            else:
                loss_times.append(mins)

    return {
        "winners": round(sum(win_times)  / len(win_times),  1) if win_times  else 0,
        "losers":  round(sum(loss_times) / len(loss_times), 1) if loss_times else 0,
    }


# ---------------------------------------------------------------------------
# Main dashboard metrics
# ---------------------------------------------------------------------------

def get_dashboard_metrics(days: int = 30):
    end   = date.today()
    start = end - timedelta(days=days)

    summaries = DailySummary.query.filter(
        DailySummary.date >= start,
        DailySummary.date <= end
    ).order_by(DailySummary.date).all()

    ods_rows = OdsDailySymbol.query.filter(
        OdsDailySymbol.date >= start,
        OdsDailySymbol.date <= end
    ).all()

    winners = [r for r in ods_rows if r.net_pnl > 0]
    losers  = [r for r in ods_rows if r.net_pnl < 0]

    total_net     = sum(r.net_pnl   or 0 for r in ods_rows)
    total_gross   = sum(r.gross_pnl or 0 for r in ods_rows)
    total_commish = sum(r.total_commission or 0 for r in ods_rows)
    gross_profit  = sum(r.net_pnl for r in winners) if winners else 0
    gross_loss    = abs(sum(r.net_pnl for r in losers)) if losers else 0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else None

    total = len(ods_rows)
    win_rate   = round(len(winners) / total * 100, 1) if total else 0
    avg_win    = round(gross_profit / len(winners), 2) if winners else 0
    avg_loss   = round(gross_loss   / len(losers),  2) if losers  else 0
    expectancy = round((win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss), 2) if total else 0

    # Equity curve & max drawdown from DailySummary
    equity_curve = []
    running = 0
    peak    = 0
    max_dd  = 0
    for s in summaries:
        running += s.net_pnl
        equity_curve.append({"date": s.date.isoformat(), "equity": round(running, 2)})
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    drawdown_curve = []
    running = 0
    peak    = 0
    for s in summaries:
        running += s.net_pnl
        if running > peak:
            peak = running
        drawdown_curve.append({"date": s.date.isoformat(), "drawdown": round(running - peak, 2)})

    avg_pnl_by_day = [{"date": s.date.isoformat(),
                        "avg_pnl": round(s.net_pnl / s.total_symbols, 2) if s.total_symbols else 0}
                       for s in summaries]

    win_rate_by_day = [{"date": s.date.isoformat(), "win_rate": s.win_rate}
                        for s in summaries]

    return {
        "net_pnl":          round(total_net, 2),
        "total_commissions":round(total_commish, 2),
        "total_fees":       0.0,
        "win_rate":         win_rate,
        "loss_rate":        round(100 - win_rate, 1),
        "profit_factor":    profit_factor,
        "avg_winner":       avg_win,
        "avg_loser":        avg_loss,
        "expectancy":       expectancy,
        "max_drawdown":     round(max_dd, 2),
        "total_trades":     total,
        "winning_trades":   len(winners),
        "losing_trades":    len(losers),
        "hold_time":        _hold_time(ods_rows),
        "equity_curve":     equity_curve,
        "avg_pnl_by_day":   avg_pnl_by_day,
        "win_rate_by_day":  win_rate_by_day,
        "drawdown_curve":   drawdown_curve,
        "pnl_by_day":       [{"date": s.date.isoformat(), "pnl": s.net_pnl} for s in summaries],
        "by_price":         _bucket_pnl(ods_rows),
        "by_hour":          _hour_pnl(ods_rows, start, end),
        "by_dow":           _dow_pnl(summaries),
    }
