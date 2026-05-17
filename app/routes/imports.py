from flask import Blueprint, render_template, request
from app.services.tlg_parser import import_file
from app.services.flex_query import sync_flex

imports_bp = Blueprint("imports", __name__)

ALLOWED_EXTENSIONS = {".tlg", ".csv", ".txt"}


@imports_bp.route("/tradelog/import", methods=["GET", "POST"])
def import_page():
    result = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "flex_sync":
            result = sync_flex()

        elif action == "file_upload":
            f = request.files.get("file")
            if not f or not f.filename:
                result = {"ok": False, "message": "No file selected.", "errors": [], "duplicates": [], "warnings": []}
            else:
                # Validate extension
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
