from flask import Blueprint, render_template, request, redirect, url_for, flash
from app.services.tlg_parser import parse_activity_csv
from app.services.flex_query import sync_flex

imports_bp = Blueprint("imports", __name__)


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
                result = {"ok": False, "message": "No file selected"}
            else:
                content = f.read().decode("utf-8", errors="ignore")
                result = parse_activity_csv(content)

    return render_template("imports.html", result=result)
