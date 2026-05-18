"""
TLG Import Pipeline
===================
Step 1 — Parse:     Read TLG file into a Pandas DataFrame (STG shape)
Step 2 — Validate:  Check field integrity before touching the DB
Step 3 — Dedup:     Check exec_ids against stg_executions — abort if any duplicate
Step 4 — Write STG: Insert new raw execution rows
Step 5 — Write ODS: Recompute ods_daily_symbol for affected date+symbol combos
Step 6 — Compute RT: FIFO avg-cost matching → rt_trades
Step 7 — Write DWH: Recompute daily_summary from rt_trades
"""

import io
import pandas as pd
from datetime import datetime, date
from app import db
from app.models import StgExecution, OdsDailySymbol, DailySummary, RtTrade

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_MAP = {
    "BUYTOOPEN":   "BUY",
    "BUYTOCLOSE":  "BUY",
    "BUY":         "BUY",
    "SELLTOCLOSE": "SELL",
    "SELLTOOPEN":  "SELL",
    "SELL":        "SELL",
}

# Field positions in a STK_TRD pipe-delimited row
# STK_TRD | exec_id | symbol | description | exchange | action | open_close | date | time | currency | qty | multiplier | price | proceeds | pnl | commission
#    0         1         2          3             4         5          6          7      8       9        10      11          12       13        14      15
COL_NAMES = [
    "record_type", "exec_id", "symbol", "description", "exchange",
    "action_raw", "open_close", "date_str", "time_str", "currency",
    "quantity", "multiplier", "price", "proceeds", "commission", "extra"
]


# ---------------------------------------------------------------------------
# Step 1 — Parse TLG into DataFrame
# ---------------------------------------------------------------------------

