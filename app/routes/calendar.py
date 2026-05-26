from flask import Blueprint, render_template, request
from app.models import DailySummary, RtTrade
from app import db
from datetime import date, timedelta
import calendar as cal_module
calendar_bp = Blueprint("calendar", __name__)
def _get_month_data(year: int, month: int) -> dict:
    """
    Returns a dict keyed by date with day stats, and a list of weeks.
    Each week is a list of 5 dicts (Mon–Fri), plus a week_summary.
    """
    # Date range for the month
    first_day = date(year, month, 1)
    last_day  = date(year, month, cal_module.monthrange(year, month)[1])
    # Pull DailySummary rows
    summaries = DailySummary.query.filter(
        DailySummary.date >= first_day,
        DailySummary.date <= last_day,
    ).all()
    summary_map = {s.date: s for s in summaries}
    # Pull RtTrade rows (for gross_pnl per day since DailySummary has it)
    # DailySummary already has gross_pnl so we use that directly
    # Build calendar weeks (Mon–Fri only)
    # Find first Monday on or before first_day
    cal = cal_module.Calendar(firstweekday=0)  # Monday first
    month_days = cal.monthdatescalendar(year, month)  # list of weeks, each 7 days
    weeks = []
    for week in month_days:
        mon_to_fri = week[:5]  # indices 0-4 are Mon–Fri
        week_days  = []
        week_net   = 0.0
        week_gross = 0.0
        week_trades = 0
        has_data   = False
        for d in mon_to_fri:
            summary = summary_map.get(d)
            if summary and d.month == month:
                day_data = {
                    "date":         d,
                    "in_month":     True,
                    "gross_pnl":    summary.gross_pnl,
                    "net_pnl":      summary.net_pnl,
                    "trades":       summary.total_trades,
                    "is_winner":    summary.net_pnl >= 0,
                    "has_data":     True,
                }
                week_net    += summary.net_pnl
                week_gross  += summary.gross_pnl
                week_trades += summary.total_trades
                has_data     = True
            else:
                day_data = {
                    "date":     d,
                    "in_month": d.month == month,
                    "has_data": False,
                }
            week_days.append(day_data)
        weeks.append({
            "days":         week_days,
            "week_net":     round(week_net,   2),
            "week_gross":   round(week_gross, 2),
            "week_trades":  week_trades,
            "has_data":     has_data,
            "is_winner":    week_net >= 0,
        })
    return weeks
@calendar_bp.route("/tradelog/calendar")
def calendar_view():
    today = date.today()
    year  = int(request.args.get("year",  today.year))
    month = int(request.args.get("month", today.month))
    # Clamp
    if month < 1:  month = 1
    if month > 12: month = 12
    # Prev / next month
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    weeks      = _get_month_data(year, month)
    month_name = date(year, month, 1).strftime("%B %Y")
    # Month totals
    month_net    = sum(w["week_net"]   for w in weeks)
    month_gross  = sum(w["week_gross"] for w in weeks)
    month_trades = sum(w["week_trades"] for w in weeks)
    return render_template(
        "calendar.html",
        weeks        = weeks,
        month_name   = month_name,
        year         = year,
        month        = month,
        prev_year    = prev_year,
        prev_month   = prev_month,
        next_year    = next_year,
        next_month   = next_month,
        today        = today,
        month_net    = round(month_net,   2),
        month_gross  = round(month_gross, 2),
        month_trades = month_trades,
    )
