"""add sms_acknowledgement and collection_receipt template type

Revision ID: c3a7f1b2e4d9
Revises: 4e76e5bf761e
Create Date: 2026-04-01 12:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a7f1b2e4d9'
down_revision: Union[str, None] = '4e76e5bf761e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add sms_acknowledgement column to collection_points
    op.add_column(
        'collection_points',
        sa.Column('sms_acknowledgement', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        schema='ingestion',
    )

    # 2. Add 'collection_receipt' to the templatetype enum
    #    PostgreSQL requires ALTER TYPE ... ADD VALUE
    op.execute("ALTER TYPE templatetype ADD VALUE IF NOT EXISTS 'COLLECTION_RECEIPT'")


def downgrade() -> None:
    # Remove the column (enum value removal is not trivially reversible in PG)
    op.drop_column('collection_points', 'sms_acknowledgement', schema='ingestion')
