"""flex_columns
Revision ID: a1b2c3d4e5f6
Revises: 7bde767cae95
Create Date: 2026-05-28 00:00:00.000000
Adds Flex XML fields to stg_executions and removes TLG-only columns.
Since we are doing a clean-slate reimport, this migration drops the table
and recreates it with the new schema rather than ALTER-ing columns, which
avoids SQLite's limited ALTER TABLE support.
"""
from alembic import op
import sqlalchemy as sa
revision = 'a1b2c3d4e5f6'
down_revision = '7bde767cae95'
branch_labels = None
depends_on = None
def upgrade():
    # Truncate all data tables — clean slate for Flex XML reimport.
    # Schema-only tables (journal_entries) are also cleared per user decision.
    op.execute("DELETE FROM rt_trades")
    op.execute("DELETE FROM ods_daily_symbol")
    op.execute("DELETE FROM daily_summary")
    op.execute("DELETE FROM journal_entries")
    op.execute("DELETE FROM stg_executions")
    # SQLite does not support DROP COLUMN reliably, so recreate the table.
    op.drop_table("stg_executions")
    op.create_table(
        "stg_executions",
        sa.Column("id",                 sa.Integer(),     primary_key=True),
        sa.Column("exec_id",            sa.String(64),    nullable=False, unique=True),
        sa.Column("symbol",             sa.String(20),    nullable=False),
        sa.Column("date",               sa.Date(),        nullable=False),
        sa.Column("time",               sa.Time(),        nullable=False),
        sa.Column("side",               sa.String(4),     nullable=False),
        sa.Column("quantity",           sa.Float(),       nullable=False),
        sa.Column("price",              sa.Float(),       nullable=False),
        sa.Column("commission",         sa.Float(),       nullable=True),
        sa.Column("currency",           sa.String(10),    nullable=True),
        sa.Column("source",             sa.String(10),    nullable=True),
        sa.Column("order_type",         sa.String(10),    nullable=True),
        sa.Column("exchange",           sa.String(20),    nullable=True),
        sa.Column("open_close",         sa.String(1),     nullable=True),
        sa.Column("broker_charge",      sa.Float(),       nullable=True),
        sa.Column("third_party_charge", sa.Float(),       nullable=True),
        sa.Column("clearing_charge",    sa.Float(),       nullable=True),
        sa.Column("regulatory_charge",  sa.Float(),       nullable=True),
        sa.Column("imported_at",        sa.DateTime(),    nullable=True),
    )
def downgrade():
    # Restore the original TLG schema (data is lost — this is intentional)
    op.drop_table("stg_executions")
    op.create_table(
        "stg_executions",
        sa.Column("id",          sa.Integer(),    primary_key=True),
        sa.Column("exec_id",     sa.String(64),   nullable=False, unique=True),
        sa.Column("symbol",      sa.String(20),   nullable=False),
        sa.Column("date",        sa.Date(),       nullable=False),
        sa.Column("time",        sa.Time(),       nullable=False),
        sa.Column("action_raw",  sa.String(20),   nullable=False),
        sa.Column("side",        sa.String(4),    nullable=False),
        sa.Column("quantity",    sa.Float(),      nullable=False),
        sa.Column("price",       sa.Float(),      nullable=False),
        sa.Column("commission",  sa.Float(),      nullable=True),
        sa.Column("currency",    sa.String(10),   nullable=True),
        sa.Column("raw_line",    sa.Text(),       nullable=True),
        sa.Column("imported_at", sa.DateTime(),   nullable=True),
    )
