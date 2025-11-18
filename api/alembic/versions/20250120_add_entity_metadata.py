"""Add entity metadata table for Telethon entities (Context7 P1.2)

Revision ID: 20250120_entity_metadata
Revises: 20250120_extend_forward_reply
Create Date: 2025-01-20 14:00:00.000000

Context7 P1.2: Хранение entity-level metadata для пользователей, каналов и чатов.
Поддерживает peer ID, access_hash, avatar_hash, bio, restrictions, admin status.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20250120_entity_metadata"
down_revision = "20250120_extend_forward_reply"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7 P1.2: Создание таблицы tg_entities для хранения entity-level metadata.
    
    Таблица хранит метаданные Telegram сущностей (users, channels, chats):
    - peer_id и peer_type (user/channel/chat/supergroup)
    - access_hash (для прямого доступа через Telethon API)
    - avatar_hash (для отслеживания изменений аватара)
    - bio (описание/био)
    - restrictions (ограничения доступа)
    - admin status и права
    - подписка статус (для каналов)
    """
    
    op.create_table(
        "tg_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("peer_id", sa.BigInteger(), nullable=False),  # Telegram peer ID
        sa.Column("peer_type", sa.String(20), nullable=False),  # 'user', 'channel', 'chat', 'supergroup'
        sa.Column("access_hash", sa.BigInteger(), nullable=True),  # Access hash для API доступа
        sa.Column("username", sa.String(255), nullable=True),  # Username (для каналов/пользователей)
        sa.Column("title", sa.Text(), nullable=True),  # Название (для каналов/чатов)
        sa.Column("first_name", sa.String(255), nullable=True),  # Имя (для пользователей)
        sa.Column("last_name", sa.String(255), nullable=True),  # Фамилия (для пользователей)
        sa.Column("avatar_hash", sa.String(64), nullable=True),  # SHA256 хеш аватара
        sa.Column("bio", sa.Text(), nullable=True),  # Описание/био
        sa.Column("restrictions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),  # Ограничения доступа
        sa.Column("is_verified", sa.Boolean(), nullable=True),  # Верификация
        sa.Column("is_premium", sa.Boolean(), nullable=True),  # Premium статус (для пользователей)
        sa.Column("is_scam", sa.Boolean(), nullable=True),  # Scam флаг
        sa.Column("is_fake", sa.Boolean(), nullable=True),  # Fake флаг
        sa.Column("is_bot", sa.Boolean(), nullable=True),  # Бот флаг
        sa.Column("is_channel", sa.Boolean(), nullable=True),  # Канал флаг
        sa.Column("is_broadcast", sa.Boolean(), nullable=True),  # Broadcast канал флаг
        sa.Column("is_megagroup", sa.Boolean(), nullable=True),  # Супергруппа флаг
        sa.Column("is_restricted", sa.Boolean(), nullable=True),  # Ограниченный доступ
        sa.Column("is_min", sa.Boolean(), nullable=True),  # Минимальная информация
        sa.Column("dc_id", sa.Integer(), nullable=True),  # DataCenter ID
        sa.Column("participants_count", sa.Integer(), nullable=True),  # Количество участников (для групп)
        sa.Column("members_count", sa.Integer(), nullable=True),  # Количество участников (для групп/каналов)
        sa.Column("admins_count", sa.Integer(), nullable=True),  # Количество администраторов
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),  # Последнее время, когда сущность была замечена
        sa.Column("entity_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),  # Дополнительные метаданные
    )
    
    # Уникальный индекс: (peer_id, peer_type) - одна запись на сущность
    op.create_unique_constraint(
        "uq_tg_entities_peer",
        "tg_entities",
        ["peer_id", "peer_type"]
    )
    
    # Индексы для быстрого поиска
    op.create_index(
        "idx_tg_entities_peer_id",
        "tg_entities",
        ["peer_id"]
    )
    op.create_index(
        "idx_tg_entities_peer_type",
        "tg_entities",
        ["peer_type"]
    )
    op.create_index(
        "idx_tg_entities_username",
        "tg_entities",
        ["username"],
        postgresql_where=sa.text("username IS NOT NULL")
    )
    op.create_index(
        "idx_tg_entities_access_hash",
        "tg_entities",
        ["access_hash"],
        postgresql_where=sa.text("access_hash IS NOT NULL")
    )
    op.create_index(
        "idx_tg_entities_last_seen",
        "tg_entities",
        ["last_seen_at"],
        postgresql_where=sa.text("last_seen_at IS NOT NULL"),
        postgresql_ops={"last_seen_at": "DESC"}
    )
    
    # Таблица для администраторов каналов/чатов
    op.create_table(
        "tg_entity_admins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tg_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_peer_id", sa.BigInteger(), nullable=False),  # Telegram ID администратора
        sa.Column("admin_peer_type", sa.String(20), nullable=False),  # 'user', 'bot'
        sa.Column("role", sa.String(50), nullable=True),  # Роль (owner, admin, moderator и т.д.)
        sa.Column("rights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),  # Права администратора (JSONB)
        sa.Column("rank", sa.String(255), nullable=True),  # Ранг/титул
        sa.Column("promoted_by", sa.BigInteger(), nullable=True),  # Кто назначил администратора
        sa.Column("is_self", sa.Boolean(), nullable=True),  # Это сам бот/клиент
        sa.Column("can_edit", sa.Boolean(), nullable=True),  # Может редактировать
        sa.Column("can_delete", sa.Boolean(), nullable=True),  # Может удалять
        sa.Column("can_ban", sa.Boolean(), nullable=True),  # Может банить
        sa.Column("can_invite", sa.Boolean(), nullable=True),  # Может приглашать
        sa.Column("can_change_info", sa.Boolean(), nullable=True),  # Может менять информацию
        sa.Column("can_post_messages", sa.Boolean(), nullable=True),  # Может публиковать (для каналов)
        sa.Column("can_edit_messages", sa.Boolean(), nullable=True),  # Может редактировать сообщения
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    
    # Уникальный индекс: (entity_id, admin_peer_id) - один администратор на сущность
    op.create_unique_constraint(
        "uq_tg_entity_admins_entity_admin",
        "tg_entity_admins",
        ["entity_id", "admin_peer_id"]
    )
    
    op.create_index(
        "idx_tg_entity_admins_entity_id",
        "tg_entity_admins",
        ["entity_id"]
    )
    op.create_index(
        "idx_tg_entity_admins_admin_peer_id",
        "tg_entity_admins",
        ["admin_peer_id"]
    )


def downgrade() -> None:
    """Откат миграции."""
    
    op.drop_table("tg_entity_admins")
    op.drop_table("tg_entities")

