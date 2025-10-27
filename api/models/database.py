"""Модели базы данных."""

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text, JSON, BigInteger, ForeignKey, UniqueConstraint, Index, CheckConstraint, PrimaryKeyConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime
from typing import Generator
from config import settings

Base = declarative_base()

# Настройка подключения к БД
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency для получения сессии БД."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Tenant(Base):
    """Модель арендатора."""
    __tablename__ = "tenants"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    settings = Column(JSON, default={})
    
    # Relationships
    users = relationship("User", back_populates="tenant")
    # channels УДАЛЕНА - каналы теперь глобальные


class User(Base):
    """Модель пользователя."""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active_at = Column(DateTime)
    settings = Column(JSON, default={})
    tier = Column(String(20), default="free")
    
    # Relationships
    tenant = relationship("Tenant", back_populates="users")
    channel_subscriptions = relationship("UserChannel", back_populates="user")
    group_subscriptions = relationship("UserGroup", back_populates="user")


class Channel(Base):
    """Модель канала (глобальный)."""
    __tablename__ = "channels"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # tenant_id УДАЛЁН - каналы теперь глобальные
    tg_channel_id = Column(BigInteger, nullable=False, unique=True)
    username = Column(String(255))
    title = Column(String(500), nullable=False)
    is_active = Column(Boolean, default=True)
    last_message_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    settings = Column(JSON, default={})
    
    # Relationships
    posts = relationship("Post", back_populates="channel")
    user_subscriptions = relationship("UserChannel", back_populates="channel")


class Post(Base):
    """Модель поста (глобальный)."""
    __tablename__ = "posts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # tenant_id УДАЛЁН - посты теперь глобальные
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id"), nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    content = Column(Text)
    media_urls = Column(JSON, default=[])
    posted_at = Column(DateTime)
    url = Column(Text)
    telegram_post_url = Column(Text)
    has_media = Column(Boolean, default=False)
    yyyymm = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_processed = Column(Boolean, default=False)
    
    # Telegram-специфичные метрики
    views_count = Column(Integer, default=0)
    forwards_count = Column(Integer, default=0)
    reactions_count = Column(Integer, default=0)
    replies_count = Column(Integer, default=0)
    is_pinned = Column(Boolean, default=False)
    is_edited = Column(Boolean, default=False)
    edited_at = Column(DateTime)
    post_author = Column(String(255))
    reply_to_message_id = Column(BigInteger)
    reply_to_chat_id = Column(BigInteger)
    via_bot_id = Column(BigInteger)
    via_business_bot_id = Column(BigInteger)
    is_silent = Column(Boolean, default=False)
    is_legacy = Column(Boolean, default=False)
    noforwards = Column(Boolean, default=False)
    invert_media = Column(Boolean, default=False)
    last_metrics_update = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    channel = relationship("Channel", back_populates="posts")
    indexing_status = relationship("IndexingStatus", back_populates="post")
    enrichment = relationship("PostEnrichment", back_populates="post", uselist=False)
    media = relationship("PostMedia", back_populates="post")
    reactions = relationship("PostReaction", back_populates="post")
    forwards = relationship("PostForward", back_populates="post")
    replies = relationship("PostReply", back_populates="post", foreign_keys="PostReply.post_id")
    
    # Unique constraint для глобальной уникальности
    __table_args__ = (
        UniqueConstraint('channel_id', 'telegram_message_id', name='ux_posts_chan_msg'),
    )


class IndexingStatus(Base):
    """Модель статуса индексации."""
    __tablename__ = "indexing_status"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)
    embedding_status = Column(String(50), default="pending")
    graph_status = Column(String(50), default="pending")
    processing_started_at = Column(DateTime)
    processing_completed_at = Column(DateTime)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    
    # Relationships
    post = relationship("Post", back_populates="indexing_status")


# --- Telegram auth & sessions ---

class EncryptionKey(Base):
    """Ключи шифрования для Telethon StringSession (поддержка ротации)."""
    __tablename__ = "encryption_keys"

    key_id = Column(String(64), primary_key=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    rotated_at = Column(DateTime(timezone=True))
    retired_at = Column(DateTime(timezone=True))


class TelegramSession(Base):
    """Зашифрованные Telethon StringSession на арендатора/пользователя."""
    __tablename__ = "telegram_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    session_string_enc = Column(Text, nullable=False)
    key_id = Column(String(64), ForeignKey("encryption_keys.key_id"), nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # pending|authorized|revoked|expired|failed
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # максимум одна активная авторизованная сессия на tenant+user
        UniqueConstraint("tenant_id", "user_id", "status", name="uq_session_active", deferrable=True, initially="DEFERRED"),
        Index("ix_telegram_sessions_tenant", "tenant_id"),
        Index("ix_telegram_sessions_status", "status"),
    )


class TelegramAuthLog(Base):
    """Аудит событий авторизации Telegram (QR/miniapp)."""
    __tablename__ = "telegram_auth_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("telegram_sessions.id"), nullable=False)
    event = Column(String(64), nullable=False)
    reason = Column(String(255))
    error_code = Column(String(64))
    ip = Column(String(64))
    user_agent = Column(String(512))
    latency_ms = Column(Integer)
    at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    meta = Column(JSON, default={})

# ============================================================================
# НОВЫЕ МОДЕЛИ ДЛЯ MANY-TO-MANY, ГРУПП И ОБОГАЩЕНИЯ
# ============================================================================

class UserChannel(Base):
    """Many-to-many связь пользователей и каналов."""
    __tablename__ = "user_channel"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id"), primary_key=True)
    subscribed_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    settings = Column(JSON, default={})
    
    # Relationships
    user = relationship("User", back_populates="channel_subscriptions")
    channel = relationship("Channel", back_populates="user_subscriptions")


class PostEnrichment(Base):
    """Обогащённые данные постов."""
    __tablename__ = "post_enrichment"
    
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), primary_key=True)
    tags = Column(JSONB, default=[])  # Используем JSONB для GIN индексов
    vision_labels = Column(JSONB, default=[])
    ocr_text = Column(Text)
    crawl_md = Column(Text)
    enrichment_provider = Column(String(50))
    enriched_at = Column(DateTime, default=datetime.utcnow)
    enrichment_latency_ms = Column(Integer)
    enrichment_metadata = Column(JSONB, default={})
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    post = relationship("Post", back_populates="enrichment")


