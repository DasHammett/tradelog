from app.models import Trade, DailySummary
from app import db
from datetime import date, timedelta
from collections import defaultdict


def compute_daily_summary(target_date: date) -> DailySummary:
    trades = Trade.query.filter(
        db.func.date(Trade.entry_time) == target_date,
        Trade.is_open == False
    ).all()

    winners = [t for t in trades if t.net_pnl and t.net_pnl > 0]
    losers  = [t for t in trades if t.net_pnl and t.net_pnl < 0]

    gross   = sum(t.gross_pnl or 0 for t in trades)
    net     = sum(t.net_pnl   or 0 for t in trades)
    commish = sum(t.commission or 0 for t in trades)

    summary = DailySummary.query.filter_by(date=target_date).first()
    if not summary:
        summary = DailySummary(date=target_date)
        db.session.add(summary)

    summary.total_trades     = len(trades)
    summary.winning_trades   = len(winners)
    summary.losing_trades    = len(losers)
    summary.gross_pnl        = round(gross, 2)
    summary.net_pnl          = round(net, 2)
    summary.total_commission = round(commish, 2)
    summary.win_rate         = round(len(winners) / len(trades) * 100, 1) if trades else 0
    summary.avg_winner       = round(sum(t.net_pnl for t in winners) / len(winners), 2) if winners else 0
    summary.avg_loser        = round(sum(t.net_pnl for t in losers)  / len(losers),  2) if losers  else 0

    db.session.commit()
    return summary


# Price range buckets (label, min, max)
PRICE_BUCKETS = [
    ("< $2.00",       0,     2),
    ("$2 - $4.99",    2,     5),
    ("$5 - $9.99",    5,    10),
    ("$10 - $19.99", 10,    20),
    ("$20 - $49.99", 20,    50),
    ("$50 - $99.99", 50,   100),
    ("$100 - $189.99",100,  190),
    ("$200 - $499.99",200,  500),
    ("> $500",        500, float("inf")),
]

DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _bucket_pnl(trades):
    """Group trades by price bucket. Returns list of dicts."""
    buckets = {label: {"pnl": 0.0, "count": 0} for label, _, _ in PRICE_BUCKETS}
    total_count = len(trades)
    for t in trades:
        price = t.entry_price or 0
        for label, lo, hi in PRICE_BUCKETS:
            if lo <= price < hi:
                buckets[label]["pnl"]   += t.net_pnl or 0
                buckets[label]["count"] += 1
                break
    result = []
    for label, _, _ in PRICE_BUCKETS:
        b = buckets[label]
        result.append({
            "label": label,
            "pnl":   round(b["pnl"], 2),
            "pct":   round(b["count"] / total_count * 100, 1) if total_count else 0,
        })
    return result


def _hour_pnl(trades):
    """Group trades by entry hour (0-23). Returns list of dicts."""
    buckets = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    total = len(trades)
    for t in trades:
        h = t.entry_time.hour
        buckets[h]["pnl"]   += t.net_pnl or 0
        buckets[h]["count"] += 1
    result = []
    # Only return hours that had any trades
    for h in sorted(buckets.keys()):
        b = buckets[h]
        result.append({
            "label": f"{h:02d}:00",
            "pnl":   round(b["pnl"], 2),
            "pct":   round(b["count"] / total * 100, 1) if total else 0,
        })
    return result


def _dow_pnl(trades):
    """Group trades by day of week. Returns list for Sun-Sat."""
    buckets = defaultdict(lambda: {"pnl": 0.0, "count": 0})
    total = len(trades)
    for t in trades:
        dow = t.entry_time.weekday()   # 0=Mon … 6=Sun in Python
        # Remap to Sun=0 … Sat=6
        dow_sun = (dow + 1) % 7
        buckets[dow_sun]["pnl"]   += t.net_pnl or 0
        buckets[dow_sun]["count"] += 1
    result = []
    for i, label in enumerate(DOW_LABELS):
        b = buckets[i]
        result.append({
            "label": label,
            "pnl":   round(b["pnl"], 2),
            "pct":   round(b["count"] / total * 100, 1) if total else 0,
        })
    return result


