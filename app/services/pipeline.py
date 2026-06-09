"""
Flex XML Import Pipeline
========================
Step 1 — Parse:     Read Flex XML into a Pandas DataFrame (STG shape)
Step 2 — Validate:  Check field integrity before touching the DB
Step 3 — Dedup:     Filter out exec_ids already in stg_executions (skip, not abort)
Step 4 — Write STG: Insert new raw execution rows
Step 5 — Write ODS: Recompute ods_daily_symbol for affected date+symbol combos
Step 6 — Compute RT: FIFO avg-cost matching → rt_trades
Step 7 — Write DWH: Recompute daily_summary from rt_trades
"""
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, date
from app import db
from app.models import StgExecution, OdsDailySymbol, DailySummary, RtTrade
# ---------------------------------------------------------------------------
# Step 1 — Parse Flex XML into DataFrame
# ---------------------------------------------------------------------------
def _parse_flex_xml(content: str):
    """
    Parse a FlexQueryResponse XML string.
    Joins <Trade> (orderType) with <UnbundledCommissionDetail> on tradeID.
    Returns (DataFrame, errors).
    """
    errors = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return None, [f"XML parse error: {e}"]
    # Build tradeID → orderType lookup from <Trade> tags
    order_type_map = {}
    for trade in root.iter("Trade"):
        tid = trade.attrib.get("tradeID", "").strip()
        ot  = trade.attrib.get("orderType", "").strip()
        if tid:
            order_type_map[tid] = ot
    rows = []
    for detail in root.iter("UnbundledCommissionDetail"):
        a = detail.attrib
        tid = a.get("tradeID", "").strip()
        if not tid:
            errors.append("UnbundledCommissionDetail missing tradeID — skipped")
            continue
        rows.append({
            "exec_id":            f"FLEX-{tid}",
            "symbol":             a.get("symbol",                   "").strip(),
            "date_time":          a.get("dateTime",                 "").strip(),
            "side":               a.get("buySell",                  "").strip().upper(),
            "quantity":           a.get("quantity",                 "0"),
            "price":              a.get("price",                    "0"),
            "commission":         a.get("totalCommission",          "0"),
            "currency":           a.get("currency",                 "USD").strip() or "USD",
            "exchange":           a.get("exchange",                 "").strip(),
            "broker_charge":      a.get("brokerExecutionCharge",    "0"),
            "third_party_charge": a.get("thirdPartyExecutionCharge","0"),
            "clearing_charge":    a.get("thirdPartyClearingCharge", "0"),
            "regulatory_charge":  a.get("thirdPartyRegulatoryCharge","0"),
            "open_close":         "",   # filled below from Trade tag
            "order_type":         order_type_map.get(tid, ""),
            "source":             "FLEX",
        })
        # Back-fill open_close from Trade tag
        for trade in root.iter("Trade"):
            if trade.attrib.get("tradeID", "").strip() == tid:
                rows[-1]["open_close"] = trade.attrib.get("openCloseIndicator", "").strip()
                break
    if not rows:
        return None, errors + ["No UnbundledCommissionDetail rows found in XML"]
    df = pd.DataFrame(rows)
    return df, errors
