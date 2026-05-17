from flask import Blueprint, render_template, request, redirect, url_for
from app.models import JournalEntry, DailySummary
from app import db
from datetime import date, datetime

journal_bp = Blueprint("journal", __name__)


@journal_bp.route("/tradelog/journal")
def journal():
    entries = JournalEntry.query.order_by(JournalEntry.date.desc()).limit(60).all()
    return render_template("journal.html", entries=entries)


@journal_bp.route("/tradelog/journal/<string:entry_date>", methods=["GET", "POST"])
def journal_entry(entry_date):
    try:
        d = datetime.strptime(entry_date, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date", 400

    entry = JournalEntry.query.filter_by(date=d).first()
    summary = DailySummary.query.filter_by(date=d).first()

    if request.method == "POST":
        body = request.form.get("body", "")
        mood = request.form.get("mood", "")
        if not entry:
            entry = JournalEntry(date=d)
            db.session.add(entry)
        entry.body = body
        entry.mood = mood
        entry.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for("journal.journal_entry", entry_date=entry_date))

    return render_template("journal_entry.html", entry=entry, summary=summary,
                           entry_date=d)


@journal_bp.route("/tradelog/journal/new")
def new_entry():
    today = date.today().isoformat()
    return redirect(url_for("journal.journal_entry", entry_date=today))
