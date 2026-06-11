from flask import Blueprint, render_template, request
from app.services.metrics import get_dashboard_metrics

dashboard_bp = Blueprint("dashboard", __name__)

VALID_PERIODS = ("wtd", "mtd", "ytd", "all")


@dashboard_bp.route("/tradelog/")
@dashboard_bp.route("/tradelog")
def index():
    period = request.args.get("period", "mtd")
    if period not in VALID_PERIODS:
        period = "mtd"
    metrics = get_dashboard_metrics(period=period)
    return render_template("dashboard.html", metrics=metrics, period=period)