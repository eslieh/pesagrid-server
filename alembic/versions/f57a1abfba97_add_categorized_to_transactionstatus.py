"""add categorized to transactionstatus

Revision ID: f57a1abfba97
Revises: 02da5aa05a35
Create Date: 2026-03-22 17:33:51.798867

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f57a1abfba97'
down_revision: Union[str, None] = '02da5aa05a35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adding a value to an enum is not supported by standard Alembic operations
    # so we use a raw SQL command.
    # Note: enum values are case-sensitive in PostgreSQL and were created as uppercase in previous migrations.
    op.execute("ALTER TYPE transactionstatus ADD VALUE 'CATEGORIZED'")


def downgrade() -> None:
    # PostgreSQL doesn't support dropping an enum value directly.
    # We can skip it here as it doesn't break anything.
    pass