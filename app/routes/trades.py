from flask import Blueprint, render_template, request, jsonify
from app.models import OdsDailySymbol, StgExecution, RtTrade
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
    # rt_by_exit: keyed by exit time → gross/net P&L for SELL rows
    rt_rows = RtTrade.query.filter_by(date=d, symbol=symbol.upper(), is_open=False).all()
    rt_by_exit: dict = {}
    for rt in rt_rows:
        key = rt.exit_time.time()
        if key in rt_by_exit:
            existing  = rt_by_exit[key]
            total_qty = existing["quantity"] + rt.quantity
            existing["entry_price"] = (
                existing["entry_price"] * existing["quantity"]
                + rt.entry_price * rt.quantity
            ) / total_qty
            existing["gross_pnl"] += rt.gross_pnl
            existing["net_pnl"]   += rt.net_pnl
            existing["quantity"]   = total_qty
        else:
            rt_by_exit[key] = {
                "entry_price": rt.entry_price,
                "gross_pnl":   rt.gross_pnl,
                "net_pnl":     rt.net_pnl,
                "quantity":    rt.quantity,
            }
    # avg_by_buy: keyed by BUY execution time → running weighted avg cost after that buy
    avg_by_buy: dict = {}
    pos_qty      = 0.0
    pos_avg_cost = 0.0
    for ex in executions:
        if ex.side == "BUY":
            pos_avg_cost = (pos_avg_cost * pos_qty + ex.price * ex.quantity) / (pos_qty + ex.quantity)
            pos_qty     += ex.quantity
            avg_by_buy[ex.time] = round(pos_avg_cost, 6)
        elif ex.side == "SELL":
            pos_qty -= ex.quantity
            if pos_qty < 0.0001:
                pos_qty      = 0.0
                pos_avg_cost = 0.0
    return render_template("trade_detail.html", ods=ods, executions=executions,
                           trade_date=d, symbol=symbol.upper(),
                           rt_by_exit=rt_by_exit, avg_by_buy=avg_by_buy)
@trades_bp.route("/tradelog/trades/<string:trade_date>/<string:symbol>/chart")
def trade_chart_data(trade_date, symbol):
    """JSON endpoint — 1-min candles for TradingView with one marker per execution."""
    try:
        d = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400
    executions = StgExecution.query.filter_by(date=d, symbol=symbol.upper())\
                                   .order_by(StgExecution.time.asc()).all()
    if not executions:
        return jsonify({"error": "No executions found for this session"})
    data = get_candles(
        symbol     = symbol.upper(),
        trade_date = d,
        executions = executions,
    )
    return jsonify(data or {"error": "No market data available"})
