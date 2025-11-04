"""enable RLS policies for multi-tenant isolation (Context7)

Revision ID: 20251103_enable_rls
Revises: 20251103_backfill_identity
Create Date: 2025-11-03
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251103_enable_rls'
down_revision = '20251103_backfill_identity'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Context7: Включаем RLS на таблицах с tenant-данными
    # Политика: USING (tenant_id = current_setting('app.tenant_id')::uuid)
    
    # 1) users (memberships) - изоляция по tenant_id
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY users_tenant_isolation ON users
        FOR ALL
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)
    
    # 2) identities - SELECT только через join на memberships (нет прямого tenant_id)
    # Для identities разрешаем SELECT всем, но UPDATE/DELETE только через JOIN проверку
    op.execute("ALTER TABLE identities ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY identities_select_all ON identities
        FOR SELECT
        USING (true)  -- SELECT разрешён всем, но обычно через JOIN с users
    """)
    op.execute("""
        CREATE POLICY identities_insert_all ON identities
        FOR INSERT
        WITH CHECK (true)  -- INSERT разрешён (создание через upsert)
    """)
    op.execute("""
        CREATE POLICY identities_update_restricted ON identities
        FOR UPDATE
        USING (
            EXISTS (
                SELECT 1 FROM users u
                WHERE u.identity_id = identities.id
                AND u.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM users u
                WHERE u.identity_id = identities.id
                AND u.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
    """)
    
    # 3) user_channel - изоляция по tenant_id через users
    op.execute("ALTER TABLE user_channel ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY user_channel_tenant_isolation ON user_channel
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM users u
                WHERE u.id = user_channel.user_id
                AND u.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM users u
                WHERE u.id = user_channel.user_id
                AND u.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
    """)
    
    # 4) telegram_sessions - изоляция по tenant_id
    op.execute("ALTER TABLE telegram_sessions ENABLE ROW LEVEL SECURITY")
    # Context7: tenant_id может быть VARCHAR в telegram_sessions, используем CAST
    op.execute("""
        CREATE POLICY telegram_sessions_tenant_isolation ON telegram_sessions
        FOR ALL
        USING (tenant_id::text = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
    """)
    
    # 5) groups - изоляция по tenant_id
    op.execute("ALTER TABLE groups ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY groups_tenant_isolation ON groups
        FOR ALL
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)
    
    # 6) user_group - изоляция по tenant_id через users
    op.execute("ALTER TABLE user_group ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY user_group_tenant_isolation ON user_group
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM users u
                WHERE u.id = user_group.user_id
                AND u.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM users u
                WHERE u.id = user_group.user_id
                AND u.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
    """)
    
    # 7) group_messages - изоляция по tenant_id через groups
    op.execute("ALTER TABLE group_messages ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY group_messages_tenant_isolation ON group_messages
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM groups g
                WHERE g.id = group_messages.group_id
                AND g.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM groups g
                WHERE g.id = group_messages.group_id
                AND g.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
    """)
    
    # 8) group_mentions - изоляция по tenant_id через group_messages -> groups
    op.execute("ALTER TABLE group_mentions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY group_mentions_tenant_isolation ON group_mentions
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM group_messages gm
                JOIN groups g ON g.id = gm.group_id
                WHERE gm.id = group_mentions.group_message_id
                AND g.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM group_messages gm
                JOIN groups g ON g.id = gm.group_id
                WHERE gm.id = group_mentions.group_message_id
                AND g.tenant_id = current_setting('app.tenant_id', true)::uuid
            )
        )
    """)


def downgrade() -> None:
    # Удаляем политики и отключаем RLS
    for table in ['group_mentions', 'group_messages', 'user_group', 'groups', 
                  'telegram_sessions', 'user_channel', 'identities', 'users']:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_select_all ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_insert_all ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_update_restricted ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

