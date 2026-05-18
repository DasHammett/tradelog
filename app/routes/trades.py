from flask import Blueprint, render_template, request, jsonify
from app.models import OdsDailySymbol, StgExecution
from app.services.market_data import get_candles
from datetime import datetime

trades_bp = Blueprint("trades", __name__)


@trades_bp.route("/tradelog/trades")
def trades():
    """ODS view — one row per day+symbol, ordered by symbol then date desc."""
    symbol    = request.args.get("symbol", "").upper().strip()
    date_from = request.args.get("from", "")
    date_to   = request.args.get("to", "")

    query = OdsDailySymbol.query

    if symbol:
        query = query.filter(OdsDailySymbol.symbol == symbol)
    if date_from:
        query = query.filter(OdsDailySymbol.date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(OdsDailySymbol.date <= datetime.strptime(date_to, "%Y-%m-%d").date())

    # Group by symbol, then earliest date at bottom within each symbol
    rows = query.order_by(OdsDailySymbol.date.desc(),
                          OdsDailySymbol.symbol.asc()).limit(500).all()

    return render_template("trades.html", rows=rows, symbol=symbol,
                           date_from=date_from, date_to=date_to)


@trades_bp.route("/tradelog/trades/<string:trade_date>/<string:symbol>")
def trade_detail(trade_date, symbol):
    """STG drill-down — all executions for a date+symbol."""
    try:
        d = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date", 400

    ods = OdsDailySymbol.query.filter_by(date=d, symbol=symbol.upper()).first_or_404()
    executions = StgExecution.query.filter_by(date=d, symbol=symbol.upper())\
                                   .order_by(StgExecution.time.asc()).all()

    return render_template("trade_detail.html", ods=ods, executions=executions,
                           trade_date=d, symbol=symbol.upper())


@trades_bp.route("/tradelog/trades/<string:trade_date>/<string:symbol>/chart")
def trade_chart_data(trade_date, symbol):
    """JSON endpoint — 1-min candles for TradingView with entry/exit markers."""
    try:
        d = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400

    executions = StgExecution.query.filter_by(date=d, symbol=symbol.upper())\
                                   .order_by(StgExecution.time.asc()).all()
    if not executions:
        return jsonify({"error": "No executions found"})

    first_exec = executions[0]
    last_exec  = executions[-1]

    entry_dt = datetime.combine(first_exec.date, first_exec.time)
    exit_dt  = datetime.combine(last_exec.date,  last_exec.time)

    data = get_candles(
        symbol     = symbol.upper(),
        trade_date = d,
        entry_time = entry_dt,
        exit_time  = exit_dt,
    )
    return jsonify(data or {"error": "No market data available"})
