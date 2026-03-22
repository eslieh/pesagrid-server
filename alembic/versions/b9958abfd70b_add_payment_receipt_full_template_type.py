"""add payment_receipt_full template type

Revision ID: b9958abfd70b
Revises: 45f68a2df8c6
Create Date: 2026-03-22 15:27:00.809622

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9958abfd70b'
down_revision: Union[str, None] = '45f68a2df8c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the new enum value to the PostgreSQL type
    op.execute("ALTER TYPE templatetype ADD VALUE IF NOT EXISTS 'PAYMENT_RECEIPT_FULL'")

def downgrade() -> None:
    # Postgres doesn't easily support dropping from an ENUM.
    pass