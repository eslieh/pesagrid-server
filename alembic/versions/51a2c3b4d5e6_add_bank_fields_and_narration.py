"""add bank fields and narration

Revision ID: 51a2c3b4d5e6
Revises: 46ec6f5b876e
Create Date: 2026-04-12 21:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '51a2c3b4d5e6'
down_revision = '46ec6f5b876e'
branch_labels = None
depends_on = None


def upgrade():
    # ── Accounts schema ────────────────────────────────────────────────────────
    op.add_column('psp_configs', sa.Column('till_number', sa.Text(), nullable=True), schema='accounts')
    op.add_column('psp_configs', sa.Column('business_key', sa.Text(), nullable=True), schema='accounts')
    op.add_column('psp_configs', sa.Column('account_no', sa.Text(), nullable=True), schema='accounts')
    
    # ── Ingestion schema ───────────────────────────────────────────────────────
    op.add_column('transactions', sa.Column('narration', sa.Text(), nullable=True), schema='ingestion')
    op.create_index('idx_transactions_narration', 'transactions', ['collection_id', 'narration'], schema='ingestion')


def downgrade():
    # ── Ingestion schema ───────────────────────────────────────────────────────
    op.drop_index('idx_transactions_narration', table_name='transactions', schema='ingestion')
    op.drop_column('transactions', 'narration', schema='ingestion')
    
    # ── Accounts schema ────────────────────────────────────────────────────────
    op.drop_column('psp_configs', 'account_no', schema='accounts')
    op.drop_column('psp_configs', 'business_key', schema='accounts')
    op.drop_column('psp_configs', 'till_number', schema='accounts')
