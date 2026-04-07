"""refined statuses and reasons

Revision ID: 3e1a2f3b4c5d
Revises: 2220d86d2c39
Create Date: 2026-04-07 20:05:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3e1a2f3b4c5d'
down_revision = '2220d86d2c39'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Add new values to obligationstatus ENUM
    # PostgreSQL requires these to be committed before they can be used in DML.
    # We exit the transaction, add the values, and restart the transaction.
    op.execute("COMMIT")
    op.execute("ALTER TYPE obligationstatus ADD VALUE IF NOT EXISTS 'SETTLED'")
    op.execute("ALTER TYPE obligationstatus ADD VALUE IF NOT EXISTS 'VOIDED'")
    op.execute("ALTER TYPE obligationstatus ADD VALUE IF NOT EXISTS 'ROLLED'")
    op.execute("BEGIN")

def downgrade() -> None:
    # Removing values from an ENUM is not supported in Postgres without dropping/recreating the type.
    pass
