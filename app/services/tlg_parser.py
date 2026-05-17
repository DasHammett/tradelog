"""
IBKR file parser — supports:
  - TLG files  (Third-Party TradeLog, pipe-delimited, from Performance & Statements)
  - CSV Activity Statements (Trades section)
Validation and duplicate detection happen BEFORE any DB writes.
If any duplicate is found, the entire import is aborted with a full report.
"""
import csv
import io
from datetime import datetime
from app import db
from app.models import Trade
from app.services.metrics import compute_daily_summary
# ---------------------------------------------------------------------------
# TLG parser
# ---------------------------------------------------------------------------
# TLG pipe-delimited field positions for STK_TRD records:
# SECTION  TYPE  | EXEC_ID | SYMBOL | DESCRIPTION | EXCHANGE | ACTION    | OPEN_CLOSE | DATE     | TIME     | CURRENCY | QTY   | MULTIPLIER | PRICE  | PROCEEDS    | PNL   | COMMISSION
# idx:       0      1         2        3              4          5           6            7          8          9          10      11           12       13            14      15
TLG_FIELDS = {
    "exec_id":    1,
    "symbol":     2,
    "exchange":   4,
    "action":     5,   # BUYTOOPEN, SELLTOCLOSE, SELLTOOPEN, BUYTOCLOSE, etc.
    "open_close": 6,   # O / C
    "date":       7,   # YYYYMMDD
    "time":       8,   # HH:MM:SS
    "currency":   9,
    "quantity":   10,
    "price":      12,
    "proceeds":   13,
    "pnl":        14,
    "commission": 15,
}
VALID_TLG_ACTIONS = {
    "BUYTOOPEN", "BUYTOCLOSE",
    "SELLTOOPEN", "SELLTOCLOSE",
    "BUY", "SELL",
}
def _parse_tlg_records(content: str):
    """
    Parse TLG file content into a list of raw record dicts.
    Returns (records, errors) where errors is a list of strings.
    Actual file format:
      ACCOUNT_INFORMATION          <- section header, no pipe, ignored
      ACT_INF|U123|Name            <- account info row, ignored
      STOCK_TRANSACTIONS           <- section header, start capturing
      STK_TRD|exec_id|symbol|...   <- trade rows, capture these
      CURRENCY_TRANSACTIONS        <- different section, stop capturing
      CASH_TRD|...                 <- ignored
      EOF
    """
    records         = []
    errors          = []
    in_stock_section = False
    for lineno, line in enumerate(content.splitlines(), 1):
        line = line.strip().rstrip("\r")   # handle Windows CRLF and any stray CR
        if not line or line == "EOF":
            continue
        # Lines without a pipe are section headers — use them to track position
        if "|" not in line:
            in_stock_section = (line == "STOCK_TRANSACTIONS")
            continue
        # Skip everything outside STOCK_TRANSACTIONS
        if not in_stock_section:
            continue
        parts       = line.split("|")
        record_type = parts[0].strip()
        # Only process STK_TRD rows; skip STK_DIV, STK_OPT, etc.
        if record_type != "STK_TRD":
            continue
        if len(parts) < 15:
            errors.append(f"Line {lineno}: expected 15+ fields, got {len(parts)} — skipping")
            continue
        try:
            exec_id     = parts[TLG_FIELDS["exec_id"]].strip()
            symbol      = parts[TLG_FIELDS["symbol"]].strip()
            action      = parts[TLG_FIELDS["action"]].strip().upper()
            date_str    = parts[TLG_FIELDS["date"]].strip()
            time_str    = parts[TLG_FIELDS["time"]].strip()
            currency    = parts[TLG_FIELDS["currency"]].strip() or "USD"
            qty_raw     = parts[TLG_FIELDS["quantity"]].strip()
            price_raw   = parts[TLG_FIELDS["price"]].strip()
            pnl_raw     = parts[TLG_FIELDS["pnl"]].strip()
            commish_raw = parts[TLG_FIELDS["commission"]].strip() if len(parts) > TLG_FIELDS["commission"] else ""
            if not symbol:
                errors.append(f"Line {lineno}: missing symbol — skipping")
                continue
            if action not in VALID_TLG_ACTIONS:
                errors.append(f"Line {lineno}: unknown action '{action}' for {symbol} — skipping")
                continue
            entry_time = datetime.strptime(f"{date_str} {time_str}", "%Y%m%d %H:%M:%S")
            qty        = float(qty_raw    or 0)
            price      = float(price_raw  or 0)
            pnl        = float(pnl_raw    or 0)
            commish    = abs(float(commish_raw or 0))
            if price <= 0:
                errors.append(f"Line {lineno}: invalid price '{price_raw}' for {symbol} — skipping")
                continue
            is_buy = action in ("BUYTOOPEN", "BUYTOCLOSE", "BUY")
            side   = "LONG" if is_buy else "SHORT"
            records.append({
                "ibkr_trade_id": f"TLG-{exec_id}-{date_str}-{time_str}",
                "symbol":        symbol,
                "asset_class":   "STK",
                "currency":      currency,
                "side":          side,
                "quantity":      abs(qty),
                "entry_price":   price,
                "entry_time":    entry_time,
                "commission":    commish,
                "gross_pnl":     round(pnl, 2),
                "net_pnl":       round(pnl - commish, 2),
                "is_open":       (pnl == 0),
            })
        except (ValueError, IndexError) as e:
            errors.append(f"Line {lineno}: parse error — {e}")
            continue
    return records, errors
