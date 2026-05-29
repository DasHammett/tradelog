"""add_cost_basis_to_stg_executions
Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-29 00:00:00.000000
Adds cost_basis column to stg_executions.
Populated during _compute_rt_trades for SELL rows:
      cost_basis = (total_buy_value + total_buy_commission) / total_buy_qty
      NULL for BUY rows.
      """
from alembic import op
import sqlalchemy as sa
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None
def upgrade():
    with op.batch_alter_table("stg_executions") as batch_op:
            batch_op.add_column(sa.Column("cost_basis", sa.Float(), nullable=True))

def downgrade():
    with op.batch_alter_table("stg_executions") as batch_op:
            batch_op.drop_column("cost_basis")
