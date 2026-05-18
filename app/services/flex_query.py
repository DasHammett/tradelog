"""
IBKR Flex Query poller — stub.
Will be implemented once STG/ODS pipeline is stable.
Set FLEX_TOKEN and FLEX_QUERY_ID in .env to enable.
"""
from flask import current_app


def sync_flex() -> dict:
    token    = current_app.config.get("FLEX_TOKEN")
    query_id = current_app.config.get("FLEX_QUERY_ID")

    if not token or not query_id:
        return {"ok": False, "message": "FLEX_TOKEN or FLEX_QUERY_ID not configured in .env"}

    return {"ok": False, "message": "Flex Query sync not yet implemented in new pipeline. Use TLG file import instead."}
