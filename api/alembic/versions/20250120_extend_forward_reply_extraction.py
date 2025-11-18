"""Extend forward/reply extraction with MessageFwdHeader fields (Context7 P1.1)

Revision ID: 20250120_extend_forward_reply
Revises: 20250120_telegram_sessions
Create Date: 2025-01-20 12:00:00.000000

Context7: Расширение извлечения forwards/replies для глубокого парсинга.
- Добавление полей из MessageFwdHeader в PostForward
- Быстрые поля в Post для прямого доступа к forward/reply данным
- Поддержка всех полей из Telethon MessageFwdHeader
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20250120_extend_forward_reply"
down_revision = "20250120_telegram_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Расширение схемы для глубокого извлечения forwards/replies.
    
    Добавляем:
    1. Поля в PostForward из MessageFwdHeader (from_id, from_name, post_author_signature, saved_from_peer, psa_type)
    2. Быстрые поля в Post для прямого доступа (forward_from_peer_id, forward_from_chat_id, forward_date)
    3. Дополнительные поля для reply (thread_id, forum_topic_id)
    """
    
    # 1. Расширение PostForward для поддержки всех полей MessageFwdHeader
    op.add_column(
        "post_forwards",
        sa.Column("from_id", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )  # Peer ID источника (может быть user_id, channel_id, chat_id)
    
    op.add_column(
        "post_forwards",
        sa.Column("from_name", sa.Text(), nullable=True)
    )  # Имя автора (если доступно)
    
    op.add_column(
        "post_forwards",
        sa.Column("post_author_signature", sa.Text(), nullable=True)
    )  # Подпись автора (для каналов)
    
    op.add_column(
        "post_forwards",
        sa.Column("saved_from_peer", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )  # Peer ID источника сохранённого форварда
    
    op.add_column(
        "post_forwards",
        sa.Column("saved_from_msg_id", sa.BigInteger(), nullable=True)
    )  # Message ID сохранённого форварда
    
    op.add_column(
        "post_forwards",
        sa.Column("psa_type", sa.String(255), nullable=True)
    )  # Тип публичного объявления (если есть)
    
    # Индексы для быстрого поиска forwards по источнику
    op.create_index(
        "idx_post_forwards_from_chat_id",
        "post_forwards",
        ["from_chat_id"],
        postgresql_where=sa.text("from_chat_id IS NOT NULL")
    )
    op.create_index(
        "idx_post_forwards_forwarded_at",
        "post_forwards",
        ["forwarded_at"],
        postgresql_ops={"forwarded_at": "DESC"}
    )
    
    # 2. Расширение Post для быстрого доступа к forward/reply данным
    op.add_column(
        "posts",
        sa.Column("forward_from_peer_id", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )  # Peer ID источника форварда (для быстрого доступа без JOIN)
    
    op.add_column(
        "posts",
        sa.Column("forward_from_chat_id", sa.BigInteger(), nullable=True)
    )  # Chat ID источника форварда (упрощённый доступ)
    
    op.add_column(
        "posts",
        sa.Column("forward_from_message_id", sa.BigInteger(), nullable=True)
    )  # Message ID источника форварда
    
    op.add_column(
        "posts",
        sa.Column("forward_date", sa.DateTime(timezone=True), nullable=True)
    )  # Дата оригинального сообщения
    
    op.add_column(
        "posts",
        sa.Column("forward_from_name", sa.Text(), nullable=True)
    )  # Имя автора оригинального сообщения
    
    # Поля для reply (расширение существующих)
    op.add_column(
        "posts",
        sa.Column("thread_id", sa.BigInteger(), nullable=True)
    )  # ID треда (для каналов с комментариями)
    
    op.add_column(
        "posts",
        sa.Column("forum_topic_id", sa.BigInteger(), nullable=True)
    )  # ID топика форума
    
    # Индексы для быстрого поиска по forwards
    op.create_index(
        "idx_posts_forward_from_chat_id",
        "posts",
        ["forward_from_chat_id"],
        postgresql_where=sa.text("forward_from_chat_id IS NOT NULL")
    )
    op.create_index(
        "idx_posts_forward_date",
        "posts",
        ["forward_date"],
        postgresql_where=sa.text("forward_date IS NOT NULL"),
        postgresql_ops={"forward_date": "DESC"}
    )
    op.create_index(
        "idx_posts_thread_id",
        "posts",
        ["thread_id"],
        postgresql_where=sa.text("thread_id IS NOT NULL")
    )
    
    # 3. Расширение PostReply для поддержки thread_id
    op.add_column(
        "post_replies",
        sa.Column("thread_id", sa.BigInteger(), nullable=True)
    )  # ID треда (для каналов с комментариями)
    
    op.create_index(
        "idx_post_replies_thread_id",
        "post_replies",
        ["thread_id"],
        postgresql_where=sa.text("thread_id IS NOT NULL")
    )


def downgrade() -> None:
    """Откат миграции."""
    
    # Удаление индексов
    op.drop_index("idx_post_replies_thread_id", table_name="post_replies")
    op.drop_index("idx_posts_thread_id", table_name="posts")
    op.drop_index("idx_posts_forward_date", table_name="posts")
    op.drop_index("idx_posts_forward_from_chat_id", table_name="posts")
    op.drop_index("idx_post_forwards_forwarded_at", table_name="post_forwards")
    op.drop_index("idx_post_forwards_from_chat_id", table_name="post_forwards")
    
    # Удаление колонок из post_replies
    op.drop_column("post_replies", "thread_id")
    
    # Удаление колонок из posts
    op.drop_column("posts", "forum_topic_id")
    op.drop_column("posts", "thread_id")
    op.drop_column("posts", "forward_from_name")
    op.drop_column("posts", "forward_date")
    op.drop_column("posts", "forward_from_message_id")
    op.drop_column("posts", "forward_from_chat_id")
    op.drop_column("posts", "forward_from_peer_id")
    
    # Удаление колонок из post_forwards
    op.drop_column("post_forwards", "psa_type")
    op.drop_column("post_forwards", "saved_from_msg_id")
    op.drop_column("post_forwards", "saved_from_peer")
    op.drop_column("post_forwards", "post_author_signature")
    op.drop_column("post_forwards", "from_name")
    op.drop_column("post_forwards", "from_id")

