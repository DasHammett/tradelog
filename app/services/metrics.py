"""
Dashboard metrics
=================
Trade-level metrics (win rate, avg winner/loser, hold time, by-price, by-hour, by-dow)
  → sourced from rt_trades (FIFO matched round-trips)

Day-level metrics (equity curve, drawdown, daily P&L)
  → sourced from daily_summary (pre-aggregated from rt_trades)

Totals (net P&L, commissions)
  → sourced from daily_summary
"""
from app.models import RtTrade, DailySummary, OdsDailySymbol
from app import db
from datetime import date, timedelta, datetime
from collections import defaultdict


PRICE_BUCKETS = [
    ("< $2",         0,    2),
    ("$2 - $4.99",   2,    5),
    ("$5 - $9.99",   5,   10),
    ("$10 - $19.99", 10,  20),
    ("$20 - $49.99", 20,  50),
    ("$50 - $99.99", 50, 100),
    ("$100 - $199",  100, 200),
    ("> $200",       200, float("inf")),
]

DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _bucket_pnl(rt_rows):
    buckets = {label: {"pnl": 0.0, "count": 0} for label, _, _ in PRICE_BUCKETS}
    total   = len(rt_rows)
    for r in rt_rows:
        price = r.entry_price or 0
        for label, lo, hi in PRICE_BUCKETS:
            if lo <= price < hi:
                buckets[label]["pnl"]   += r.net_pnl or 0
                buckets[label]["count"] += 1
                break
    return [{"label": label,
             "pnl":   round(buckets[label]["pnl"], 2),
             "pct":   round(buckets[label]["count"] / total * 100, 1) if total else 0}
            for label, _, _ in PRICE_BUCKETS]


def _hour_pnl(rt_rows):
    buckets = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    total   = len(rt_rows)
    for r in rt_rows:
        if r.entry_time:
            h = r.entry_time.hour
            buckets[h]["pnl"]   += r.net_pnl or 0
            buckets[h]["count"] += 1
    return [{"label": f"{h:02d}:00",
             "pnl":   round(buckets[h]["pnl"], 2),
             "pct":   round(buckets[h]["count"] / total * 100, 1) if total else 0}
            for h in sorted(buckets)]


def _dow_pnl(rt_rows):
    buckets = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    total   = len(rt_rows)
    for r in rt_rows:
        dow_sun = (r.date.weekday() + 1) % 7   # Mon=0 → remap Sun=0..Sat=6
        buckets[dow_sun]["pnl"]   += r.net_pnl or 0
        buckets[dow_sun]["count"] += 1
    return [{"label": DOW_LABELS[i],
             "pnl":   round(buckets[i]["pnl"], 2),
             "pct":   round(buckets[i]["count"] / total * 100, 1) if total else 0}
            for i in range(7)]


def _hold_time(rt_rows):
    win_times  = []
    loss_times = []
    for r in rt_rows:
        if r.entry_time and r.exit_time and not r.is_open:
            mins = (r.exit_time - r.entry_time).total_seconds() / 60
            if r.net_pnl > 0:
                win_times.append(mins)
            else:
                loss_times.append(mins)
    return {
        "winners": round(sum(win_times)  / len(win_times),  1) if win_times  else 0,
        "losers":  round(sum(loss_times) / len(loss_times), 1) if loss_times else 0,
    }


def get_dashboard_metrics(days: int = 30):
    end   = date.today()
    start = end - timedelta(days=days)

    summaries = DailySummary.query.filter(
        DailySummary.date >= start,
        DailySummary.date <= end
    ).order_by(DailySummary.date).all()

    rt_rows = RtTrade.query.filter(
        RtTrade.date >= start,
        RtTrade.date <= end,
        RtTrade.is_open == False
    ).all()

    winners = [r for r in rt_rows if r.net_pnl > 0]
    losers  = [r for r in rt_rows if r.net_pnl <= 0]

    total         = len(rt_rows)
    total_net     = sum(s.net_pnl          for s in summaries)
    total_commish = sum(s.total_commission for s in summaries)
    gross_profit  = sum(r.net_pnl for r in winners)
    gross_loss    = abs(sum(r.net_pnl for r in losers))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else None

    win_rate   = round(len(winners) / total * 100, 1) if total else 0
    avg_win    = round(gross_profit / len(winners), 2) if winners else 0
    avg_loss   = round(gross_loss   / len(losers),  2) if losers  else 0
    expectancy = round((win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss), 2) if total else 0

    # Equity curve & max drawdown from DailySummary
    equity_curve   = []
    drawdown_curve = []
    running = 0
    peak    = 0
    max_dd  = 0
    for s in summaries:
        running += s.net_pnl
        equity_curve.append({"date": s.date.isoformat(), "equity": round(running, 2)})
        if running > peak:
            peak = running
        dd = peak - running
        drawdown_curve.append({"date": s.date.isoformat(), "drawdown": round(running - peak, 2)})
        if dd > max_dd:
            max_dd = dd

    avg_pnl_by_day  = [{"date": s.date.isoformat(),
                         "avg_pnl": round(s.net_pnl / s.total_trades, 2) if s.total_trades else 0}
                        for s in summaries]

    win_rate_by_day = [{"date": s.date.isoformat(), "win_rate": s.win_rate}
                        for s in summaries]

    return {
        # Totals
        "net_pnl":          round(total_net, 2),
        "total_commissions":round(total_commish, 2),
        # Trade-level metrics (from rt_trades)
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
        "hold_time":        _hold_time(rt_rows),
        # Charts
        "equity_curve":     equity_curve,
        "avg_pnl_by_day":   avg_pnl_by_day,
        "win_rate_by_day":  win_rate_by_day,
        "drawdown_curve":   drawdown_curve,
        "pnl_by_day":       [{"date": s.date.isoformat(), "pnl": s.net_pnl} for s in summaries],
        # Performance breakdowns (from rt_trades)
        "by_price":         _bucket_pnl(rt_rows),
        "by_hour":          _hour_pnl(rt_rows),
        "by_dow":           _dow_pnl(rt_rows),
    }