def _avg_pnl_by_day(summaries):
    """Average trade P&L per day (net_pnl / total_trades)."""
    result = []
    for s in summaries:
        avg = round(s.net_pnl / s.total_trades, 2) if s.total_trades else 0
        result.append({"date": s.date.isoformat(), "avg_pnl": avg})
    return result


def _win_rate_by_day(summaries):
    return [{"date": s.date.isoformat(), "win_rate": s.win_rate} for s in summaries]


def _hold_time(trades):
    """Avg hold time in minutes for winners vs losers."""
    winners = [t for t in trades if t.net_pnl and t.net_pnl > 0 and t.duration_minutes is not None]
    losers  = [t for t in trades if t.net_pnl and t.net_pnl <= 0 and t.duration_minutes is not None]
    avg_win  = round(sum(t.duration_minutes for t in winners) / len(winners), 1) if winners else 0
    avg_loss = round(sum(t.duration_minutes for t in losers)  / len(losers),  1) if losers  else 0
    return {"winners": avg_win, "losers": avg_loss}


def _drawdown_curve(summaries):
    """Cumulative drawdown series."""
    result = []
    peak = 0
    running = 0
    for s in summaries:
        running += s.net_pnl
        if running > peak:
            peak = running
        dd = running - peak   # always <= 0
        result.append({"date": s.date.isoformat(), "drawdown": round(dd, 2)})
    return result


def get_dashboard_metrics(days: int = 30):
    end   = date.today()
    start = end - timedelta(days=days)

    summaries = DailySummary.query.filter(
        DailySummary.date >= start,
        DailySummary.date <= end
    ).order_by(DailySummary.date).all()

    trades = Trade.query.filter(
        db.func.date(Trade.entry_time) >= start,
        Trade.is_open == False
    ).all()

    winners = [t for t in trades if t.net_pnl and t.net_pnl > 0]
    losers  = [t for t in trades if t.net_pnl and t.net_pnl <= 0]

    total_net    = sum(t.net_pnl or 0 for t in trades)
    total_commish = sum(t.commission or 0 for t in trades)
    gross_profit = sum(t.net_pnl for t in winners) if winners else 0
    gross_loss   = abs(sum(t.net_pnl for t in losers)) if losers else 0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else None

    win_rate  = round(len(winners) / len(trades) * 100, 1) if trades else 0
    avg_win   = round(gross_profit / len(winners), 2) if winners else 0
    avg_loss  = round(gross_loss   / len(losers),  2) if losers  else 0
    expectancy = round((win_rate/100 * avg_win) - ((1 - win_rate/100) * avg_loss), 2) if trades else 0

    # Equity curve & max drawdown
    equity_curve = []
    running = 0
    peak = 0
    max_dd = 0
    for s in summaries:
        running += s.net_pnl
        equity_curve.append({"date": s.date.isoformat(), "equity": round(running, 2)})
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    return {
        # Top-row cards
        "net_pnl":        round(total_net, 2),
        "total_commissions": round(total_commish, 2),
        "total_fees":     0.0,   # extend later if needed
        "win_rate":       win_rate,
        "loss_rate":      round(100 - win_rate, 1),
        "profit_factor":  profit_factor,
        "avg_winner":     avg_win,
        "avg_loser":      avg_loss,
        "expectancy":     expectancy,
        "max_drawdown":   round(max_dd, 2),
        "total_trades":   len(trades),
        "winning_trades": len(winners),
        "losing_trades":  len(losers),
        "hold_time":      _hold_time(trades),
        # Charts
        "equity_curve":   equity_curve,
        "avg_pnl_by_day": _avg_pnl_by_day(summaries),
        "win_rate_by_day":_win_rate_by_day(summaries),
        "drawdown_curve": _drawdown_curve(summaries),
        "pnl_by_day":     [{"date": s.date.isoformat(), "pnl": s.net_pnl} for s in summaries],
        # Performance tables
        "by_price":       _bucket_pnl(trades),
        "by_hour":        _hour_pnl(trades),
        "by_dow":         _dow_pnl(trades),
    }
