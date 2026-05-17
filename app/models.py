from datetime import datetime
from app import db


class Trade(db.Model):
    __tablename__ = "trades"

    id = db.Column(db.Integer, primary_key=True)
    ibkr_trade_id = db.Column(db.String(64), unique=True, nullable=True)  # from Flex
    symbol = db.Column(db.String(20), nullable=False)
    asset_class = db.Column(db.String(20), default="STK")  # STK, OPT, FUT, FX
    currency = db.Column(db.String(10), default="USD")
    side = db.Column(db.String(10), nullable=False)   # LONG / SHORT
    quantity = db.Column(db.Float, nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    exit_price = db.Column(db.Float, nullable=True)
    entry_time = db.Column(db.DateTime, nullable=False)
    exit_time = db.Column(db.DateTime, nullable=True)
    commission = db.Column(db.Float, default=0.0)
    gross_pnl = db.Column(db.Float, nullable=True)
    net_pnl = db.Column(db.Float, nullable=True)
    is_open = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tags = db.relationship("TradeTag", back_populates="trade", cascade="all, delete-orphan")
    executions = db.relationship("Execution", back_populates="trade", cascade="all, delete-orphan")

    @property
    def duration_minutes(self):
        if self.entry_time and self.exit_time:
            delta = self.exit_time - self.entry_time
            return round(delta.total_seconds() / 60, 1)
        return None

    def __repr__(self):
        return f"<Trade {self.symbol} {self.side} {self.entry_time}>"


class Execution(db.Model):
    """Individual fills that make up a trade."""
    __tablename__ = "executions"

    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.Integer, db.ForeignKey("trades.id"), nullable=False)
    ibkr_exec_id = db.Column(db.String(64), nullable=True)
    side = db.Column(db.String(10), nullable=False)   # BUY / SELL
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    commission = db.Column(db.Float, default=0.0)
    executed_at = db.Column(db.DateTime, nullable=False)

    trade = db.relationship("Trade", back_populates="executions")


class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    color = db.Column(db.String(10), default="#6b7280")  # tailwind gray

    trades = db.relationship("TradeTag", back_populates="tag")


class TradeTag(db.Model):
    __tablename__ = "trade_tags"

    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.Integer, db.ForeignKey("trades.id"), nullable=False)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), nullable=False)

    trade = db.relationship("Trade", back_populates="tags")
    tag = db.relationship("Tag", back_populates="trades")


class JournalEntry(db.Model):
    __tablename__ = "journal_entries"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True)
    body = db.Column(db.Text, nullable=True)          # Markdown
    mood = db.Column(db.String(20), nullable=True)    # great/good/neutral/bad/terrible
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DailySummary(db.Model):
    """Pre-computed daily stats for fast dashboard rendering."""
    __tablename__ = "daily_summary"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True)
    total_trades = db.Column(db.Integer, default=0)
    winning_trades = db.Column(db.Integer, default=0)
    losing_trades = db.Column(db.Integer, default=0)
    gross_pnl = db.Column(db.Float, default=0.0)
    net_pnl = db.Column(db.Float, default=0.0)
    total_commission = db.Column(db.Float, default=0.0)
    win_rate = db.Column(db.Float, default=0.0)       # 0-100
    avg_winner = db.Column(db.Float, default=0.0)
    avg_loser = db.Column(db.Float, default=0.0)
