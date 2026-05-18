from flask import Blueprint, render_template, request
from app.services.tlg_parser import import_file, _recompute_ods, _recompute_daily_summary, _compute_rt_trades
from app.services.flex_query import sync_flex
from app.models import StgExecution
from app import db

imports_bp = Blueprint("imports", __name__)

ALLOWED_EXTENSIONS = {".tlg", ".csv", ".txt"}


@imports_bp.route("/tradelog/import", methods=["GET", "POST"])
def import_page():
    result = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "flex_sync":
            result = sync_flex()

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
                    "warnings": rt_warnings, "errors": [], "duplicates": [],
                }
            except Exception as e:
                db.session.rollback()
                result = {"ok": False, "message": f"Recompute failed: {e}",
                          "errors": [str(e)], "duplicates": [], "warnings": []}

        elif action == "file_upload":
            f = request.files.get("file")
            if not f or not f.filename:
                result = {"ok": False, "message": "No file selected.", "errors": [], "duplicates": [], "warnings": []}
            else:
                filename = f.filename
                ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                if ext not in ALLOWED_EXTENSIONS:
                    result = {
                        "ok": False,
                        "message": f"Unsupported file type '{ext}'. Please upload a .tlg or .csv file.",
                        "errors": [], "duplicates": [], "warnings": [],
                    }
                else:
                    content = f.read().decode("utf-8", errors="ignore")
                    if not content.strip():
                        result = {"ok": False, "message": "File is empty.", "errors": [], "duplicates": [], "warnings": []}
                    else:
                        result = import_file(content, filename)

    return render_template("imports.html", result=result)
