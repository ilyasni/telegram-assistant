"""backfill identities and users.identity_id; add unique (tenant, identity); set NOT NULL

Revision ID: 20251103_backfill_identity
Revises: 20251103_add_identities
Create Date: 2025-11-03
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251103_backfill_identity'
down_revision = '20251103_add_identities'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Backfill identities from existing users.telegram_id
    # Context7: Используем GROUP BY вместо DISTINCT для работы с JSON типом
    op.execute(
        """
        INSERT INTO identities (id, telegram_id, created_at, meta)
        SELECT gen_random_uuid(), u.telegram_id, now(), '{}'::jsonb
        FROM users u
        GROUP BY u.telegram_id
        ON CONFLICT (telegram_id) DO NOTHING
        """
    )

    # 2) Set users.identity_id by join on telegram_id
    op.execute(
        """
        UPDATE users u
        SET identity_id = i.id
        FROM identities i
        WHERE i.telegram_id = u.telegram_id
          AND u.identity_id IS NULL
        """
    )

    # 3) Ensure no NULLs remain
    # (Validation query is implicit; ALTER will fail if any NULLs)
    op.alter_column('users', 'identity_id', existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)

    # 4) Add unique constraint on (tenant_id, identity_id)
    # Context7: После backfill все identity_id NOT NULL, можно сразу создать обычный UNIQUE
    op.create_unique_constraint(
        'users_tenant_identity_uniq',
        'users',
        ['tenant_id', 'identity_id']
    )

    # 5) Create helper index for queries (не CONCURRENTLY - Alembic в транзакции)
    op.create_index('ix_users_tenant_identity', 'users', ['tenant_id', 'identity_id'])


def downgrade() -> None:
    # Drop helper index
    op.drop_index('ix_users_tenant_identity', table_name='users')

    # Drop unique constraint
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_tenant_identity_uniq")

    # Allow NULL again (dual-read rollback)
    op.alter_column('users', 'identity_id', existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)


