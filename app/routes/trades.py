from flask import Blueprint, render_template, request, jsonify
from sqlalchemy import func, or_, tuple_
from app import db
from app.models import OdsDailySymbol, RtTrade, StgExecution, Strategy, TradeTag
from app.services.market_data import get_candles
from app.services.grouping import group_executions, annotate_cycles, POSITION_EPS
from datetime import datetime
from collections import defaultdict
trades_bp = Blueprint("trades", __name__)
@trades_bp.route("/tradelog/trades")
def trades():
    """
    ODS view — one row per day+symbol, ordered by date desc.
    Optional tag filter (?tag=<strategy id> | none): keeps the sessions that contain at
    least one closed round trip with that tag (or no tag), using the same rt_trades +
    trade_tags join as the dashboard, and adds per-tag trade count / net P&L columns so
    the totals reconcile with the dashboard's Performance By Tag panel.
    """
    symbol    = request.args.get("symbol", "").upper().strip()
    date_from = request.args.get("from", "")
    date_to   = request.args.get("to", "")
    tag       = request.args.get("tag", "").strip()

    d_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    d_to   = datetime.strptime(date_to,   "%Y-%m-%d").date() if date_to   else None

    # Normalise the tag parameter: "" (all), "none" (untagged) or a strategy id
    tag_id = None
    if tag and tag != "none":
        try:
            tag_id = int(tag)
        except ValueError:
            tag = ""
    tag_stats = {}   # (date, symbol) -> {"trades": n, "net": pnl}   (only when a tag filter is active)

    query = OdsDailySymbol.query
    if symbol:
        query = query.filter(OdsDailySymbol.symbol == symbol)
    if d_from:
        query = query.filter(OdsDailySymbol.date >= d_from)
    if d_to:
        query = query.filter(OdsDailySymbol.date <= d_to)

    if tag:
        rt_q = RtTrade.query.filter(RtTrade.is_open == False)   # noqa: E712
        if symbol:
            rt_q = rt_q.filter(RtTrade.symbol == symbol)
        if d_from:
            rt_q = rt_q.filter(RtTrade.date >= d_from)
        if d_to:
            rt_q = rt_q.filter(RtTrade.date <= d_to)
        if tag == "none":
            tagged_ids = db.session.query(TradeTag.exec_id)
            rt_q = rt_q.filter(or_(RtTrade.exec_id.is_(None), ~RtTrade.exec_id.in_(tagged_ids)))
        else:
            rt_q = rt_q.join(TradeTag, TradeTag.exec_id == RtTrade.exec_id)\
                       .filter(TradeTag.strategy_id == tag_id)
        for r in rt_q.all():
            st = tag_stats.setdefault((r.date, r.symbol), {"trades": 0, "net": 0.0})
            st["trades"] += 1
            st["net"]    += r.net_pnl or 0.0
        pairs = list(tag_stats.keys())
        query = query.filter(tuple_(OdsDailySymbol.date, OdsDailySymbol.symbol).in_(pairs)) \
                if pairs else query.filter(db.false())

    rows = query.order_by(OdsDailySymbol.date.desc(),
                          OdsDailySymbol.symbol.asc()).limit(500).all()
    # Sum commission breakdown fields from STG for each (date, symbol) pair
    breakdown = {}
    if rows:
        pairs = [(r.date, r.symbol) for r in rows]
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

    # Tag totals over the listed sessions (reconciles with the dashboard panel)
    tag_totals = None
    if tag:
        tag_totals = {
            "trades": sum(tag_stats.get((r.date, r.symbol), {}).get("trades", 0) for r in rows),
            "net":    round(sum(tag_stats.get((r.date, r.symbol), {}).get("net", 0.0) for r in rows), 2),
        }

    # Dropdown: active strategies, plus hidden ones that still have tagged history
    used_ids = {sid for (sid,) in db.session.query(TradeTag.strategy_id).distinct().all()}
    strategies = [st for st in Strategy.query.order_by(Strategy.sort_order.asc(), Strategy.name.asc()).all()
                  if st.is_active or st.id in used_ids]

    return render_template("trades.html", rows=rows, breakdown=breakdown,
                           symbol=symbol, date_from=date_from, date_to=date_to,
                           tag=tag, strategies=strategies,
                           tag_stats=tag_stats, tag_totals=tag_totals)


