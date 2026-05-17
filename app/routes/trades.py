from flask import Blueprint, render_template, request, jsonify
from app.models import Trade
from app.services.market_data import get_candles
from app import db
from datetime import datetime

trades_bp = Blueprint("trades", __name__)


@trades_bp.route("/tradelog/trades")
def trades():
    symbol = request.args.get("symbol", "").upper()
    date_from = request.args.get("from", "")
    date_to   = request.args.get("to", "")

    query = Trade.query.order_by(Trade.entry_time.desc())

    if symbol:
        query = query.filter(Trade.symbol == symbol)
    if date_from:
        query = query.filter(Trade.entry_time >= datetime.strptime(date_from, "%Y-%m-%d"))
    if date_to:
        query = query.filter(Trade.entry_time <= datetime.strptime(date_to, "%Y-%m-%d"))

    trades = query.limit(200).all()
    return render_template("trades.html", trades=trades, symbol=symbol,
                           date_from=date_from, date_to=date_to)


@trades_bp.route("/tradelog/trades/<int:trade_id>")
def trade_detail(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    return render_template("trade_detail.html", trade=trade)


@trades_bp.route("/tradelog/trades/<int:trade_id>/chart")
def trade_chart_data(trade_id):
    """JSON endpoint for TradingView chart data."""
    trade = Trade.query.get_or_404(trade_id)
    data = get_candles(
        symbol=trade.symbol,
        trade_date=trade.entry_time,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
    )
    return jsonify(data or {"error": "No data available"})


@trades_bp.route("/tradelog/trades/<int:trade_id>/notes", methods=["POST"])
def update_notes(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    trade.notes = request.form.get("notes", "")
    db.session.commit()
    return jsonify({"ok": True})
