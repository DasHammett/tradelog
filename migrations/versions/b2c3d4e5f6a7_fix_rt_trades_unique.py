"""drop_rt_trades_unique_constraint
Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-28 00:00:00.000000
rt_trades rows are always recomputed from STG via _compute_rt_trades which
deletes all rows for a (date, symbol) before reinserting. The unique
constraint on (date, symbol, exit_time) is too tight for split fills and
provides no real protection. Remove it entirely.
"""
from alembic import op
import sqlalchemy as sa
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None
def upgrade():
    with op.batch_alter_table("rt_trades") as batch_op:
        batch_op.drop_constraint("uq_rt_date_symbol_exit", type_="unique")
def downgrade():
    with op.batch_alter_table("rt_trades") as batch_op:
        batch_op.create_unique_constraint(
            "uq_rt_date_symbol_exit",
            ["date", "symbol", "exit_time"]
        )