def _compute_exec_rows(groups):
    """
    For each FillGroup (partial fills of one order merged — see
    app/services/grouping.py) compute:
      - avg_pos:   running cost basis per share including buy commissions
                   = (total_buy_value + total_buy_commission) / total_buy_qty
                   Shown in Avg Position column. NULL once position fully closed.
      - gross_pnl: (sell_price - avg_buy_price) * qty  — pure price, no commissions.
                   Consistent with ODS gross_pnl so they sum correctly.
      - net_pnl:   gross_pnl - sell_commission - proportional_buy_commission
    All state variables are captured BEFORE updating position so SELL calculations
    use the correct pre-sell values.
    Each row also carries the group's flat-to-flat `cycle` number.
    """
    pos_qty        = 0.0
    pos_avg_price  = 0.0   # weighted avg buy price (no commission) — for gross P&L
    pos_cost_total = 0.0   # total cost including commissions — for avg_pos display
    pos_commission = 0.0   # accumulated buy commissions — for net P&L allocation
    result         = []
    for ex in groups:
        if ex.side == "BUY":
            total_price    = pos_avg_price * pos_qty + ex.price * ex.quantity
            pos_qty       += ex.quantity
            pos_avg_price  = total_price / pos_qty
            pos_cost_total += ex.quantity * ex.price + (ex.commission or 0.0)
            pos_commission += (ex.commission or 0.0)
            gross = None
            net   = None
        elif ex.side == "SELL":
            if pos_qty > POSITION_EPS:
                sell_qty         = min(ex.quantity, pos_qty)
                # Capture pre-sell values for P&L calculation
                avg_price_before = pos_avg_price
                buy_comm_portion = pos_commission * (sell_qty / pos_qty)
                gross = round((ex.price - avg_price_before) * sell_qty, 2)
                net   = round(gross - (ex.commission or 0.0) - buy_comm_portion, 2)
                # Update position
                pos_qty        -= sell_qty
                pos_commission -= buy_comm_portion
                if pos_qty <= POSITION_EPS:
                    pos_qty        = 0.0
                    pos_avg_price  = 0.0
                    pos_cost_total = 0.0
                    pos_commission = 0.0
                else:
                    pos_cost_total *= (pos_qty / (pos_qty + sell_qty))
            else:
                gross = None
                net   = None
        else:
            gross = None
            net   = None
        avg_after = round(pos_cost_total / pos_qty, 4) if pos_qty > POSITION_EPS else None
        result.append({
            "ex":        ex,
            "avg_pos":   avg_after,
            "gross_pnl": gross,
            "net_pnl":   net,
            "cycle":     ex.cycle,
            "strategy_id": None,   # filled in by trade_detail from trade_tags
        })
    return result
@trades_bp.route("/tradelog/trades/<string:trade_date>/<string:symbol>")
def trade_detail(trade_date, symbol):
    """STG drill-down — all executions for a date+symbol, partial fills grouped."""
    try:
        d = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date", 400
    ods = OdsDailySymbol.query.filter_by(date=d, symbol=symbol.upper()).first_or_404()
    executions = StgExecution.query.filter_by(date=d, symbol=symbol.upper())\
                                   .order_by(StgExecution.time.asc()).all()
    groups = annotate_cycles(group_executions(executions))
    exec_rows = _compute_exec_rows(groups)

    # Strategy tag per SELL group: first tag found on any member execution
    tag_by_exec = {
        t.exec_id: t.strategy_id
        for t in TradeTag.query.filter(
            TradeTag.exec_id.in_([e.exec_id for e in executions])
        ).all()
    }
    for row in exec_rows:
        g = row["ex"]
        if g.side == "SELL":
            row["strategy_id"] = next(
                (tag_by_exec[eid] for eid in g.exec_ids if eid in tag_by_exec), None
            )
    strategies = Strategy.query.order_by(Strategy.sort_order.asc(), Strategy.name.asc()).all()
    return render_template(
        "trade_detail.html", ods=ods, exec_rows=exec_rows,
        execution_count=len(executions), strategies=strategies,
        strategies_json=[_strategy_dict(st) for st in strategies],
        trade_date=d, symbol=symbol.upper(),
    )
@trades_bp.route("/tradelog/trades/<string:trade_date>/<string:symbol>/chart")
def trade_chart_data(trade_date, symbol):
    """JSON endpoint — 1-min candles, indicators and one segment per (grouped) fill."""
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
        fills      = group_executions(executions),
    )
    return jsonify(data or {"error": "No market data available"})


# ---------------------------------------------------------------------------
# Strategy tags
# ---------------------------------------------------------------------------

