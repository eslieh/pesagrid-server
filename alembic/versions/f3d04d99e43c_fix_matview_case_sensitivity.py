"""fix_matview_case_sensitivity

Revision ID: f3d04d99e43c
Revises: d9791aa0f57c
Create Date: 2026-04-12 15:45:36.583033

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3d04d99e43c'
down_revision: Union[str, None] = 'd9791aa0f57c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop existing view (which drops its indexes)
    op.execute("DROP MATERIALIZED VIEW IF EXISTS obligations.ledger_summary;")

    # Recreate view with UPPERCASE filters to match database values
    op.execute("""
        CREATE MATERIALIZED VIEW obligations.ledger_summary AS
        SELECT
            o.collection_id,
            p.group_id,
            o.payer_id,
            p.name              AS payer_name,
            p.phone             AS payer_phone,
            p.account_no        AS payer_account_no,
            p.is_active         AS payer_is_active,
            COUNT(o.id)         AS total_obligations,
            COALESCE(SUM(o.amount_due)  FILTER (WHERE UPPER(o.status::text) IN ('PENDING', 'PARTIAL', 'OVERDUE')), 0) AS total_due,
            COALESCE(SUM(o.amount_paid) FILTER (WHERE UPPER(o.status::text) IN ('PENDING', 'PARTIAL', 'OVERDUE')), 0) AS total_paid,
            COALESCE(SUM(o.balance)     FILTER (WHERE UPPER(o.status::text) IN ('PENDING', 'PARTIAL', 'OVERDUE')), 0) AS total_balance,
            COUNT(*) FILTER (WHERE UPPER(o.status::text) = 'OVERDUE')                          AS overdue_count,
            COUNT(*) FILTER (WHERE UPPER(o.status::text) IN ('PENDING', 'PARTIAL'))             AS pending_count,
            COUNT(*) FILTER (WHERE UPPER(o.status::text) = 'SETTLED')                          AS settled_count,
            MIN(o.due_date) FILTER (
                WHERE UPPER(o.status::text) IN ('PENDING', 'PARTIAL', 'OVERDUE')
                  AND o.due_date IS NOT NULL
            )                   AS next_due_date,
            MAX(o.updated_at)   AS last_activity
        FROM obligations.obligations o
        JOIN obligations.payers p ON p.id = o.payer_id
        GROUP BY
            o.collection_id,
            p.group_id,
            o.payer_id,
            p.name,
            p.phone,
            p.account_no,
            p.is_active
        WITH DATA;
    """)

    # Recreate the indexes
    op.execute("""
        CREATE UNIQUE INDEX idx_ledger_summary_pk
        ON obligations.ledger_summary (collection_id, payer_id);
    """)

    op.execute("""
        CREATE INDEX idx_ledger_summary_group
        ON obligations.ledger_summary (collection_id, group_id);
    """)

    op.execute("""
        CREATE INDEX idx_ledger_summary_overdue
        ON obligations.ledger_summary (collection_id, overdue_count DESC);
    """)

    op.execute("""
        CREATE INDEX idx_ledger_summary_balance
        ON obligations.ledger_summary (collection_id, total_balance DESC);
    """)

def downgrade() -> None:
    pass