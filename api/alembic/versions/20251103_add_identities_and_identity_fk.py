"""add identities table and identity_id FK on users (Context7 dual-stage)

Revision ID: 20251103_add_identities
Revises: 20250130_unify_post_enrichment_schema
Create Date: 2025-11-03
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251103_add_identities'
down_revision = '20250130_unify_enrichment'  # Исправлено: используем реальный revision ID
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) identities table
    op.create_table(
        'identities',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('meta', sa.JSON(), server_default=sa.text("'{}'::json")),
    )

    # global unique on telegram_id (не CONCURRENTLY - Alembic в транзакции)
    op.create_index(
        'ux_identities_telegram_id',
        'identities',
        ['telegram_id'],
        unique=True
    )

    # 2) add users.identity_id (nullable for dual-read)
    op.add_column(
        'users',
        sa.Column('identity_id', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    )

    # FK with RESTRICT delete semantics
    op.create_foreign_key(
        constraint_name='fk_users_identity_id',
        source_table='users',
        referent_table='identities',
        local_cols=['identity_id'],
        remote_cols=['id'],
        ondelete='RESTRICT'
    )

    # Ensure CASCADE for users.tenant_id (documented; FK may already exist)
    # Recreate FK with ON DELETE CASCADE if needed — safe no-op if already correct
    try:
        op.drop_constraint('users_tenant_id_fkey', 'users', type_='foreignkey')
    except Exception:
        pass
    op.create_foreign_key(
        constraint_name='fk_users_tenant_id',
        source_table='users',
        referent_table='tenants',
        local_cols=['tenant_id'],
        remote_cols=['id'],
        ondelete='CASCADE'
    )

    # Helpful indexes (не CONCURRENTLY - Alembic в транзакции)
    op.create_index('ix_users_tenant', 'users', ['tenant_id'])
    op.create_index('ix_users_identity', 'users', ['identity_id'])
    
    # Partial unique for backfill window (will be replaced later)
    # Используем raw SQL для partial index
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_users_tenant_identity_notnull
        ON users(tenant_id, identity_id)
        WHERE identity_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Drop partial unique
    op.execute(
        "DROP INDEX IF EXISTS ux_users_tenant_identity_notnull"
    )

    # Drop helper indexes
    op.drop_index('ix_users_identity', table_name='users')
    op.drop_index('ix_users_tenant', table_name='users')

    # Drop FKs and column
    try:
        op.drop_constraint('fk_users_identity_id', 'users', type_='foreignkey')
    except Exception:
        pass
    op.drop_column('users', 'identity_id')

    # identities unique index and table
    op.drop_index('ux_identities_telegram_id', table_name='identities')
    op.drop_table('identities')


