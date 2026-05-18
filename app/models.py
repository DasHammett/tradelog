from datetime import datetime
from app import db


# ---------------------------------------------------------------------------
# STG — Raw executions from TLG file, one row per execution, no transformation
# ---------------------------------------------------------------------------

class StgExecution(db.Model):
    __tablename__ = "stg_executions"

    id          = db.Column(db.Integer, primary_key=True)
    exec_id     = db.Column(db.String(64), unique=True, nullable=False)  # dedup key
    symbol      = db.Column(db.String(20), nullable=False)
    date        = db.Column(db.Date, nullable=False)
    time        = db.Column(db.Time, nullable=False)
    action_raw  = db.Column(db.String(20), nullable=False)   # BUYTOOPEN / SELLTOCLOSE
    side        = db.Column(db.String(4),  nullable=False)   # BUY / SELL
    quantity    = db.Column(db.Float, nullable=False)
    price       = db.Column(db.Float, nullable=False)
    commission  = db.Column(db.Float, default=0.0)
    currency    = db.Column(db.String(10), default="USD")
    raw_line    = db.Column(db.Text, nullable=True)          # original TLG line
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<StgExecution {self.symbol} {self.side} {self.date} {self.time}>"


# ---------------------------------------------------------------------------
# RT_TRADES — Round-trip trades, one row per SELL execution (FIFO avg cost)
# Computed from STG. Join to ODS/STG on date + symbol.
# ---------------------------------------------------------------------------

class RtTrade(db.Model):
    __tablename__ = "rt_trades"

    id           = db.Column(db.Integer, primary_key=True)
    date         = db.Column(db.Date,     nullable=False, index=True)
    symbol       = db.Column(db.String(20), nullable=False, index=True)
    entry_time   = db.Column(db.DateTime, nullable=True)   # time of first BUY that opened this position lot
    exit_time    = db.Column(db.DateTime, nullable=False)  # time of this SELL
    quantity     = db.Column(db.Float,    nullable=False)  # shares in this round-trip
    entry_price  = db.Column(db.Float,    nullable=False)  # weighted avg cost at time of sell
    exit_price   = db.Column(db.Float,    nullable=False)  # sell price
    commission   = db.Column(db.Float,    default=0.0)     # sum of buy + sell commissions for this rt
    gross_pnl    = db.Column(db.Float,    nullable=False)  # (exit - entry) * qty
    net_pnl      = db.Column(db.Float,    nullable=False)  # gross_pnl - commission
    is_open      = db.Column(db.Boolean,  default=False)   # True if BUY with no matching SELL yet

    __table_args__ = (
        db.UniqueConstraint("date", "symbol", "exit_time", name="uq_rt_date_symbol_exit"),
    )

    @property
    def duration_minutes(self):
        if self.entry_time and self.exit_time:
            return round((self.exit_time - self.entry_time).total_seconds() / 60, 1)
        return None

    def __repr__(self):
        return f"<RtTrade {self.symbol} {self.date} net={self.net_pnl}>"


# ---------------------------------------------------------------------------
# ODS — Daily aggregated per symbol, computed from STG via Pandas
# ---------------------------------------------------------------------------

class OdsDailySymbol(db.Model):
    __tablename__ = "ods_daily_symbol"

    id               = db.Column(db.Integer, primary_key=True)
    date             = db.Column(db.Date,    nullable=False)
    symbol           = db.Column(db.String(20), nullable=False)
    bought_qty       = db.Column(db.Float, default=0.0)
    sold_qty         = db.Column(db.Float, default=0.0)
    avg_buy_price    = db.Column(db.Float, default=0.0)   # weighted average
    avg_sell_price   = db.Column(db.Float, default=0.0)   # weighted average
    total_commission = db.Column(db.Float, default=0.0)
    gross_pnl        = db.Column(db.Float, default=0.0)
    net_pnl          = db.Column(db.Float, default=0.0)

    __table_args__ = (
        db.UniqueConstraint("date", "symbol", name="uq_ods_date_symbol"),
    )

    @property
    def is_winner(self):
        return self.net_pnl > 0

    def __repr__(self):
        return f"<OdsDailySymbol {self.symbol} {self.date} net={self.net_pnl}>"


# ---------------------------------------------------------------------------
# DailySummary — Aggregated per day, computed from rt_trades, drives dashboard
# ---------------------------------------------------------------------------

class DailySummary(db.Model):
    __tablename__ = "daily_summary"

    id               = db.Column(db.Integer, primary_key=True)
    date             = db.Column(db.Date, nullable=False, unique=True)
    total_trades     = db.Column(db.Integer, default=0)   # rt_trades rows (closed)
    winning_trades   = db.Column(db.Integer, default=0)
    losing_trades    = db.Column(db.Integer, default=0)
    gross_pnl        = db.Column(db.Float,   default=0.0)
    net_pnl          = db.Column(db.Float,   default=0.0)
    total_commission = db.Column(db.Float,   default=0.0)
    win_rate         = db.Column(db.Float,   default=0.0)
    avg_winner       = db.Column(db.Float,   default=0.0)
    avg_loser        = db.Column(db.Float,   default=0.0)


# ---------------------------------------------------------------------------
# Journal — unchanged
# ---------------------------------------------------------------------------

class JournalEntry(db.Model):
    __tablename__ = "journal_entries"

    id         = db.Column(db.Integer, primary_key=True)
    date       = db.Column(db.Date,    nullable=False, unique=True)
    body       = db.Column(db.Text,    nullable=True)
    mood       = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
