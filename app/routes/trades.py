from flask import Blueprint, render_template, request, jsonify
from app.models import OdsDailySymbol, StgExecution
from app.services.market_data import get_candles
from datetime import datetime
from collections import defaultdict

trades_bp = Blueprint("trades", __name__)


@trades_bp.route("/tradelog/trades")
def trades():
    """ODS view — one row per day+symbol, ordered by date desc."""
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

    rows = query.order_by(OdsDailySymbol.date.desc(),
                          OdsDailySymbol.symbol.asc()).limit(500).all()

    # Sum commission breakdown fields from STG for each (date, symbol) pair
    breakdown = {}
    if rows:
        pairs = [(r.date, r.symbol) for r in rows]
        from sqlalchemy import tuple_
        stg_rows = StgExecution.query.filter(
            tuple_(StgExecution.date, StgExecution.symbol).in_(pairs)
        ).with_entities(
            StgExecution.date,
            StgExecution.symbol,
            StgExecution.broker_charge,
            StgExecution.third_party_charge,
            StgExecution.clearing_charge,
            StgExecution.regulatory_charge,
        ).all()

        agg = defaultdict(lambda: {"broker": 0.0, "third_party": 0.0, "clearing": 0.0, "regulatory": 0.0})
        for s in stg_rows:
            key = (s.date, s.symbol)
            agg[key]["broker"]      += s.broker_charge      or 0.0
            agg[key]["third_party"] += s.third_party_charge or 0.0
            agg[key]["clearing"]    += s.clearing_charge    or 0.0
            agg[key]["regulatory"]  += s.regulatory_charge  or 0.0
        breakdown = dict(agg)

    return render_template("trades.html", rows=rows, breakdown=breakdown,
                           symbol=symbol, date_from=date_from, date_to=date_to)


def _compute_exec_rows(executions):
    """
    For each execution compute:
      - avg_pos:   running weighted avg cost after this execution (shown in Avg Position column)
      - gross_pnl: (sell_price - avg_buy_price) * qty  — only on SELLs
      - net_pnl:   gross_pnl - sell_commission         — only on SELLs
                   buy commission is already baked into cost_basis per share,
                   so net_pnl = (sell_price - cost_basis) * qty

    cost_basis is computed by _compute_rt_trades and stored on each SELL StgExecution row.
    It equals avg_buy_price + (total_buy_commission / total_buy_qty) at the time of the sell.
    """
    pos_qty      = 0.0
    pos_avg_cost = 0.0
    result       = []

    for ex in executions:
        if ex.side == "BUY":
            total_cost   = pos_avg_cost * pos_qty + ex.price * ex.quantity
            pos_qty     += ex.quantity
            pos_avg_cost = total_cost / pos_qty

        elif ex.side == "SELL":
            pos_qty -= ex.quantity
            if pos_qty <= 0.0001:
                pos_qty      = 0.0
                pos_avg_cost = 0.0

        avg_after = pos_avg_cost if pos_qty > 0.0001 else None

        # P&L on SELLs only, using cost_basis (includes buy commission per share)
        if ex.side == "SELL" and ex.cost_basis is not None:
            gross = round((ex.price - ex.cost_basis) * ex.quantity, 2)
            net   = round(gross - (ex.commission or 0.0), 2)
        else:
            gross = None
            net   = None

        result.append({
            "ex":        ex,
            "avg_pos":   avg_after,
            "gross_pnl": gross,
            "net_pnl":   net,
        })

    return result


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

    exec_rows = _compute_exec_rows(executions)

    return render_template("trade_detail.html", ods=ods, exec_rows=exec_rows,
                           trade_date=d, symbol=symbol.upper())


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