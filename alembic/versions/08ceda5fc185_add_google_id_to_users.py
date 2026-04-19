"""add_google_id_to_users

Revision ID: 08ceda5fc185
Revises: 868e7dfa00ac
Create Date: 2026-04-19 14:41:17.608283

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '08ceda5fc185'
down_revision: Union[str, None] = '868e7dfa00ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add google_id column and indexes to auth.users
    op.add_column('users', sa.Column('google_id', sa.Text(), nullable=True), schema='auth')
    op.create_index('idx_users_google_id', 'users', ['google_id'], unique=False, schema='auth', postgresql_using='hash')
    op.create_index(op.f('ix_auth_users_google_id'), 'users', ['google_id'], unique=True, schema='auth')


def downgrade() -> None:
    # Remove google_id column and indexes from auth.users
    op.drop_index(op.f('ix_auth_users_google_id'), table_name='users', schema='auth')
    op.drop_index('idx_users_google_id', table_name='users', schema='auth', postgresql_using='hash')
    op.drop_column('users', 'google_id', schema='auth')