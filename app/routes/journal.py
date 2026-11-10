from flask import Blueprint, render_template, request, redirect, url_for
from app.models import JournalEntry, DailySummary
from app import db
from datetime import date, datetime

journal_bp = Blueprint("journal", __name__)


PAGE_SIZE = 30


@journal_bp.route("/tradelog/journal")
def journal():
    total = JournalEntry.query.count()
    pages = max(1, -(-total // PAGE_SIZE))                  # ceil division
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    page = min(max(page, 1), pages)
    entries = (JournalEntry.query
               .order_by(JournalEntry.date.desc())
               .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all())
    return render_template("journal.html", entries=entries, page=page, pages=pages,
                           total=total, today=date.today().isoformat())


@journal_bp.route("/tradelog/journal/go")
def go_to_date():
    """Date picker target: open (or start) the entry for the chosen day. Future days are not allowed."""
    raw = request.args.get("date", "")
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("journal.journal"))
    if d > date.today():
        return redirect(url_for("journal.journal"))
    return redirect(url_for("journal.journal_entry", entry_date=d.isoformat()))


@journal_bp.route("/tradelog/journal/<string:entry_date>", methods=["GET", "POST"])
def journal_entry(entry_date):
    try:
        d = datetime.strptime(entry_date, "%Y-%m-%d").date()
    except ValueError:
        return "Invalid date", 400
    if d > date.today():
        # Applies to GET and POST, so a typed URL cannot create a future entry either
        return "Journal entries cannot be created for future dates.", 400

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
