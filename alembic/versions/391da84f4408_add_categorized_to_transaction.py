"""add categorized to transaction

Revision ID: 391da84f4408
Revises: 417f9d22721b
Create Date: 2026-03-25 16:03:20.132242

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '391da84f4408'
down_revision: Union[str, None] = '417f9d22721b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass