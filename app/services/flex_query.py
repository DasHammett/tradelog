"""
IBKR Flex Query sync
====================
Two-step HTTP flow:
  1. SendRequest  → returns a ReferenceCode (or error)
  2. GetStatement → poll until the statement is ready, then return XML
Both steps hit the IBKR Flex Web Service endpoints.
Requires FLEX_TOKEN and FLEX_QUERY_ID in .env.
"""
import time
import requests
import xml.etree.ElementTree as ET
from flask import current_app
from app.services.pipeline import import_flex_xml
SEND_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
GET_URL  = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
MAX_POLLS       = 10
INITIAL_DELAY_S = 5    # wait before first GetStatement poll
POLL_DELAY_S    = 10   # seconds between subsequent polls
def _send_request(token: str, query_id: str) -> tuple[str | None, str | None]:
    """
    Step 1: ask IBKR to generate the statement.
    Returns (reference_code, error_message).
    """
    try:
        resp = requests.get(
            SEND_URL,
            params={"t": token, "q": query_id, "v": "3"},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return None, f"SendRequest network error: {e}"
    raw = resp.text.strip()
    current_app.logger.info(f"Flex SendRequest raw response: {raw[:500]}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return None, f"SendRequest XML parse error: {e} — raw: {raw[:200]}"
    status = root.findtext("Status", "").strip()
    if status == "Success":
        ref = root.findtext("ReferenceCode", "").strip()
        if ref:
            current_app.logger.info(f"Flex SendRequest success, ReferenceCode: {ref}")
            return ref, None
        return None, "SendRequest succeeded but no ReferenceCode returned"
    error_code = root.findtext("ErrorCode", "").strip()
    error_msg  = root.findtext("ErrorMessage", "unknown error").strip()
    return None, f"SendRequest failed [{error_code}]: {error_msg}"
def _get_statement(token: str, ref_code: str) -> tuple[str | None, str | None]:
    """
    Step 2: poll until the statement XML is ready.
    Returns (xml_content, error_message).
    """
    # Wait before first poll — IBKR needs a moment to start generating
    time.sleep(INITIAL_DELAY_S)
    for attempt in range(1, MAX_POLLS + 1):
        if attempt > 1:
            time.sleep(POLL_DELAY_S)
        current_app.logger.info(f"Flex GetStatement attempt {attempt}/{MAX_POLLS}")
        try:
            resp = requests.get(
                GET_URL,
                params={"t": token, "q": ref_code, "v": "3"},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            return None, f"GetStatement network error (attempt {attempt}): {e}"
        text = resp.text.strip()
        # Full statement returned
        if "<FlexQueryResponse" in text:
            current_app.logger.info(f"Flex GetStatement succeeded on attempt {attempt}")
            return text, None
        # Short status XML — parse it
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None, f"GetStatement unexpected response (attempt {attempt}): {text[:200]}"
        status    = root.findtext("Status", "").strip()
        error_msg = root.findtext("ErrorMessage", "").strip()
        current_app.logger.info(f"Flex GetStatement status: '{status}' error: '{error_msg}'")
        if status == "Statement generation in progress":
            continue
        if error_msg:
            return None, f"GetStatement error [{status}]: {error_msg}"
        return None, f"GetStatement unexpected status '{status}' (attempt {attempt})"
    return None, (
        f"GetStatement timed out after {MAX_POLLS} attempts "
        f"({INITIAL_DELAY_S + (MAX_POLLS - 1) * POLL_DELAY_S}s). Try Sync Now again."
    )
def sync_flex() -> dict:
    """
    Run the full Flex sync: HTTP fetch → parse → pipeline.
    Returns the same result dict shape as import_flex_xml.
    """
    token    = current_app.config.get("FLEX_TOKEN",    "")
    query_id = current_app.config.get("FLEX_QUERY_ID", "")
    if not token or not query_id:
        return {
            "ok": False,
            "message": "FLEX_TOKEN or FLEX_QUERY_ID not configured in .env",
            "errors": [], "warnings": [], "skipped": [], "new_rows": 0,
        }
    current_app.logger.info(f"Flex sync starting — query_id: {query_id}")
    # Step 1
    ref_code, err = _send_request(token, query_id)
    if err:
        return {"ok": False, "message": err,
                "errors": [err], "warnings": [], "skipped": [], "new_rows": 0}
    # Step 2
    xml_content, err = _get_statement(token, ref_code)
    if err:
        return {"ok": False, "message": err,
                "errors": [err], "warnings": [], "skipped": [], "new_rows": 0}
    # Step 3 — hand off to the shared pipeline
    return import_flex_xml(xml_content)