# ---------------------------------------------------------------------------
# CSV Activity Statement parser
# ---------------------------------------------------------------------------
def _parse_csv_records(content: str):
    """
    Parse IBKR CSV Activity Statement (Trades section).
    Returns (records, errors).
    """
    records = []
    errors = []
    reader = csv.reader(io.StringIO(content))
    headers = None
    for lineno, row in enumerate(reader, 1):
        if not row:
            continue
        if row[0] == "Trades" and row[1] == "Header":
            headers = row
            continue
        if not headers:
            continue
        if row[0] != "Trades" or row[1] != "Data" or row[2] != "Order":
            continue
        data = dict(zip(headers, row))
        symbol = data.get("Symbol", "").strip()
        if not symbol:
            continue
        dt_str = data.get("Date/Time", "").strip()
        try:
            entry_time = datetime.strptime(dt_str, "%Y-%m-%d, %H:%M:%S")
        except ValueError:
            try:
                entry_time = datetime.strptime(dt_str, "%Y-%m-%d")
            except ValueError:
                errors.append(f"Row {lineno}: unparseable date '{dt_str}' for {symbol}")
                continue
        try:
            qty      = float(data.get("Quantity", 0) or 0)
            price    = float(data.get("T. Price", 0) or 0)
            commish  = abs(float(data.get("Comm/Fee", 0) or 0))
            realized = float(data.get("Realized P/L", 0) or 0)
        except ValueError as e:
            errors.append(f"Row {lineno}: numeric parse error — {e}")
            continue
        if price <= 0:
            errors.append(f"Row {lineno}: invalid price {price} for {symbol} — skipping")
            continue
        pseudo_id = f"CSV-{symbol}-{dt_str}-{qty}-{price}"
        side      = "LONG" if qty > 0 else "SHORT"
        records.append({
            "ibkr_trade_id": pseudo_id,
            "symbol":        symbol,
            "asset_class":   data.get("Asset Category", "Stocks").split()[0].upper()[:3],
            "currency":      data.get("Currency", "USD"),
            "side":          side,
            "quantity":      abs(qty),
            "entry_price":   price,
            "entry_time":    entry_time,
            "commission":    commish,
            "gross_pnl":     round(realized, 2),
            "net_pnl":       round(realized - commish, 2),
            "is_open":       (realized == 0),
        })
    return records, errors
# ---------------------------------------------------------------------------
# Shared: validate + write
# ---------------------------------------------------------------------------
def _check_duplicates(records):
    """
    Check every record against the DB.
    Returns list of duplicate ibkr_trade_ids found.
    """
    duplicates = []
    for r in records:
        if Trade.query.filter_by(ibkr_trade_id=r["ibkr_trade_id"]).first():
            duplicates.append(r["ibkr_trade_id"])
    return duplicates
def _write_records(records):
    """Write validated, de-duped records to DB. Returns count."""
    affected_dates = set()
    for r in records:
        t = Trade(**r)
        db.session.add(t)
        affected_dates.add(r["entry_time"].date())
    db.session.commit()
    for d in affected_dates:
        compute_daily_summary(d)
    return len(records)
def import_file(content: str, filename: str) -> dict:
    """
    Main entry point. Auto-detects TLG vs CSV, validates, checks duplicates,
    and only writes to DB if everything is clean.
    Returns a result dict with keys:
      ok, new_trades, warnings, duplicates, errors, file_type
    """
    filename_lower = filename.lower()
    # --- Detect file type ---
    if filename_lower.endswith(".tlg"):
        file_type = "TLG"
        records, parse_errors = _parse_tlg_records(content)
    elif filename_lower.endswith(".csv") or filename_lower.endswith(".txt"):
        file_type = "CSV"
        records, parse_errors = _parse_csv_records(content)
    else:
        # Try TLG first (pipe format), fall back to CSV
        # Try TLG first by checking for STK_TRD data rows or section header
        if "STK_TRD|" in content or "STOCK_TRANSACTIONS" in content:
            file_type = "TLG"
            records, parse_errors = _parse_tlg_records(content)
        else:
            file_type = "CSV"
            records, parse_errors = _parse_csv_records(content)
    # --- Validation: must have parsed at least one record ---
    if not records and not parse_errors:
        # Build a diagnostic snippet to help debug
        first_lines = content.splitlines()[:10]
        diag = " | ".join(repr(l) for l in first_lines)
        return {
            "ok": False,
            "file_type": file_type,
            "message": "No valid trade records found in file. "
                       "Make sure this is an IBKR TLG or CSV Activity Statement.",
            "errors": [f"First lines of file: {diag}"],
            "duplicates": [],
            "warnings": [],
        }
    if not records:
        return {
            "ok": False,
            "file_type": file_type,
            "message": f"File could not be parsed — {len(parse_errors)} error(s).",
            "errors": parse_errors,
            "duplicates": [],
            "warnings": [],
        }
    # --- Duplicate check (dry run — no DB writes yet) ---
    duplicates = _check_duplicates(records)
    if duplicates:
        return {
            "ok": False,
            "file_type": file_type,
            "message": f"Import aborted — {len(duplicates)} duplicate trade(s) already in database.",
            "errors": parse_errors,
            "duplicates": duplicates,
            "warnings": [],
            "parsed_count": len(records),
        }
    # --- All good — write to DB ---
    try:
        count = _write_records(records)
        return {
            "ok": True,
            "file_type": file_type,
            "new_trades": count,
            "warnings": parse_errors,   # non-fatal parse warnings
            "errors": [],
            "duplicates": [],
        }
    except Exception as e:
        db.session.rollback()
        return {
            "ok": False,
            "file_type": file_type,
            "message": f"Database error: {e}",
            "errors": [str(e)],
            "duplicates": [],
            "warnings": [],
        }
# Keep old name as alias for the CSV-only path used by Flex importer
def parse_activity_csv(file_content: str) -> dict:
    return import_file(file_content, "upload.csv")
