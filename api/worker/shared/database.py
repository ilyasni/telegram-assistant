"""Модели базы данных."""

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text, JSON, BigInteger, ForeignKey, UniqueConstraint, Index, CheckConstraint, PrimaryKeyConstraint, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from datetime import datetime
from typing import Any, Dict, Generator
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


class Identity(Base):
    """Глобальная личность (Telegram-пользователь)."""
    __tablename__ = "identities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    meta = Column(JSON, default={})

    # Relationships
    memberships = relationship("User", back_populates="identity")


class User(Base):
    """Модель пользователя."""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    # Context7: telegram_id НЕ уникален в users (может повторяться в разных tenants)
    # Уникальность гарантируется через identities.telegram_id + (tenant_id, identity_id) UNIQUE
    telegram_id = Column(BigInteger, nullable=False)  # Dual-write для обратной совместимости
    # Context7: переход к модели Membership — добавляем ссылку на Identity (dual-read/dual-write)
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id"), nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active_at = Column(DateTime)
    settings = Column(JSON, default={})
    tier = Column(String(20), default="free")
    
    # Relationships
    tenant = relationship("Tenant", back_populates="users")
    identity = relationship("Identity", back_populates="memberships")
    channel_subscriptions = relationship("UserChannel", back_populates="user")
    group_subscriptions = relationship("UserGroup", back_populates="user")

    __table_args__ = (
        Index("ix_users_tenant", "tenant_id"),
        Index("ix_users_identity", "identity_id"),
    )


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
    media_map = relationship("PostMediaMap", back_populates="post", cascade="all, delete-orphan")
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
    """Обогащённые данные постов (унифицированная модель с kind/provider/data)."""
    __tablename__ = "post_enrichment"
    
    # Context7: Составной первичный ключ (post_id, kind) для модульного хранения
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    kind = Column(String(50), nullable=False, primary_key=True)  # 'vision', 'vision_ocr', 'crawl', 'tags', 'classify', 'general'
    provider = Column(String(50), nullable=False)  # 'gigachat-vision', 'tesseract', 'crawl4ai'
    params_hash = Column(String(64))  # SHA256 hash параметров для версионирования
    data = Column(JSONB, nullable=False, default={})  # Унифицированное JSONB поле для всех обогащений
    status = Column(String(20), nullable=False, default='ok')  # 'ok', 'partial', 'error'
    error = Column(Text)  # Текст ошибки при status='error'
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Context7: Legacy поля удалены из БД миграцией 20251117_remove_legacy
    # Все данные хранятся только в data JSONB
    # Используйте data->'tags', data->'labels', data->'ocr'->>'text' и т.д.
    
    # Relationships
    post = relationship("Post", back_populates="enrichment")
    
    # Constraints (Context7: CHECK constraints из миграций)
    __table_args__ = (
        UniqueConstraint('post_id', 'kind', name='ux_post_enrichment_post_kind'),
        CheckConstraint(
            "kind IN ('vision', 'vision_ocr', 'crawl', 'tags', 'classify', 'general')",
            name='chk_enrichment_kind'
        ),
        CheckConstraint(
            "status IN ('ok', 'partial', 'error')",
            name='chk_enrichment_status'
        ),
        CheckConstraint(
            "vision_provider IS NULL OR vision_provider IN ('gigachat', 'ocr_fallback', 'none')",
            name='chk_vision_provider'
        ),
        CheckConstraint(
            "vision_analysis_reason IS NULL OR vision_analysis_reason IN ('new', 'retry', 'cache_hit', 'fallback', 'skipped')",
            name='chk_vision_analysis_reason'
        ),
        CheckConstraint(
            "vision_tokens_used >= 0",
            name='chk_vision_tokens_used'
        ),
        Index('idx_pe_post_kind', 'post_id', 'kind'),
        Index('idx_pe_kind', 'kind', postgresql_where=text('kind IS NOT NULL')),
        Index('idx_pe_updated_at', 'updated_at', postgresql_ops={'updated_at': 'DESC'}),
        Index('idx_pe_data_gin', 'data', postgresql_using='gin'),
    )


class MediaObject(Base):
    """Централизованный реестр всех медиафайлов (content-addressed storage)."""
    __tablename__ = "media_objects"
    
    file_sha256 = Column(String(64), primary_key=True)  # SHA256 hex (64 символа)
    mime = Column(Text, nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    s3_key = Column(Text, nullable=False)
    s3_bucket = Column(Text, nullable=False, default='test-467940')
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    refs_count = Column(Integer, default=0)  # Количество ссылок на этот файл
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            "size_bytes >= 0 AND size_bytes <= 41943040",
            name='chk_media_size_bytes'
        ),  # 0-40MB
        CheckConstraint(
            "mime ~ '^(image|video|application)/'",
            name='chk_media_mime'
        ),
        Index('idx_media_mime', 'mime'),
        Index('idx_media_size', 'size_bytes'),
        Index('idx_media_refs', 'refs_count'),
        Index('idx_media_last_seen', 'last_seen_at'),
    )


class PostMediaMap(Base):
    """Связь многие-ко-многим: посты ↔ медиафайлы."""
    __tablename__ = "post_media_map"
    
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    file_sha256 = Column(String(64), ForeignKey("media_objects.file_sha256"), nullable=False)
    position = Column(Integer, default=0)
    role = Column(String(50), default='primary')  # primary | attachment | thumbnail
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    post = relationship("Post", back_populates="media_map")
    media_object = relationship("MediaObject")
    
    # Constraints
    __table_args__ = (
        PrimaryKeyConstraint('post_id', 'file_sha256'),
        CheckConstraint(
            "role IN ('primary', 'attachment', 'thumbnail')",
            name='chk_pmm_role'
        ),
        Index('idx_pmm_sha', 'file_sha256'),
        Index('idx_pmm_post', 'post_id'),
    )


class PostMedia(Base):
    """Медиа-файлы постов (legacy, для совместимости)."""
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


class GroupDigestStageArtifact(Base):
    """Артефакты стадий генерации дайджеста (оперативные + персистентные)."""
    __tablename__ = "group_digest_stage_artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    group_id = Column(UUID(as_uuid=True), nullable=True)
    window_id = Column(UUID(as_uuid=True), nullable=False)
    stage = Column(String(64), nullable=False)
    schema_version = Column(String(32), nullable=False)
    prompt_id = Column(String(128))
    prompt_version = Column(String(32))
    model_id = Column(String(128))
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            'tenant_id',
            'group_id',
            'window_id',
            'stage',
            'schema_version',
            name='ux_digest_stage_artifacts_stage',
        ),
        Index('idx_digest_stage_window', 'window_id'),
        Index('idx_digest_stage_tenant_group', 'tenant_id', 'group_id'),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id) if self.id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "group_id": str(self.group_id) if self.group_id else None,
            "window_id": str(self.window_id) if self.window_id else None,
            "stage": self.stage,
            "schema_version": self.schema_version,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "model_id": self.model_id,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
