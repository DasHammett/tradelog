"""
IBKR Flex Query poller.
Set FLEX_TOKEN and FLEX_QUERY_ID in .env to enable auto-sync.
Flex Query in IBKR portal must be configured to export:
  Trades, Executions — in XML format, Last N Days.
"""
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from flask import current_app
from app import db
from app.models import Trade, Execution
from app.services.metrics import compute_daily_summary


FLEX_URL   = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
FLEX_FETCH = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"


def _request_statement(token: str, query_id: str) -> str | None:
    """Step 1: request the report — returns a reference code."""
    resp = requests.get(FLEX_URL, params={"t": token, "q": query_id, "v": 3}, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    status = root.findtext("Status")
    if status != "Success":
        current_app.logger.error(f"Flex request failed: {root.findtext('ErrorMessage')}")
        return None
    return root.findtext("ReferenceCode")


def _fetch_statement(token: str, ref_code: str) -> str | None:
    """Step 2: download the report XML using the reference code."""
    import time
    for attempt in range(5):
        time.sleep(3)
        resp = requests.get(FLEX_FETCH, params={"t": token, "q": ref_code, "v": 3}, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        status = root.findtext("Status")
        if status == "Success":
            return resp.text
        if status == "Warn":
            continue  # still generating
        current_app.logger.error(f"Flex fetch error: {root.findtext('ErrorMessage')}")
        return None
    return None


def _parse_and_upsert(xml_text: str) -> int:
    """Parse Flex XML and upsert trades. Returns count of new trades."""
    root = ET.fromstring(xml_text)
    new_count = 0
    affected_dates = set()

    for trade_el in root.iter("Trade"):
        ibkr_id = trade_el.get("tradeID") or trade_el.get("execID")
        if not ibkr_id:
            continue

        # Skip if already imported
        if Trade.query.filter_by(ibkr_trade_id=ibkr_id).first():
            continue

        try:
            entry_time = datetime.strptime(
                trade_el.get("dateTime", ""), "%Y%m%d;%H%M%S"
            )
        except ValueError:
            continue

        symbol   = trade_el.get("symbol", "")
        side     = "LONG" if float(trade_el.get("quantity", 0)) > 0 else "SHORT"
        qty      = abs(float(trade_el.get("quantity", 0)))
        price    = float(trade_el.get("tradePrice", 0))
        commish  = abs(float(trade_el.get("ibCommission", 0)))
        proceeds = float(trade_el.get("proceeds", 0))
        basis    = float(trade_el.get("cost", 0))
        gross    = float(trade_el.get("fifoPnlRealized", 0))
        net      = gross - commish

        t = Trade(
            ibkr_trade_id=ibkr_id,
            symbol=symbol,
            asset_class=trade_el.get("assetCategory", "STK"),
            currency=trade_el.get("currency", "USD"),
            side=side,
            quantity=qty,
            entry_price=price,
            entry_time=entry_time,
            commission=commish,
            gross_pnl=round(gross, 2),
            net_pnl=round(net, 2),
            is_open=(gross == 0 and proceeds == 0),
        )
        db.session.add(t)
        affected_dates.add(entry_time.date())
        new_count += 1

    db.session.commit()

    for d in affected_dates:
        compute_daily_summary(d)

    return new_count


def sync_flex() -> dict:
    token    = current_app.config.get("FLEX_TOKEN")
    query_id = current_app.config.get("FLEX_QUERY_ID")

    if not token or not query_id:
        return {"ok": False, "message": "FLEX_TOKEN or FLEX_QUERY_ID not configured"}

    try:
        ref = _request_statement(token, query_id)
        if not ref:
            return {"ok": False, "message": "Could not get reference code from IBKR"}

        xml_text = _fetch_statement(token, ref)
        if not xml_text:
            return {"ok": False, "message": "Could not fetch statement from IBKR"}

        count = _parse_and_upsert(xml_text)
        return {"ok": True, "new_trades": count}

    except Exception as e:
        current_app.logger.error(f"Flex sync error: {e}")
        return {"ok": False, "message": str(e)}
