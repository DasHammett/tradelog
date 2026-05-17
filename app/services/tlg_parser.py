"""
IBKR Activity Statement parser (CSV format).
Handles the Trades section of the default activity statement export.
"""
import csv
import io
from datetime import datetime
from app import db
from app.models import Trade
from app.services.metrics import compute_daily_summary


def parse_activity_csv(file_content: str) -> dict:
    """
    Parse an IBKR activity statement CSV.
    Returns {"ok": True, "new_trades": N} or {"ok": False, "message": ...}
    """
    new_count = 0
    affected_dates = set()

    try:
        reader = csv.reader(io.StringIO(file_content))
        headers = None
        for row in reader:
            if not row:
                continue

            # The Trades section header row
            if row[0] == "Trades" and row[1] == "Header":
                headers = row
                continue

            if headers and row[0] == "Trades" and row[1] == "Data" and row[2] == "Order":
                data = dict(zip(headers, row))
                _process_row(data, affected_dates)
                new_count += 1

        db.session.commit()
        for d in affected_dates:
            compute_daily_summary(d)

        return {"ok": True, "new_trades": new_count}

    except Exception as e:
        db.session.rollback()
        return {"ok": False, "message": str(e)}


def _process_row(data: dict, affected_dates: set):
    symbol = data.get("Symbol", "").strip()
    if not symbol:
        return

    # Parse datetime — IBKR uses "YYYY-MM-DD, HH:MM:SS" in activity statements
    dt_str = data.get("Date/Time", "").strip()
    try:
        entry_time = datetime.strptime(dt_str, "%Y-%m-%d, %H:%M:%S")
    except ValueError:
        try:
            entry_time = datetime.strptime(dt_str, "%Y-%m-%d")
        except ValueError:
            return

    qty      = float(data.get("Quantity", 0) or 0)
    price    = float(data.get("T. Price", 0) or 0)
    commish  = abs(float(data.get("Comm/Fee", 0) or 0))
    realized = float(data.get("Realized P/L", 0) or 0)
    side     = "LONG" if qty > 0 else "SHORT"

    # Use a composite key as a pseudo unique ID for dedup
    pseudo_id = f"{symbol}-{dt_str}-{qty}-{price}"
    if Trade.query.filter_by(ibkr_trade_id=pseudo_id).first():
        return

    t = Trade(
        ibkr_trade_id=pseudo_id,
        symbol=symbol,
        asset_class=data.get("Asset Category", "Stocks").split()[0].upper()[:3],
        currency=data.get("Currency", "USD"),
        side=side,
        quantity=abs(qty),
        entry_price=price,
        entry_time=entry_time,
        commission=commish,
        gross_pnl=round(realized, 2),
        net_pnl=round(realized - commish, 2),
        is_open=(realized == 0),
    )
    db.session.add(t)
    affected_dates.add(entry_time.date())
