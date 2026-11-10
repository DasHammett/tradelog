"""trade tags, strategies, ib_order_id on stg_executions, exec_id on rt_trades
Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-09 00:00:00.000000

- stg_executions.ib_order_id : IBKR order ID from the Flex <Trade> tag, used to
  group partial fills of one order.
- rt_trades.exec_id          : exec_id of the first fill of the (grouped) SELL,
  joins a round trip to its strategy tag.
- strategies / trade_tags    : user-editable strategy list and one tag per SELL
  execution (keyed on exec_id so tags survive rt_trades rebuilds).

After upgrading, run "Recompute" on the Import page once so existing rt_trades
rows are rebuilt with the grouping and get their exec_id.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None

STARTER_STRATEGIES = ["EMA 9 bounce", "Momentum", "VWAP breakout"]


def upgrade():
    with op.batch_alter_table("stg_executions") as batch_op:
        batch_op.add_column(sa.Column("ib_order_id", sa.String(length=32), nullable=True))
        batch_op.create_index("ix_stg_executions_ib_order_id", ["ib_order_id"])

    with op.batch_alter_table("rt_trades") as batch_op:
        batch_op.add_column(sa.Column("exec_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_rt_trades_exec_id", ["exec_id"])

    strategies = op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "trade_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exec_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exec_id"),
    )
    op.create_index("ix_trade_tags_exec_id", "trade_tags", ["exec_id"])

    op.bulk_insert(strategies, [
        {"name": name, "is_active": True, "sort_order": i}
        for i, name in enumerate(STARTER_STRATEGIES)
    ])


def downgrade():
    op.drop_index("ix_trade_tags_exec_id", table_name="trade_tags")
    op.drop_table("trade_tags")
    op.drop_table("strategies")
    with op.batch_alter_table("rt_trades") as batch_op:
        batch_op.drop_index("ix_rt_trades_exec_id")
        batch_op.drop_column("exec_id")
    with op.batch_alter_table("stg_executions") as batch_op:
        batch_op.drop_index("ix_stg_executions_ib_order_id")
        batch_op.drop_column("ib_order_id")
