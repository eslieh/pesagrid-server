"""billing schema — subscription plans, wallets, usage tracking, invoices

Revision ID: a1b2c3d4e5f6
Revises: 2220d86d2c39
Create Date: 2026-04-08 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text
from app.core.db_types import UUID as PesaUUID

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5d4c3b2a1e0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Create billing schema ─────────────────────────────────────────────────
    op.execute("CREATE SCHEMA IF NOT EXISTS billing")

    # ── Enums — create only if missing ───────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE billing.planslug AS ENUM ('starter', 'growth', 'enterprise');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE billing.subscriptionstatus AS ENUM (
                'trial', 'active', 'suspended', 'blocked', 'cancelled'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE billing.wallettxtype AS ENUM (
                'topup', 'deduction', 'reversal', 'subscription'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE billing.wallettxevent AS ENUM (
                'reconciliation', 'sms', 'email', 'subscription_fee', 'topup', 'reversal'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE billing.invoicestatus AS ENUM (
                'draft', 'sent', 'paid', 'overdue', 'void'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ── subscription_plans ────────────────────────────────────────────────────
    op.create_table(
        'subscription_plans',
        sa.Column('id', PesaUUID(), nullable=False),
        sa.Column('slug', postgresql.ENUM('starter', 'growth', 'enterprise', name='planslug', schema='billing', create_type=False), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('monthly_fee_kes', sa.Numeric(12, 2), nullable=False),
        sa.Column('recon_fee_kes', sa.Numeric(10, 4), nullable=False),
        sa.Column('notification_fee_kes', sa.Numeric(10, 4), nullable=False),
        sa.Column('wallet_minimum_kes', sa.Numeric(12, 2), nullable=False),
        sa.Column('max_branches', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('max_psps', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('requires_custom_quote', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
        schema='billing',
    )

    # ── tenant_subscriptions ──────────────────────────────────────────────────
    op.create_table(
        'tenant_subscriptions',
        sa.Column('id', PesaUUID(), nullable=False),
        sa.Column('collection_id', PesaUUID(), nullable=False),
        sa.Column('plan_id', PesaUUID(), nullable=False),
        sa.Column('status', postgresql.ENUM('trial', 'active', 'suspended', 'blocked', 'cancelled', name='subscriptionstatus', schema='billing', create_type=False), nullable=False),
        sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('grace_ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recon_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('notification_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['billing.subscription_plans.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('collection_id'),
        schema='billing',
    )
    op.create_index('idx_tenant_subs_status', 'tenant_subscriptions', ['status'], schema='billing')
    op.create_index('idx_tenant_subs_collection', 'tenant_subscriptions', ['collection_id'], schema='billing')

    # ── tenant_wallets ────────────────────────────────────────────────────────
    op.create_table(
        'tenant_wallets',
        sa.Column('id', PesaUUID(), nullable=False),
        sa.Column('collection_id', PesaUUID(), nullable=False),
        sa.Column('subscription_id', PesaUUID(), nullable=False),
        sa.Column('balance_kes', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('lifetime_topup', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('is_auto_deduct_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_topup_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['subscription_id'], ['billing.tenant_subscriptions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('collection_id'),
        sa.UniqueConstraint('subscription_id'),
        schema='billing',
    )
    op.create_index('idx_tenant_wallets_collection', 'tenant_wallets', ['collection_id'], schema='billing')

    # ── wallet_transactions ───────────────────────────────────────────────────
    op.create_table(
        'wallet_transactions',
        sa.Column('id', PesaUUID(), nullable=False),
        sa.Column('wallet_id', PesaUUID(), nullable=False),
        sa.Column('collection_id', PesaUUID(), nullable=False),
        sa.Column('tx_type', postgresql.ENUM('topup', 'deduction', 'reversal', 'subscription', name='wallettxtype', schema='billing', create_type=False), nullable=False),
        sa.Column('event_type', postgresql.ENUM('reconciliation', 'sms', 'email', 'subscription_fee', 'topup', 'reversal', name='wallettxevent', schema='billing', create_type=False), nullable=False),
        sa.Column('amount_kes', sa.Numeric(12, 4), nullable=False),
        sa.Column('balance_after', sa.Numeric(14, 2), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('reference', sa.Text(), nullable=True),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['wallet_id'], ['billing.tenant_wallets.id']),
        sa.PrimaryKeyConstraint('id'),
        schema='billing',
    )
    op.create_index('idx_wallet_txn_wallet_created', 'wallet_transactions', ['wallet_id', 'created_at'], schema='billing')
    op.create_index('idx_wallet_txn_collection_type', 'wallet_transactions', ['collection_id', 'tx_type'], schema='billing')
    op.create_index('idx_wallet_txn_reference', 'wallet_transactions', ['reference'], schema='billing')

    # ── platform_invoices ─────────────────────────────────────────────────────
    op.create_table(
        'platform_invoices',
        sa.Column('id', PesaUUID(), nullable=False),
        sa.Column('collection_id', PesaUUID(), nullable=False),
        sa.Column('plan_id', PesaUUID(), nullable=False),
        sa.Column('invoice_number', sa.Text(), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('subscription_fee_kes', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('recon_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('recon_fee_total_kes', sa.Numeric(12, 4), nullable=False, server_default='0'),
        sa.Column('notification_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('notification_fee_total_kes', sa.Numeric(12, 4), nullable=False, server_default='0'),
        sa.Column('total_amount_kes', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('status', postgresql.ENUM('draft', 'sent', 'paid', 'overdue', 'void', name='invoicestatus', schema='billing', create_type=False), nullable=False),
        sa.Column('paystack_payment_link', sa.Text(), nullable=True),
        sa.Column('paystack_reference', sa.Text(), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['billing.subscription_plans.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invoice_number'),
        sa.UniqueConstraint('collection_id', 'period_start', name='uq_invoice_collection_period'),
        schema='billing',
    )
    op.create_index('idx_platform_invoices_collection', 'platform_invoices', ['collection_id'], schema='billing')
    op.create_index('idx_platform_invoices_period', 'platform_invoices', ['period_start', 'period_end'], schema='billing')
    op.create_index('idx_platform_invoices_status', 'platform_invoices', ['status'], schema='billing')

    # ── Seed plan data ────────────────────────────────────────────────────────
    op.execute("""
        INSERT INTO billing.subscription_plans (
            id, slug, name,
            monthly_fee_kes, recon_fee_kes, notification_fee_kes,
            wallet_minimum_kes, max_branches, max_psps,
            requires_custom_quote, features,
            is_active, created_at, updated_at
        ) VALUES
        (
            gen_random_uuid(), 'starter', 'Starter',
            3000.00, 0.3000, 0.5000,
            5000.00, 1, 2,
            false,
            '{
                "real_time_recon": false,
                "detailed_ledger": false,
                "multi_channel_reminders": false,
                "priority_support": false,
                "automated_mpesa_import": true,
                "weekly_reporting_emails": true
            }'::jsonb,
            true, NOW(), NOW()
        ),
        (
            gen_random_uuid(), 'growth', 'Growth',
            12000.00, 0.2000, 0.5000,
            15000.00, 20, -1,
            false,
            '{
                "real_time_recon": true,
                "detailed_ledger": true,
                "multi_channel_reminders": true,
                "priority_support": true,
                "automated_mpesa_import": true,
                "weekly_reporting_emails": true
            }'::jsonb,
            true, NOW(), NOW()
        ),
        (
            gen_random_uuid(), 'enterprise', 'Enterprise',
            0.00, 0.1000, 0.5000,
            0.00, -1, -1,
            true,
            '{
                "real_time_recon": true,
                "detailed_ledger": true,
                "multi_channel_reminders": true,
                "priority_support": true,
                "dedicated_account_manager": true,
                "advanced_audit_trails": true,
                "custom_api_integrations": true,
                "on_prem_deployment": true,
                "custom_sla": true
            }'::jsonb,
            true, NOW(), NOW()
        )
        ON CONFLICT (slug) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_index('idx_platform_invoices_status', table_name='platform_invoices', schema='billing')
    op.drop_index('idx_platform_invoices_period', table_name='platform_invoices', schema='billing')
    op.drop_index('idx_platform_invoices_collection', table_name='platform_invoices', schema='billing')
    op.drop_table('platform_invoices', schema='billing')

    op.drop_index('idx_wallet_txn_reference', table_name='wallet_transactions', schema='billing')
    op.drop_index('idx_wallet_txn_collection_type', table_name='wallet_transactions', schema='billing')
    op.drop_index('idx_wallet_txn_wallet_created', table_name='wallet_transactions', schema='billing')
    op.drop_table('wallet_transactions', schema='billing')

    op.drop_index('idx_tenant_wallets_collection', table_name='tenant_wallets', schema='billing')
    op.drop_table('tenant_wallets', schema='billing')

    op.drop_index('idx_tenant_subs_collection', table_name='tenant_subscriptions', schema='billing')
    op.drop_index('idx_tenant_subs_status', table_name='tenant_subscriptions', schema='billing')
    op.drop_table('tenant_subscriptions', schema='billing')

    op.drop_table('subscription_plans', schema='billing')

    # Drop enums
    op.execute("DROP TYPE IF EXISTS billing.invoicestatus")
    op.execute("DROP TYPE IF EXISTS billing.wallettxevent")
    op.execute("DROP TYPE IF EXISTS billing.wallettxtype")
    op.execute("DROP TYPE IF EXISTS billing.subscriptionstatus")
    op.execute("DROP TYPE IF EXISTS billing.planslug")

    op.execute("DROP SCHEMA IF EXISTS billing")