class PostMedia(Base):
    """Медиа-файлы постов."""
    __tablename__ = "post_media"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)
    media_type = Column(String(50), nullable=False)
    media_url = Column(Text, nullable=False)
    thumbnail_url = Column(Text)
    file_size_bytes = Column(BigInteger)
    width = Column(Integer)
    height = Column(Integer)
    duration_seconds = Column(Integer)
    # Telegram-специфичные поля для дедупликации
    tg_file_id = Column(Text)
    tg_file_unique_id = Column(Text)
    sha256 = Column(Text)  # BYTEA в PostgreSQL, но Text в SQLAlchemy для простоты
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    post = relationship("Post", back_populates="media")
    
    # Constraints
    __table_args__ = (
        CheckConstraint("media_type IN ('photo', 'video', 'document')", name='chk_media_type'),
    )


class Group(Base):
    """Групповые чаты."""
    __tablename__ = "groups"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    tg_chat_id = Column(BigInteger, nullable=False)
    title = Column(String(500), nullable=False)
    username = Column(String(255))
    is_active = Column(Boolean, default=True)
    last_checked_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    settings = Column(JSON, default={})
    
    # Relationships
    tenant = relationship("Tenant")
    messages = relationship("GroupMessage", back_populates="group")
    user_subscriptions = relationship("UserGroup", back_populates="group")


class UserGroup(Base):
    """Подписки пользователей на группы."""
    __tablename__ = "user_group"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), primary_key=True)
    monitor_mentions = Column(Boolean, default=True)
    subscribed_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    settings = Column(JSON, default={})
    
    # Relationships
    user = relationship("User", back_populates="group_subscriptions")
    group = relationship("Group", back_populates="user_subscriptions")


class GroupMessage(Base):
    """Сообщения из групповых чатов."""
    __tablename__ = "group_messages"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    tg_message_id = Column(BigInteger, nullable=False)
    sender_tg_id = Column(BigInteger)
    sender_username = Column(String(255))
    content = Column(Text)
    posted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    group = relationship("Group", back_populates="messages")
    mentions = relationship("GroupMention", back_populates="message")


class GroupMention(Base):
    """Упоминания пользователей в группах."""
    __tablename__ = "group_mentions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_message_id = Column(UUID(as_uuid=True), ForeignKey("group_messages.id"), nullable=False)
    mentioned_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    mentioned_user_tg_id = Column(BigInteger, nullable=False)
    context_snippet = Column(Text)
    is_processed = Column(Boolean, default=False)
    processed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    message = relationship("GroupMessage", back_populates="mentions")
    mentioned_user = relationship("User")


class PostReaction(Base):
    """Модель реакции на пост."""
    __tablename__ = "post_reactions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)
    reaction_type = Column(String(50), nullable=False)  # 'emoji', 'custom_emoji', 'paid'
    reaction_value = Column(Text, nullable=False)  # emoji или document_id
    user_tg_id = Column(BigInteger)  # ID пользователя (если доступен)
    is_big = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    post = relationship("Post", back_populates="reactions")
    
    # Unique constraint для предотвращения дубликатов
    __table_args__ = (
        UniqueConstraint('post_id', 'reaction_type', 'reaction_value', 'user_tg_id', 
                        name='ux_post_reactions_unique'),
    )


class PostForward(Base):
    """Модель репоста поста."""
    __tablename__ = "post_forwards"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)
    from_chat_id = Column(BigInteger)  # ID чата, откуда репост
    from_message_id = Column(BigInteger)  # ID сообщения, откуда репост
    from_chat_title = Column(Text)  # Название чата
    from_chat_username = Column(Text)  # Username чата
    forwarded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    post = relationship("Post", back_populates="forwards")


class PostReply(Base):
    """Модель комментария/ответа на пост."""
    __tablename__ = "post_replies"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)
    reply_to_post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"))
    reply_message_id = Column(BigInteger, nullable=False)
    reply_chat_id = Column(BigInteger, nullable=False)
    reply_author_tg_id = Column(BigInteger)
    reply_author_username = Column(String(255))
    reply_content = Column(Text)
    reply_posted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    post = relationship("Post", back_populates="replies", foreign_keys=[post_id])
    reply_to_post = relationship("Post", foreign_keys=[reply_to_post_id])
