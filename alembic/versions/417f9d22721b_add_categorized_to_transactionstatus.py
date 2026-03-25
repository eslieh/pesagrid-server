"""add categorized to transactionstatus

Revision ID: 417f9d22721b
Revises: f57a1abfba97
Create Date: 2026-03-25 16:03:04.452878

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '417f9d22721b'
down_revision: Union[str, None] = 'f57a1abfba97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass