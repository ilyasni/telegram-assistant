"""remove old users.telegram_id unique constraint (Context7)

После миграции на Identity/Membership модель, старый UNIQUE на users.telegram_id
больше не нужен - теперь telegram_id уникален в identities, а memberships
уникальны по (tenant_id, identity_id).

Revision ID: 20251103_remove_telegram_id_uniq
Revises: 20251103_enable_rls_policies
Create Date: 2025-11-03
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251103_remove_telegram_id_uniq'
down_revision = '20251103_enable_rls'  # Последняя применённая миграция
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Context7: Удаляем старое уникальное ограничение на users.telegram_id
    # Теперь telegram_id уникален в identities, а memberships уникальны по (tenant_id, identity_id)
    
    # Проверяем, что все users имеют identity_id (должно быть выполнено в предыдущей миграции)
    op.execute("""
        DO $$
        DECLARE
            null_count INTEGER;
        BEGIN
            SELECT COUNT(*) INTO null_count FROM users WHERE identity_id IS NULL;
            IF null_count > 0 THEN
                RAISE EXCEPTION 'Cannot remove users.telegram_id unique: % users still have NULL identity_id', null_count;
            END IF;
        END $$;
    """)
    
    # Удаляем старое уникальное ограничение
    try:
        op.drop_constraint('users_telegram_id_key', 'users', type_='unique')
    except Exception:
        # Если ограничение уже удалено - игнорируем
        pass
    
    # Удаляем partial unique index, если он ещё существует (заменён на обычный UNIQUE)
    op.execute("DROP INDEX IF EXISTS ux_users_tenant_identity_notnull")


def downgrade() -> None:
    # Восстанавливаем старое уникальное ограничение на users.telegram_id
    # ВНИМАНИЕ: Это может провалиться, если есть дубликаты telegram_id в разных tenants
    
    op.create_unique_constraint(
        'users_telegram_id_key',
        'users',
        ['telegram_id']
    )