def _parse_tlg_to_df(content: str):
    """
    Read only STK_TRD rows from the STOCK_TRANSACTIONS section.
    Returns a raw DataFrame with COL_NAMES columns plus 'raw_line'.
    """
    rows      = []
    raw_lines = []
    in_stock  = False

    for line in content.splitlines():
        line = line.strip().rstrip("\r")
        if not line or line == "EOF":
            continue

        if "|" not in line:
            in_stock = (line == "STOCK_TRANSACTIONS")
            continue

        if not in_stock:
            continue

        parts = line.split("|")
        if parts[0].strip() != "STK_TRD":
            continue

        # Pad to expected width so zip always works
        while len(parts) < len(COL_NAMES):
            parts.append("")

        rows.append(parts[:len(COL_NAMES)])
        raw_lines.append(line)

    if not rows:
        return None, ["No STK_TRD rows found in STOCK_TRANSACTIONS section"]

    df = pd.DataFrame(rows, columns=COL_NAMES)
    df["raw_line"] = raw_lines
    return df, []


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

    # Normalise strings
    for col in ["exec_id", "symbol", "action_raw", "currency"]:
        df[col] = df[col].str.strip()

    # Filter unknown actions early
    df["action_raw"] = df["action_raw"].str.upper()
    unknown = df[~df["action_raw"].isin(ACTION_MAP)]
    if not unknown.empty:
        for _, row in unknown.iterrows():
            errors.append(f"Unknown action '{row.action_raw}' for {row.symbol} — skipped")
    df = df[df["action_raw"].isin(ACTION_MAP)].copy()

    # Map to BUY / SELL
    df["side"] = df["action_raw"].map(ACTION_MAP)

    # Parse datetime
    def parse_dt(row):
        try:
            return datetime.strptime(f"{row.date_str.strip()} {row.time_str.strip()}", "%Y%m%d %H:%M:%S")
        except ValueError:
            return pd.NaT

    df["datetime"] = df.apply(parse_dt, axis=1)
    bad_dt = df["datetime"].isna()
    if bad_dt.any():
        for _, row in df[bad_dt].iterrows():
            errors.append(f"Bad date/time '{row.date_str} {row.time_str}' for {row.symbol} — skipped")
    df = df[~bad_dt].copy()

    df["date"] = df["datetime"].dt.date
    df["time"] = df["datetime"].dt.time

    # Numeric columns
    for col in ["quantity", "price", "commission"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    bad_num = df[["quantity", "price"]].isna().any(axis=1)
    if bad_num.any():
        for _, row in df[bad_num].iterrows():
            errors.append(f"Non-numeric qty/price for {row.symbol} at {row.date_str} — skipped")
    df = df[~bad_num].copy()

    # qty is negative for sells in TLG; commission is negative (cost) — take abs of both
    df["quantity"]   = df["quantity"].abs()
    df["commission"] = df["commission"].abs().fillna(0.0)

    # Drop zero or negative prices
    bad_price = df["price"] <= 0
    if bad_price.any():
        for _, row in df[bad_price].iterrows():
            errors.append(f"Invalid price {row.price} for {row.symbol} — skipped")
    df = df[~bad_price].copy()

    # Drop missing symbols
    df = df[df["symbol"].str.len() > 0].copy()

    # Build unique exec_id: TLG-{exec_id}-{date}-{time}
    df["exec_id"] = "TLG-" + df["exec_id"] + "-" + df["date_str"].str.strip() + "-" + df["time_str"].str.strip()

    # Keep only needed columns
    df = df[["exec_id", "symbol", "date", "time", "action_raw", "side",
             "quantity", "price", "commission", "currency", "raw_line"]].copy()
    return df, errors


# ---------------------------------------------------------------------------
# Step 3 — Duplicate check
# ---------------------------------------------------------------------------

def _check_duplicates(df: pd.DataFrame):
    """
    Query existing exec_ids from STG and return any that already exist.
    """
    incoming_ids = df["exec_id"].tolist()
    existing = db.session.query(StgExecution.exec_id)\
                 .filter(StgExecution.exec_id.in_(incoming_ids))\
                 .all()
    return [row.exec_id for row in existing]


# ---------------------------------------------------------------------------
# Step 4 — Write STG
# ---------------------------------------------------------------------------

def _write_stg(df: pd.DataFrame):
    for _, row in df.iterrows():
        ex = StgExecution(
            exec_id    = row.exec_id,
            symbol     = row.symbol,
            date       = row.date,
            time       = row.time,
            action_raw = row.action_raw,
            side       = row.side,
            quantity   = float(row.quantity),
            price      = float(row.price),
            commission = float(row.commission),
            currency   = row.currency or "USD",
            raw_line   = row.raw_line,
        )
        db.session.add(ex)
    db.session.commit()


# ---------------------------------------------------------------------------
# Step 5 — Recompute ODS for affected date+symbol combos
# ---------------------------------------------------------------------------

def _recompute_ods(affected: list):
    """
    affected: list of (date, symbol) tuples.
    Pulls all STG rows for each combo and recomputes ODS.
    """
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

        # Weighted average prices
        avg_buy  = (buys["price"]  * buys["quantity"]).sum()  / bought_qty if bought_qty > 0 else 0.0
        avg_sell = (sells["price"] * sells["quantity"]).sum() / sold_qty   if sold_qty   > 0 else 0.0

        total_commission = df["commission"].sum()

        # P&L: proceeds from sells minus cost of buys minus commissions
        gross_pnl = round((avg_sell * sold_qty) - (avg_buy * bought_qty), 4)
        net_pnl   = round(gross_pnl - total_commission, 4)

        # Upsert ODS row
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
# Step 6 — Recompute DailySummary from ODS
# ---------------------------------------------------------------------------

def _recompute_daily_summary(affected_dates: set):
    for trade_date in affected_dates:
        ods_rows = OdsDailySymbol.query.filter_by(date=trade_date).all()
        if not ods_rows:
            continue

        winners = [r for r in ods_rows if r.net_pnl > 0]
        losers  = [r for r in ods_rows if r.net_pnl < 0]

        summary = DailySummary.query.filter_by(date=trade_date).first()
        if not summary:
            summary = DailySummary(date=trade_date)
            db.session.add(summary)

        summary.total_symbols   = len(ods_rows)
        summary.winning_symbols = len(winners)
        summary.losing_symbols  = len(losers)
        summary.gross_pnl       = round(sum(r.gross_pnl for r in ods_rows), 2)
        summary.net_pnl         = round(sum(r.net_pnl   for r in ods_rows), 2)
        summary.total_commission= round(sum(r.total_commission for r in ods_rows), 2)
        summary.win_rate        = round(len(winners) / len(ods_rows) * 100, 1)
        summary.avg_winner      = round(sum(r.net_pnl for r in winners) / len(winners), 2) if winners else 0.0
        summary.avg_loser       = round(sum(r.net_pnl for r in losers)  / len(losers),  2) if losers  else 0.0

    db.session.commit()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def import_file(content: str, filename: str) -> dict:
    """
    Full STG → ODS → DailySummary pipeline.
    Returns a result dict with ok, new_rows, warnings, duplicates, errors.
    """
    filename_lower = filename.lower()

    # Only TLG supported for now; CSV path can be added later
    if filename_lower.endswith(".csv"):
        return {"ok": False, "message": "CSV import not yet supported in new pipeline.",
                "errors": [], "duplicates": [], "warnings": []}

    # Step 1 — Parse
    raw_df, parse_errors = _parse_tlg_to_df(content)
    if raw_df is None:
        return {"ok": False, "message": "No valid STK_TRD rows found.",
                "errors": parse_errors, "duplicates": [], "warnings": []}

    # Step 2 — Validate
    clean_df, validation_errors = _validate_and_clean(raw_df)
    all_errors = parse_errors + validation_errors

    if clean_df.empty:
        return {"ok": False, "message": f"No valid rows after validation — {len(all_errors)} error(s).",
                "errors": all_errors, "duplicates": [], "warnings": []}

    # Step 3 — Dedup
    duplicates = _check_duplicates(clean_df)
    if duplicates:
        return {
            "ok":           False,
            "message":      f"Import aborted — {len(duplicates)} duplicate execution(s) already in STG.",
            "errors":       all_errors,
            "duplicates":   duplicates,
            "warnings":     [],
            "parsed_count": len(clean_df),
        }

    # Steps 4-6 — Write
    try:
        affected_pairs = list(clean_df[["date", "symbol"]].drop_duplicates().itertuples(index=False, name=None))
        affected_dates = {d for d, _ in affected_pairs}

        _write_stg(clean_df)
        _recompute_ods(affected_pairs)
        _recompute_daily_summary(affected_dates)

        return {
            "ok":        True,
            "new_rows":  len(clean_df),
            "warnings":  all_errors,
            "errors":    [],
            "duplicates":[],
        }

    except Exception as e:
        db.session.rollback()
        return {"ok": False, "message": f"Database error: {e}",
                "errors": [str(e)], "duplicates": [], "warnings": []}


# Alias used by flex_query.py
def parse_activity_csv(file_content: str) -> dict:
    return import_file(file_content, "upload.tlg")