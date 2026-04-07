"""add credit_balance to payer

Revision ID: 5d4c3b2a1e0f
Revises: 4f2e1d0c9b8a
Create Date: 2026-04-07 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5d4c3b2a1e0f'
down_revision = '4f2e1d0c9b8a'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Add credit_balance column to payers table
    op.add_column('payers', sa.Column('credit_balance', sa.Numeric(precision=18, scale=2), server_default='0', nullable=False), schema='obligations')

def downgrade() -> None:
    # 1. Drop credit_balance column
    op.drop_column('payers', 'credit_balance', schema='obligations')
