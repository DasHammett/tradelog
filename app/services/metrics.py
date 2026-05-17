from app.models import Trade, DailySummary
from app import db
from datetime import date, timedelta
import math


def compute_daily_summary(target_date: date) -> DailySummary:
    trades = Trade.query.filter(
        db.func.date(Trade.entry_time) == target_date,
        Trade.is_open == False
    ).all()

    winners = [t for t in trades if t.net_pnl and t.net_pnl > 0]
    losers  = [t for t in trades if t.net_pnl and t.net_pnl < 0]

    gross   = sum(t.gross_pnl or 0 for t in trades)
    net     = sum(t.net_pnl  or 0 for t in trades)
    commish = sum(t.commission or 0 for t in trades)

    summary = DailySummary.query.filter_by(date=target_date).first()
    if not summary:
        summary = DailySummary(date=target_date)
        db.session.add(summary)

    summary.total_trades    = len(trades)
    summary.winning_trades  = len(winners)
    summary.losing_trades   = len(losers)
    summary.gross_pnl       = round(gross, 2)
    summary.net_pnl         = round(net, 2)
    summary.total_commission = round(commish, 2)
    summary.win_rate        = round(len(winners) / len(trades) * 100, 1) if trades else 0
    summary.avg_winner      = round(sum(t.net_pnl for t in winners) / len(winners), 2) if winners else 0
    summary.avg_loser       = round(sum(t.net_pnl for t in losers)  / len(losers),  2) if losers  else 0

    db.session.commit()
    return summary


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
    losers  = [t for t in trades if t.net_pnl and t.net_pnl < 0]

    total_net   = sum(t.net_pnl or 0 for t in trades)
    gross_profit = sum(t.net_pnl for t in winners) if winners else 0
    gross_loss   = abs(sum(t.net_pnl for t in losers)) if losers else 0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else None

    win_rate = round(len(winners) / len(trades) * 100, 1) if trades else 0
    avg_win  = round(gross_profit / len(winners), 2) if winners else 0
    avg_loss = round(-gross_loss  / len(losers),  2) if losers  else 0
    expectancy = round((win_rate/100 * avg_win) - ((1 - win_rate/100) * abs(avg_loss)), 2) if trades else 0

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
        "net_pnl":       round(total_net, 2),
        "win_rate":      win_rate,
        "profit_factor": profit_factor,
        "avg_winner":    avg_win,
        "avg_loser":     avg_loss,
        "expectancy":    expectancy,
        "max_drawdown":  round(max_dd, 2),
        "total_trades":  len(trades),
        "equity_curve":  equity_curve,
        "pnl_by_day":    [{"date": s.date.isoformat(), "pnl": s.net_pnl} for s in summaries],
    }