# ---------------------------------------------------------------------------
# Step 2 — Validate & clean
# ---------------------------------------------------------------------------
def _validate_and_clean(df: pd.DataFrame):
    """
    Type-cast, normalise, and validate. Returns (clean_df, errors).
    Rows with unrecoverable errors are dropped and reported.
    """
    errors = []
    df = df.copy()
    # Drop rows with missing symbol
    missing_sym = df["symbol"].str.len() == 0
    if missing_sym.any():
        errors.append(f"{missing_sym.sum()} row(s) with empty symbol — skipped")
        df = df[~missing_sym].copy()
    # Validate side
    valid_sides = {"BUY", "SELL"}
    bad_side = ~df["side"].isin(valid_sides)
    if bad_side.any():
        for _, row in df[bad_side].iterrows():
            errors.append(f"Unknown buySell '{row.side}' for {row.symbol} — skipped")
        df = df[~bad_side].copy()
    # Parse dateTime — format is YYYYMMDD;HH:MM:SS
    def parse_dt(val):
        try:
            return datetime.strptime(val, "%Y%m%d;%H%M%S")
        except ValueError:
            try:
                return datetime.strptime(val, "%Y%m%d;%H:%M:%S")
            except ValueError:
                return pd.NaT
    df["datetime"] = df["date_time"].apply(parse_dt)
    bad_dt = df["datetime"].isna()
    if bad_dt.any():
        for _, row in df[bad_dt].iterrows():
            errors.append(f"Bad dateTime '{row.date_time}' for {row.symbol} — skipped")
        df = df[~bad_dt].copy()
    df["date"] = df["datetime"].dt.date
    df["time"] = df["datetime"].dt.time
    # Numeric columns — quantity and commission can be negative in Flex XML
    for col in ["quantity", "price", "commission",
                "broker_charge", "third_party_charge",
                "clearing_charge", "regulatory_charge"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # qty and price are required
    bad_num = df[["quantity", "price"]].isna().any(axis=1)
    if bad_num.any():
        for _, row in df[bad_num].iterrows():
            errors.append(f"Non-numeric qty/price for {row.symbol} at {row.date_time} — skipped")
        df = df[~bad_num].copy()
    # Flex sends negative qty for sells — take abs on quantity only.
    # Flex commission sign convention: negative = cost, positive = rebate.
    # We invert so that positive = cost, negative = rebate (natural for display).
    df["quantity"]           = df["quantity"].abs()
    df["commission"]         = -df["commission"].fillna(0.0)
    df["broker_charge"]      = -df["broker_charge"].fillna(0.0)
    df["third_party_charge"] = -df["third_party_charge"].fillna(0.0)
    df["clearing_charge"]    = -df["clearing_charge"].fillna(0.0)
    df["regulatory_charge"]  = -df["regulatory_charge"].fillna(0.0)
    # Drop zero / negative prices
    bad_price = df["price"] <= 0
    if bad_price.any():
        for _, row in df[bad_price].iterrows():
            errors.append(f"Invalid price {row.price} for {row.symbol} — skipped")
        df = df[~bad_price].copy()
    if df.empty:
        return df, errors
    # Keep only needed columns
    df = df[[
        "exec_id", "symbol", "date", "time", "side",
        "quantity", "price", "commission", "currency", "source",
        "order_type", "exchange", "open_close",
        "broker_charge", "third_party_charge", "clearing_charge", "regulatory_charge",
    ]].copy()
    return df, errors
# ---------------------------------------------------------------------------
# Step 3 — Dedup (skip, not abort)
# ---------------------------------------------------------------------------
def _filter_duplicates(df: pd.DataFrame):
    """
    Returns (new_df, duplicate_ids).
    Rows whose exec_id already exists in STG are silently skipped.
    """
    incoming_ids = df["exec_id"].tolist()
    existing = db.session.query(StgExecution.exec_id)\
                 .filter(StgExecution.exec_id.in_(incoming_ids))\
                 .all()
    existing_ids = {row.exec_id for row in existing}
    duplicates   = [eid for eid in incoming_ids if eid in existing_ids]
    new_df       = df[~df["exec_id"].isin(existing_ids)].copy()
    return new_df, duplicates
# ---------------------------------------------------------------------------
# Step 4 — Write STG
# ---------------------------------------------------------------------------
def _write_stg(df: pd.DataFrame):
    for _, row in df.iterrows():
        ex = StgExecution(
            exec_id            = row.exec_id,
            symbol             = row.symbol,
            date               = row.date,
            time               = row.time,
            side               = row.side,
            quantity           = float(row.quantity),
            price              = float(row.price),
            commission         = float(row.commission),
            currency           = row.currency or "USD",
            source             = row.source,
            order_type         = row.order_type   or None,
            exchange           = row.exchange     or None,
            open_close         = row.open_close   or None,
            broker_charge      = float(row.broker_charge),
            third_party_charge = float(row.third_party_charge),
            clearing_charge    = float(row.clearing_charge),
            regulatory_charge  = float(row.regulatory_charge),
            cost_basis         = None,   # populated by _compute_rt_trades
        )
        db.session.add(ex)
    db.session.commit()
# ---------------------------------------------------------------------------
# Step 5 — Recompute ODS for affected date+symbol combos
# ---------------------------------------------------------------------------
def _recompute_ods(affected: list):
    """affected: list of (date, symbol) tuples."""
    for trade_date, symbol in affected:
        rows = StgExecution.query.filter_by(date=trade_date, symbol=symbol).all()
        if not rows:
            continue
        df = pd.DataFrame([{
            "side":       r.side,
            "quantity":   r.quantity,
            "price":      r.price,
            "commission": r.commission,
        } for r in rows])
        buys  = df[df["side"] == "BUY"]
        sells = df[df["side"] == "SELL"]
        bought_qty = buys["quantity"].sum()
        sold_qty   = sells["quantity"].sum()
        avg_buy  = (buys["price"]  * buys["quantity"]).sum()  / bought_qty if bought_qty > 0 else 0.0
        avg_sell = (sells["price"] * sells["quantity"]).sum() / sold_qty   if sold_qty   > 0 else 0.0
        total_commission = df["commission"].sum()
        gross_pnl = round((avg_sell * sold_qty) - (avg_buy * bought_qty), 4)
        net_pnl   = round(gross_pnl - total_commission, 4)
        ods = OdsDailySymbol.query.filter_by(date=trade_date, symbol=symbol).first()
        if not ods:
            ods = OdsDailySymbol(date=trade_date, symbol=symbol)
            db.session.add(ods)
        ods.bought_qty       = round(float(bought_qty), 4)
        ods.sold_qty         = round(float(sold_qty),   4)
        ods.avg_buy_price    = round(float(avg_buy),    6)
        ods.avg_sell_price   = round(float(avg_sell),   6)
        ods.total_commission = round(float(total_commission), 4)
        ods.gross_pnl        = gross_pnl
        ods.net_pnl          = net_pnl
    db.session.commit()
# ---------------------------------------------------------------------------
# Step 6 — FIFO avg-cost matching → rt_trades
# ---------------------------------------------------------------------------
def _compute_rt_trades(affected: list, warnings: list):
    """
    For each (date, symbol) pair, pull STG rows sorted by time and apply
    avg-cost FIFO matching.
    """
    for trade_date, symbol in affected:
        RtTrade.query.filter_by(date=trade_date, symbol=symbol).delete()
        execs = StgExecution.query\
            .filter_by(date=trade_date, symbol=symbol)\
            .order_by(StgExecution.time.asc(), StgExecution.side.asc()).all()
            # side.asc() sorts "BUY" before "SELL" alphabetically (B < S),
            # ensuring a same-second stop-loss doesn't arrive before its BUY
        pos_qty        = 0.0
        pos_avg_cost   = 0.0
        pos_commission = 0.0
        pos_entry_time = None
        for ex in execs:
            exec_dt = datetime.combine(trade_date, ex.time)
            if ex.side == "BUY":
                total_cost     = pos_avg_cost * pos_qty + ex.price * ex.quantity
                pos_qty       += ex.quantity
                pos_avg_cost   = total_cost / pos_qty
                pos_commission += ex.commission
                if pos_entry_time is None:
                    pos_entry_time = exec_dt
            elif ex.side == "SELL":
                if pos_qty <= 0:
                    warnings.append(
                        f"Orphaned SELL {ex.quantity} {symbol} @ {ex.price} "
                        f"at {ex.time} — no open position, skipped"
                    )
                    continue
                sell_qty = min(ex.quantity, pos_qty)
                buy_commission_portion = pos_commission * (sell_qty / pos_qty)
                # Cost basis per share: avg price + proportional buy commission per share
                cost_basis_per_share = round(
                    pos_avg_cost + (pos_commission / pos_qty), 6
                )
                gross_pnl        = round((ex.price - pos_avg_cost) * sell_qty, 6)
                total_commission = round(buy_commission_portion + ex.commission, 6)
                net_pnl          = round(gross_pnl - total_commission, 6)
                # Write cost_basis back to the STG execution row
                ex.cost_basis = cost_basis_per_share
                rt = RtTrade(
                    date         = trade_date,
                    symbol       = symbol,
                    entry_time   = pos_entry_time,
                    exit_time    = exec_dt,
                    quantity     = sell_qty,
                    entry_price  = round(pos_avg_cost, 6),
                    exit_price   = round(ex.price, 6),
                    commission   = total_commission,
                    gross_pnl    = round(gross_pnl, 2),
                    net_pnl      = round(net_pnl, 2),
                    is_open      = False,
                )
                db.session.add(rt)
                pos_qty        -= sell_qty
                pos_commission -= buy_commission_portion
                if pos_qty <= 0.0001:
                    pos_qty        = 0.0
                    pos_avg_cost   = 0.0
                    pos_commission = 0.0
                    pos_entry_time = None
        # Residual open position (shouldn't happen for a day trader)
        if pos_qty > 0.0001:
            rt = RtTrade(
                date         = trade_date,
                symbol       = symbol,
                entry_time   = pos_entry_time,
                exit_time    = datetime.combine(trade_date, execs[-1].time),
                quantity     = pos_qty,
                entry_price  = round(pos_avg_cost, 6),
                exit_price   = 0.0,   # open position — no exit price yet; 0.0 satisfies NOT NULL
                commission   = round(pos_commission, 6),
                gross_pnl    = 0.0,
                net_pnl      = 0.0,
                is_open      = True,
            )
            db.session.add(rt)
            warnings.append(f"Open position {pos_qty} {symbol} on {trade_date} — no matching SELL found")
    db.session.commit()
# ---------------------------------------------------------------------------
# Step 7 — Recompute DailySummary from rt_trades
# ---------------------------------------------------------------------------
def _recompute_daily_summary(affected_dates: set):
    for trade_date in affected_dates:
        rt_rows  = RtTrade.query.filter_by(date=trade_date, is_open=False).all()
        ods_rows = OdsDailySymbol.query.filter_by(date=trade_date).all()
        if not rt_rows and not ods_rows:
            continue
        winners = [r for r in rt_rows if r.net_pnl > 0]
        losers  = [r for r in rt_rows if r.net_pnl < 0]
        summary = DailySummary.query.filter_by(date=trade_date).first()
        if not summary:
            summary = DailySummary(date=trade_date)
            db.session.add(summary)
        summary.total_trades     = len(rt_rows)
        summary.winning_trades   = len(winners)
        summary.losing_trades    = len(losers)
        summary.gross_pnl        = round(sum(r.gross_pnl for r in rt_rows), 2)
        summary.net_pnl          = round(sum(r.net_pnl   for r in rt_rows), 2)
        summary.total_commission = round(sum(r.commission for r in rt_rows), 2)
        summary.win_rate         = round(len(winners) / len(rt_rows) * 100, 1) if rt_rows else 0.0
        summary.avg_winner       = round(sum(r.net_pnl for r in winners) / len(winners), 2) if winners else 0.0
        summary.avg_loser        = round(sum(r.net_pnl for r in losers)  / len(losers),  2) if losers  else 0.0
    db.session.commit()
# ---------------------------------------------------------------------------
# Main entry point — called by both file upload and Flex HTTP sync
# ---------------------------------------------------------------------------
def import_flex_xml(content: str) -> dict:
    """
    Full parse → validate → dedup → STG → ODS → RT → DailySummary pipeline.
    Returns a result dict with ok, new_rows, skipped, warnings, errors.
    """
    # Step 1 — Parse
    raw_df, parse_errors = _parse_flex_xml(content)
    if raw_df is None:
        return {
            "ok": False,
            "message": "No valid UnbundledCommissionDetail rows found in XML.",
            "errors": parse_errors, "warnings": [], "skipped": [], "new_rows": 0,
        }
    # Step 2 — Validate
    clean_df, validation_errors = _validate_and_clean(raw_df)
    all_errors = parse_errors + validation_errors
    if clean_df.empty:
        return {
            "ok": False,
            "message": f"No valid rows after validation — {len(all_errors)} error(s).",
            "errors": all_errors, "warnings": [], "skipped": [], "new_rows": 0,
        }
    # Step 3 — Dedup (skip, not abort)
    new_df, skipped = _filter_duplicates(clean_df)
    if new_df.empty:
        return {
            "ok": True,
            "message": f"Nothing new to import — all {len(skipped)} row(s) already in database.",
            "errors": all_errors, "warnings": [], "skipped": skipped, "new_rows": 0,
        }
    # Steps 4-7 — Write
    try:
        affected_pairs = list(new_df[["date", "symbol"]].drop_duplicates().itertuples(index=False, name=None))
        affected_dates = {d for d, _ in affected_pairs}
        rt_warnings    = []
        _write_stg(new_df)
        _recompute_ods(affected_pairs)
        _compute_rt_trades(affected_pairs, rt_warnings)
        _recompute_daily_summary(affected_dates)
        return {
            "ok":       True,
            "new_rows": len(new_df),
            "skipped":  skipped,
            "warnings": all_errors + rt_warnings,
            "errors":   [],
            "message":  None,
        }
    except Exception as e:
        db.session.rollback()
        return {
            "ok": False,
            "message": f"Database error: {e}",
            "errors": [str(e)], "warnings": [], "skipped": skipped, "new_rows": 0,
        }