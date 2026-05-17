from flask import Blueprint, render_template, request
from app.services.metrics import get_dashboard_metrics

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/tradelog/")
@dashboard_bp.route("/tradelog")
def index():
    days = int(request.args.get("days", 30))
    metrics = get_dashboard_metrics(days=days)
    return render_template("dashboard.html", metrics=metrics, days=days)
