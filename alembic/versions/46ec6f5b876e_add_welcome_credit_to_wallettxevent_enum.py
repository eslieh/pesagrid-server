"""add welcome_credit to wallettxevent enum

Revision ID: 46ec6f5b876e
Revises: d3e4690c13e8
Create Date: 2026-04-12 23:47:51.872270

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46ec6f5b876e'
down_revision: Union[str, None] = 'd3e4690c13e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Handle the Enum expansion for 'wallettxevent' in 'billing' schema
    # PostgreSQL requires a COMMIT before ALTER TYPE ... ADD VALUE
    op.execute("COMMIT")
    op.execute("ALTER TYPE billing.wallettxevent ADD VALUE IF NOT EXISTS 'welcome_credit'")


def downgrade() -> None:
    # Downgrading enums is difficult in Postgres (requires recreating the type).
    # Since this is adding a metadata value, we usually leave it.
    pass