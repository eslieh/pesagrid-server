"""datetimedetlat

Revision ID: 4e76e5bf761e
Revises: 1e0812ee9140
Create Date: 2026-03-26 09:30:48.395538

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e76e5bf761e'
down_revision: Union[str, None] = '1e0812ee9140'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass