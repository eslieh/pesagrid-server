"""add all to templatechannel

Revision ID: 1e0812ee9140
Revises: 391da84f4408
Create Date: 2026-03-25 16:15:23.663697

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e0812ee9140'
down_revision: Union[str, None] = '391da84f4408'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add 'all' to templatechannel enum
    op.execute("COMMIT")  # ALTER TYPE cannot run in a transaction block
    op.execute("ALTER TYPE templatechannel ADD VALUE 'all'")
    
    # 2. Add new values to templatetype enum
    op.execute("ALTER TYPE templatetype ADD VALUE 'obligation_created'")
    op.execute("ALTER TYPE templatetype ADD VALUE 'obligation_cancelled'")


def downgrade() -> None:
    # PostgreSQL doesn't support easy column value removal from enum types.
    # Recreating the type is out of scope for this revision as a simple fix.
    pass