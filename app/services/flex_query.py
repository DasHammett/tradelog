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
MAX_POLLS    = 6
POLL_DELAY_S = 10   # seconds between polls — IBKR recommends >= 10s
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
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        return None, f"SendRequest XML parse error: {e}"
    status = root.findtext("Status", "").strip()
    if status == "Success":
        ref = root.findtext("ReferenceCode", "").strip()
        if ref:
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
    for attempt in range(1, MAX_POLLS + 1):
        if attempt > 1:
            time.sleep(POLL_DELAY_S)
        try:
            resp = requests.get(
                GET_URL,
                params={"t": token, "q": ref_code, "v": "3"},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            return None, f"GetStatement network error (attempt {attempt}): {e}"
        # If the response starts with XML and contains FlexQueryResponse it's done
        text = resp.text.strip()
        if "<FlexQueryResponse" in text:
            return text, None
        # Otherwise IBKR returns a short XML status message — check it
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            # Not valid XML at all — unexpected response
            return None, f"GetStatement unexpected response (attempt {attempt}): {text[:200]}"
        status    = root.findtext("Status", "").strip()
        error_msg = root.findtext("ErrorMessage", "").strip()
        if status == "Statement generation in progress":
            continue   # normal — keep polling
        if error_msg:
            return None, f"GetStatement error [{status}]: {error_msg}"
        # Any other status — give up
        return None, f"GetStatement unexpected status '{status}' (attempt {attempt})"
    return None, (
        f"GetStatement timed out after {MAX_POLLS} attempts "
        f"({MAX_POLLS * POLL_DELAY_S}s). Try Sync Now again in a moment."
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
