"""migrate obligation data

Revision ID: 4f2e1d0c9b8a
Revises: 3e1a2f3b4c5d
Create Date: 2026-04-07 20:10:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '4f2e1d0c9b8a'
down_revision = '3e1a2f3b4c5d'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Add status_reason column to obligations table
    op.add_column('obligations', sa.Column('status_reason', sa.Text(), nullable=True), schema='obligations')

    # 2. Migrate existing data to new statuses
    # PAID -> SETTLED
    # CANCELLED -> VOIDED
    op.execute("UPDATE obligations.obligations SET status = 'SETTLED' WHERE status = 'PAID'")
    op.execute("UPDATE obligations.obligations SET status = 'VOIDED' WHERE status = 'CANCELLED'")

def downgrade() -> None:
    # 1. Migrate data back (SETTLED/VOIDED/ROLLED -> PAID/CANCELLED)
    op.execute("UPDATE obligations.obligations SET status = 'PAID' WHERE status = 'SETTLED'")
    op.execute("UPDATE obligations.obligations SET status = 'CANCELLED' WHERE status IN ('VOIDED', 'ROLLED')")
    
    # 2. Drop the status_reason column
    op.drop_column('obligations', 'status_reason', schema='obligations')
