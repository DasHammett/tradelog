from flask import Blueprint, render_template, request
from app.services.pipeline import import_flex_xml, _recompute_ods, _recompute_daily_summary, _compute_rt_trades
from app.services.flex_query import sync_flex
from app.models import StgExecution, OdsDailySymbol, RtTrade, DailySummary, JournalEntry
from app import db
imports_bp = Blueprint("imports", __name__)
ALLOWED_EXTENSIONS = {".xml"}
@imports_bp.route("/tradelog/import", methods=["GET", "POST"])
def import_page():
    result = None
    if request.method == "POST":
        action = request.form.get("action")
        # ── Flex HTTP sync ──────────────────────────────────────────────────
        if action == "flex_sync":
            result = sync_flex()
        # ── XML file upload ─────────────────────────────────────────────────
        elif action == "file_upload":
            f = request.files.get("file")
            if not f or not f.filename:
                result = {"ok": False, "message": "No file selected.",
                          "errors": [], "warnings": [], "skipped": [], "new_rows": 0}
            else:
                ext = ("." + f.filename.rsplit(".", 1)[-1].lower()) if "." in f.filename else ""
                if ext not in ALLOWED_EXTENSIONS:
                    result = {
                        "ok": False,
                        "message": f"Unsupported file type '{ext}'. Please upload a Flex XML file (.xml).",
                        "errors": [], "warnings": [], "skipped": [], "new_rows": 0,
                    }
                else:
                    content = f.read().decode("utf-8", errors="ignore")
                    if not content.strip():
                        result = {"ok": False, "message": "File is empty.",
                                  "errors": [], "warnings": [], "skipped": [], "new_rows": 0}
                    else:
                        result = import_flex_xml(content)
        # ── Recompute ODS ───────────────────────────────────────────────────
        elif action == "recompute_ods":
            try:
                pairs = db.session.query(StgExecution.date, StgExecution.symbol)\
                                  .distinct().all()
                affected_dates = {d for d, _ in pairs}
                rt_warnings    = []
                _recompute_ods(pairs)
                _compute_rt_trades(pairs, rt_warnings)
                _recompute_daily_summary(affected_dates)
                result = {
                    "ok":      True,
                    "message": f"ODS + RT trades recomputed for {len(pairs)} date+symbol combination(s)."
                               + (f" {len(rt_warnings)} warning(s)." if rt_warnings else ""),
                    "warnings": rt_warnings, "errors": [], "skipped": [], "new_rows": 0,
                }
            except Exception as e:
                db.session.rollback()
                result = {"ok": False, "message": f"Recompute failed: {e}",
                          "errors": [str(e)], "warnings": [], "skipped": [], "new_rows": 0}
        # ── Reset database ──────────────────────────────────────────────────
        elif action == "reset_db":
            confirm = request.form.get("confirm_reset", "").strip()
            if confirm != "RESET":
                result = {"ok": False,
                          "message": "Reset cancelled — type RESET in the confirmation box to proceed.",
                          "errors": [], "warnings": [], "skipped": [], "new_rows": 0}
            else:
                try:
                    RtTrade.query.delete()
                    OdsDailySymbol.query.delete()
                    DailySummary.query.delete()
                    JournalEntry.query.delete()
                    StgExecution.query.delete()
                    db.session.commit()
                    result = {"ok": True,
                              "message": "All data has been deleted. Database is empty — ready for fresh import.",
                              "errors": [], "warnings": [], "skipped": [], "new_rows": 0}
                except Exception as e:
                    db.session.rollback()
                    result = {"ok": False, "message": f"Reset failed: {e}",
                              "errors": [str(e)], "warnings": [], "skipped": [], "new_rows": 0}
    return render_template("imports.html", result=result)