def _strategy_dict(s):
    return {"id": s.id, "name": s.name, "is_active": bool(s.is_active)}


def _strategies_payload():
    rows = Strategy.query.order_by(Strategy.sort_order.asc(), Strategy.name.asc()).all()
    return [_strategy_dict(r) for r in rows]


@trades_bp.route("/tradelog/trades/tag", methods=["POST"])
def set_trade_tag():
    """
    Set (or clear) the strategy tag of one or more SELL executions.
    Body: {"exec_ids": ["FLEX-1", ...], "strategy_id": 3 | null}
    The page sends every member exec_id of a grouped SELL (and, for "apply to
    cycle", of every SELL in the cycle).
    """
    data = request.get_json(silent=True) or {}
    exec_ids = data.get("exec_ids")
    if (not isinstance(exec_ids, list) or not exec_ids or len(exec_ids) > 500
            or not all(isinstance(e, str) and e for e in exec_ids)):
        return jsonify({"ok": False, "error": "exec_ids must be a non-empty list of strings"}), 400
    exec_ids = list(dict.fromkeys(exec_ids))

    rows = StgExecution.query.filter(StgExecution.exec_id.in_(exec_ids)).all()
    if len(rows) != len(exec_ids):
        return jsonify({"ok": False, "error": "Unknown execution id"}), 404
    if any(r.side != "SELL" for r in rows):
        return jsonify({"ok": False, "error": "Only SELL executions can be tagged"}), 400

    strategy_id = data.get("strategy_id")
    if strategy_id in (None, "", 0):
        TradeTag.query.filter(TradeTag.exec_id.in_(exec_ids)).delete(synchronize_session=False)
        db.session.commit()
        return jsonify({"ok": True, "strategy_id": None, "count": len(exec_ids)})

    try:
        strategy = db.session.get(Strategy, int(strategy_id))
    except (TypeError, ValueError):
        strategy = None
    if strategy is None:
        return jsonify({"ok": False, "error": "Unknown strategy"}), 404

    existing = {t.exec_id: t for t in TradeTag.query.filter(TradeTag.exec_id.in_(exec_ids)).all()}
    for eid in exec_ids:
        tag = existing.get(eid)
        if tag:
            tag.strategy_id = strategy.id
        else:
            db.session.add(TradeTag(exec_id=eid, strategy_id=strategy.id))
    db.session.commit()
    return jsonify({"ok": True, "strategy_id": strategy.id, "count": len(exec_ids)})


@trades_bp.route("/tradelog/strategies", methods=["POST"])
def create_strategy():
    """Create a strategy (or return / re-activate an existing one with the same name)."""
    data = request.get_json(silent=True) or {}
    name = " ".join(str(data.get("name", "")).split())
    if not name or len(name) > 60:
        return jsonify({"ok": False, "error": "Name must be 1-60 characters"}), 400
    existing = Strategy.query.filter(func.lower(Strategy.name) == name.lower()).first()
    if existing:
        if not existing.is_active:
            existing.is_active = True
            db.session.commit()
        return jsonify({"ok": True, "existing": True,
                        "strategy": _strategy_dict(existing),
                        "strategies": _strategies_payload()})
    next_order = (db.session.query(func.max(Strategy.sort_order)).scalar() or 0) + 1
    strategy = Strategy(name=name, is_active=True, sort_order=next_order)
    db.session.add(strategy)
    db.session.commit()
    return jsonify({"ok": True, "existing": False,
                    "strategy": _strategy_dict(strategy),
                    "strategies": _strategies_payload()}), 201


@trades_bp.route("/tradelog/strategies/<int:strategy_id>", methods=["POST"])
def update_strategy(strategy_id):
    """Rename and/or hide / show a strategy. Hidden strategies keep their history."""
    strategy = db.session.get(Strategy, strategy_id)
    if strategy is None:
        return jsonify({"ok": False, "error": "Unknown strategy"}), 404
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = " ".join(str(data.get("name", "")).split())
        if not name or len(name) > 60:
            return jsonify({"ok": False, "error": "Name must be 1-60 characters"}), 400
        clash = Strategy.query.filter(func.lower(Strategy.name) == name.lower(),
                                      Strategy.id != strategy.id).first()
        if clash:
            return jsonify({"ok": False, "error": f"'{clash.name}' already exists"}), 409
        strategy.name = name
    if "is_active" in data:
        strategy.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"ok": True, "strategy": _strategy_dict(strategy),
                    "strategies": _strategies_payload()})
