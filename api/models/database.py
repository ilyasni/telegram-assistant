"""Модели базы данных."""

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text, JSON, BigInteger, ForeignKey, UniqueConstraint, Index, CheckConstraint, PrimaryKeyConstraint, func, text, event, REAL, Time, Date, Computed, TypeDecorator
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA
from sqlalchemy.dialects.postgresql.base import ischema_names
import json
import uuid
from datetime import datetime
from typing import Generator, Optional
from contextvars import ContextVar
from config import settings

Base = declarative_base()

# Context7: TypeDecorator для работы с pgvector типом
class VectorType(TypeDecorator):
    """TypeDecorator для работы с pgvector типом vector(n).
    
    Использует Text как базовый тип и конвертирует между list и строковым представлением vector.
    """
    impl = Text
    cache_ok = True
    
    def __init__(self, dimensions: int = 1536):
        super().__init__()
        self.dimensions = dimensions
    
    def load_dialect_impl(self, dialect):
        # Для PostgreSQL используем Text, но с явным указанием типа в SQL
        return dialect.type_descriptor(Text())
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, list):
            # Конвертируем список в строку для vector типа: '[0.1,0.2,0.3]'
            # pgvector принимает формат массива PostgreSQL
            # Используем format для правильного форматирования чисел
            vector_str = '[' + ','.join(f"{float(v):.6f}" if isinstance(v, (int, float)) else str(float(v)) for v in value) + ']'
            return vector_str
        if isinstance(value, str):
            # Уже строка, возвращаем как есть
            return value
        return value
    
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            # Парсим строку обратно в список: '[0.1,0.2,0.3]' -> [0.1, 0.2, 0.3]
            value = value.strip('[]')
            if not value:
                return None
            return [float(v.strip()) for v in value.split(',') if v.strip()]
        return value

# Context7: ContextVar для передачи request и tenant_id через dependency injection
_request_context: Optional[ContextVar] = ContextVar('request_context', default=None)

# Настройка подключения к БД
# Context7: Connection pooling для предотвращения переполнения подключений
engine = create_engine(
    settings.database_url,
    pool_size=10,  # Размер пула соединений
    max_overflow=20,  # Максимальное количество дополнительных соединений
    pool_pre_ping=True,  # Проверка соединений перед использованием
    pool_recycle=3600,  # Пересоздание соединений через час
    # Context7: Явная изоляция транзакций для предотвращения задержек
    isolation_level="READ COMMITTED",  # Уровень изоляции для немедленного отражения изменений
    connect_args={
        "connect_timeout": 10,  # Таймаут подключения
        "application_name": "telegram_api"
    }
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Dependency для получения сессии БД.
    Context7: Устанавливает app.tenant_id для RLS через ContextVar (устанавливается в RLSMiddleware).
    """
    from config import settings
    from middleware.rls_middleware import set_tenant_id_in_session
    
    db = SessionLocal()
    try:
        # Context7: Получаем tenant_id из ContextVar (устанавливается RLSMiddleware)
        if settings.feature_rls_enabled:
            try:
                request = _request_context.get(None)
                if request and hasattr(request, 'state'):
                    tenant_id = getattr(request.state, 'tenant_id', None)
                    if tenant_id:
                        set_tenant_id_in_session(db, tenant_id)
            except LookupError:
                pass  # ContextVar не установлен - это нормально для воркеров
        
        yield db
    finally:
        db.close()


# Context7: Event listener для установки app.tenant_id при начале транзакции
# (fallback если tenant_id не был установлен в get_db)
@event.listens_for(engine, "connect")
def set_tenant_id_on_connect(dbapi_conn, connection_record):
    """Context7: Устанавливает app.tenant_id при подключении (fallback)."""
    from config import settings
    if not settings.feature_rls_enabled:
        return
    
    try:
        request = _request_context.get(None)
        if request and hasattr(request, 'state'):
            tenant_id = getattr(request.state, 'tenant_id', None)
            if tenant_id:
                with dbapi_conn.cursor() as cursor:
                    cursor.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
    except Exception:
        pass


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
    # [C7-ID: db-admin-001] Роль пользователя для админ-панели
    role = Column(String(20), default="user", server_default="user")
    # Context7: Версионирование для оптимистичной блокировки (OCC)
    version = Column(Integer, default=1, server_default='1', nullable=False)
    # Context7: Автоматическое обновление timestamp при изменении записи
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    tenant = relationship("Tenant", back_populates="users")
    identity = relationship("Identity", back_populates="memberships")
    channel_subscriptions = relationship("UserChannel", back_populates="user")
    group_subscriptions = relationship("UserGroup", back_populates="user")
    # Context7: Явно указываем foreign_keys для избежания AmbiguousForeignKeysError
    # (UserAuditLog имеет два FK на users: user_id и changed_by)
    # Используем строковое имя колонки через lambda, так как класс UserAuditLog определен ниже
    audit_logs = relationship(
        "UserAuditLog",
        foreign_keys="UserAuditLog.user_id",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    feedback = relationship("UserFeedback", foreign_keys="UserFeedback.user_id", back_populates="user", cascade="all, delete-orphan")

    # Индексы/ограничения: окончательный UNIQUE(tenant_id, identity_id) накатывается миграцией
    __table_args__ = (
        Index("ix_users_tenant", "tenant_id"),
        Index("ix_users_identity", "identity_id"),
    )


class UserAuditLog(Base):
    """История изменений пользователя (tier, role) для аудита [Context7: OCC audit]."""
    __tablename__ = "user_audit_log"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(50), nullable=False)  # role_changed|tier_changed|upgraded|downgraded
    old_value = Column(String(255))
    new_value = Column(String(255))
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))  # admin user_id
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes = Column(Text)
    
    # Relationships
    user = relationship("User", back_populates="audit_logs", foreign_keys=[user_id])
    changed_by_user = relationship("User", foreign_keys=[changed_by])
    
    __table_args__ = (
        Index("ix_user_audit_log_user_id", "user_id"),
        Index("ix_user_audit_log_changed_at", "changed_at"),
        Index("ix_user_audit_log_action", "action"),
    )


class UserFeedback(Base):
    """Модель feedback от пользователей.
    
    Context7: Хранение комментариев и пожеланий пользователей с поддержкой статусов
    и multi-tenant изоляции.
    """
    __tablename__ = "user_feedback"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, server_default="pending")  # pending, in_progress, resolved, closed
    admin_notes = Column(Text, nullable=True)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="feedback")
    tenant = relationship("Tenant")
    resolver = relationship("User", foreign_keys=[resolved_by])
    
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'in_progress', 'resolved', 'closed')", name="ck_user_feedback_status"),
        Index("ix_user_feedback_user_id", "user_id"),
        Index("ix_user_feedback_tenant_id", "tenant_id"),
        Index("ix_user_feedback_status", "status"),
        Index("ix_user_feedback_created_at", "created_at"),
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


class TelegramEntity(Base):
    """Модель Telegram сущности (Context7 P1.2: entity-level metadata)."""
    __tablename__ = "tg_entities"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    peer_id = Column(BigInteger, nullable=False)  # Telegram peer ID
    peer_type = Column(String(20), nullable=False)  # 'user', 'channel', 'chat', 'supergroup'
    access_hash = Column(BigInteger, nullable=True)  # Access hash для API доступа
    username = Column(String(255), nullable=True)
    title = Column(Text, nullable=True)  # Название (для каналов/чатов)
    first_name = Column(String(255), nullable=True)  # Имя (для пользователей)
    last_name = Column(String(255), nullable=True)  # Фамилия (для пользователей)
    avatar_hash = Column(String(64), nullable=True)  # SHA256 хеш аватара
    bio = Column(Text, nullable=True)  # Описание/био
    restrictions = Column(JSONB, nullable=True)  # Ограничения доступа
    is_verified = Column(Boolean, nullable=True)
    is_premium = Column(Boolean, nullable=True)  # Premium статус (для пользователей)
    is_scam = Column(Boolean, nullable=True)
    is_fake = Column(Boolean, nullable=True)
    is_bot = Column(Boolean, nullable=True)
    is_channel = Column(Boolean, nullable=True)
    is_broadcast = Column(Boolean, nullable=True)
    is_megagroup = Column(Boolean, nullable=True)
    is_restricted = Column(Boolean, nullable=True)
    is_min = Column(Boolean, nullable=True)
    dc_id = Column(Integer, nullable=True)  # DataCenter ID
    participants_count = Column(Integer, nullable=True)
    members_count = Column(Integer, nullable=True)
    admins_count = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    entity_metadata = Column(JSONB, nullable=True)  # Дополнительные метаданные
    
    # Relationships
    admins = relationship("TelegramEntityAdmin", back_populates="entity", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint("peer_id", "peer_type", name="uq_tg_entities_peer"),
        Index("idx_tg_entities_peer_id", "peer_id"),
        Index("idx_tg_entities_peer_type", "peer_type"),
        Index("idx_tg_entities_username", "username", postgresql_where=text("username IS NOT NULL")),
        Index("idx_tg_entities_access_hash", "access_hash", postgresql_where=text("access_hash IS NOT NULL")),
        Index("idx_tg_entities_last_seen", "last_seen_at", postgresql_where=text("last_seen_at IS NOT NULL"), postgresql_ops={"last_seen_at": "DESC"}),
    )


class TelegramEntityAdmin(Base):
    """Модель администратора Telegram сущности (Context7 P1.2)."""
    __tablename__ = "tg_entity_admins"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("tg_entities.id", ondelete="CASCADE"), nullable=False)
    admin_peer_id = Column(BigInteger, nullable=False)  # Telegram ID администратора
    admin_peer_type = Column(String(20), nullable=False)  # 'user', 'bot'
    role = Column(String(50), nullable=True)  # Роль (owner, admin, moderator)
    rights = Column(JSONB, nullable=True)  # Права администратора
    rank = Column(String(255), nullable=True)  # Ранг/титул
    promoted_by = Column(BigInteger, nullable=True)  # Кто назначил администратора
    is_self = Column(Boolean, nullable=True)
    can_edit = Column(Boolean, nullable=True)
    can_delete = Column(Boolean, nullable=True)
    can_ban = Column(Boolean, nullable=True)
    can_invite = Column(Boolean, nullable=True)
    can_change_info = Column(Boolean, nullable=True)
    can_post_messages = Column(Boolean, nullable=True)  # Для каналов
    can_edit_messages = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    entity = relationship("TelegramEntity", back_populates="admins")
    
    __table_args__ = (
        UniqueConstraint("entity_id", "admin_peer_id", name="uq_tg_entity_admins_entity_admin"),
        Index("idx_tg_entity_admins_entity_id", "entity_id"),
        Index("idx_tg_entity_admins_admin_peer_id", "admin_peer_id"),
    )


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
    # Context7: engagement_score как GENERATED ALWAYS AS STORED (определяется в миграции)
    # SQLAlchemy 2.0: используем Computed() для computed columns
    engagement_score = Column(REAL, Computed("LOG(1 + COALESCE(views_count, 0)) + 2 * LOG(1 + COALESCE(reactions_count, 0)) + 3 * LOG(1 + COALESCE(forwards_count, 0)) + LOG(1 + COALESCE(replies_count, 0))", persisted=True))
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
    
    # Context7: Поле для связи постов с альбомами (Telegram grouped_id)
    grouped_id = Column(BigInteger, nullable=True, index=True)
    
    # Context7 P1.1: Быстрые поля для forwards (для прямого доступа без JOIN)
    forward_from_peer_id = Column(JSONB, nullable=True)  # Peer ID источника форварда (JSONB для гибкости)
    forward_from_chat_id = Column(BigInteger, nullable=True)  # Chat ID источника форварда (упрощённый доступ)
    forward_from_message_id = Column(BigInteger, nullable=True)  # Message ID источника форварда
    forward_date = Column(DateTime(timezone=True), nullable=True)  # Дата оригинального сообщения
    forward_from_name = Column(Text, nullable=True)  # Имя автора оригинального сообщения
    
    # Context7 P1.1: Дополнительные поля для replies (thread_id, forum_topic_id)
    thread_id = Column(BigInteger, nullable=True)  # ID треда (для каналов с комментариями)
    forum_topic_id = Column(BigInteger, nullable=True)  # ID топика форума
    
    # Context7 P3: Поле source для различения источников (channel/group/dm/persona)
    source = Column(String(20), nullable=True, server_default="channel")  # channel|group|dm|persona
    
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
    """Зашифрованные Telethon StringSession на арендатора/пользователя (старая схема для QR-авторизации)."""
    __tablename__ = "telegram_sessions_legacy"  # Переименовано для обратной совместимости

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


class TelegramSessionV2(Base):
    """Зашифрованные Telethon StringSession с multi-tenant поддержкой через identity_id (Context7 P0.1)."""
    __tablename__ = "telegram_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_id = Column(UUID(as_uuid=True), ForeignKey("identities.id", ondelete="CASCADE"), nullable=False)
    telegram_id = Column(BigInteger, nullable=False)  # Dual-write для обратной совместимости
    session_string_enc = Column(Text, nullable=False)  # Зашифрованная сессия (StringSession.save())
    s3_key = Column(String(512), nullable=True)  # Опционально: путь в S3 для .session файла
    s3_bucket = Column(String(255), nullable=True)
    dc_id = Column(Integer, nullable=False)  # DataCenter ID
    auth_key_enc = Column(BYTEA(), nullable=True)  # Опционально: зашифрованный auth_key
    session_key_id = Column(BigInteger, nullable=True)
    is_active = Column(Boolean, server_default=text("true"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)  # Для архивации неиспользуемых сессий
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    identity = relationship("Identity", backref="telegram_sessions")

    __table_args__ = (
        # Multi-tenant: UNIQUE(identity_id, dc_id) - одна Identity может иметь сессии в разных DC
        UniqueConstraint("identity_id", "dc_id", name="ux_telegram_sessions_identity_dc"),
        Index("idx_sessions_identity_id", "identity_id", postgresql_where=text("is_active = true")),
        Index("idx_sessions_telegram_id", "telegram_id", postgresql_where=text("is_active = true")),
        Index("idx_sessions_last_used", "last_used_at", postgresql_where=text("is_active = true")),
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
    
    # Context7: Агрегаты альбомов
    album_size = Column(Integer, nullable=True)  # Количество элементов в альбоме
    vision_labels_agg = Column(JSONB, nullable=True)  # Агрегированные метки vision из всех элементов альбома
    ocr_present = Column(Boolean, default=False)  # Наличие OCR текста в альбоме
    
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
            'vision_tokens_used >= 0',
            name='chk_vision_tokens_used'
        ),
        Index('idx_pe_post_kind', 'post_id', 'kind'),
        Index('idx_pe_kind', 'kind', postgresql_where=text('kind IS NOT NULL')),
        Index('idx_pe_updated_at', 'updated_at', postgresql_ops={'updated_at': 'DESC'}),
        Index('idx_pe_data_gin', 'data', postgresql_using='gin'),
    )


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


# ============================================================================
# MEDIA REGISTRY & VISION MODELS (Context7: добавлены согласно миграции 20250128_add_media_registry_vision)
# ============================================================================

class MediaObject(Base):
    """Централизованный реестр всех медиафайлов (content-addressed storage)."""
    __tablename__ = "media_objects"
    
    file_sha256 = Column(String(64), primary_key=True)  # SHA256 hex (64 символа)
    mime = Column(Text, nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    s3_key = Column(Text, nullable=False)
    s3_bucket = Column(Text, nullable=False, server_default='test-467940')
    first_seen_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, server_default=func.now(), onupdate=datetime.utcnow)
    refs_count = Column(Integer, server_default='0')  # Количество ссылок на этот файл
    
    # Relationships
    post_media_links = relationship("PostMediaMap", back_populates="media_object")
    
    # Constraints (Context7: CHECK constraints из миграции)
    __table_args__ = (
        CheckConstraint(
            'size_bytes >= 0 AND size_bytes <= 41943040',
            name='chk_media_size_bytes'
        ),  # 0-40MB (GigaChat Vision лимит)
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
    position = Column(Integer, server_default='0')
    role = Column(String(50), server_default='primary')  # primary | attachment | thumbnail
    uploaded_at = Column(DateTime, server_default=func.now())
    
    # Relationships
    post = relationship("Post", back_populates="media_map")
    media_object = relationship("MediaObject", back_populates="post_media_links")
    
    # Constraints (Context7: CHECK constraints из миграции)
    __table_args__ = (
        PrimaryKeyConstraint('post_id', 'file_sha256'),
        CheckConstraint(
            "role IN ('primary', 'attachment', 'thumbnail')",
            name='chk_pmm_role'
        ),
        Index('idx_pmm_sha', 'file_sha256'),
        Index('idx_pmm_post', 'post_id'),
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
    conversation_windows = relationship("GroupConversationWindow", back_populates="group")


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
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    tg_message_id = Column(BigInteger, nullable=False)
    sender_tg_id = Column(BigInteger)
    sender_username = Column(String(255))
    content = Column(Text)
    media_urls = Column(JSON, default=list)
    reply_to = Column(JSON, default=dict)
    posted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    has_media = Column(Boolean, default=False)
    is_service = Column(Boolean, default=False)
    action_type = Column(String(100))
    
    # Context7 P3: Поле source для различения источников (group/dm/persona)
    source = Column(String(20), nullable=True, server_default="group")  # group|dm|persona
    
    # Relationships
    group = relationship("Group", back_populates="messages")
    mentions = relationship("GroupMention", back_populates="message")
    analytics = relationship("GroupMessageAnalytics", back_populates="message", uselist=False)
    media_map = relationship("GroupMediaMap", back_populates="group_message")
    
    # Context7: UNIQUE constraint для поддержки ON CONFLICT в telegram_client.py
    __table_args__ = (
        UniqueConstraint('group_id', 'tg_message_id', name='ux_group_messages_group_tg_message'),
    )


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


class GroupMessageAnalytics(Base):
    """Аналитика по сообщений из групповых чатов."""
    __tablename__ = "group_message_analytics"

    message_id = Column(UUID(as_uuid=True), ForeignKey("group_messages.id"), primary_key=True)
    embeddings = Column(JSON, default=list)
    tags = Column(JSON, default=list)
    entities = Column(JSON, default=list)
    sentiment_score = Column(REAL)
    emotions = Column(JSON, default=dict)
    moderation_flags = Column(JSON, default=dict)
    analysed_at = Column(DateTime)
    metadata_payload = Column(JSON, default=dict)

    # Relationships
    message = relationship("GroupMessage", back_populates="analytics")


class GroupMediaMap(Base):
    """Связь групповых сообщений с медиа-объектами CAS."""
    __tablename__ = "group_media_map"

    group_message_id = Column(UUID(as_uuid=True), ForeignKey("group_messages.id", ondelete="CASCADE"), primary_key=True)
    file_sha256 = Column(String(64), ForeignKey("media_objects.file_sha256"), primary_key=True)
    position = Column(Integer, server_default="0")
    meta = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    group_message = relationship("GroupMessage", back_populates="media_map")
    media_object = relationship("MediaObject")

    __table_args__ = (
        Index("idx_group_media_map_message", "group_message_id"),
        Index("idx_group_media_map_sha", "file_sha256"),
    )


class GroupConversationWindow(Base):
    """Агрегированные окна обсуждений в группах."""
    __tablename__ = "group_conversation_windows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    window_size_hours = Column(Integer, nullable=False)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    message_count = Column(Integer, default=0)
    participant_count = Column(Integer, default=0)
    dominant_emotions = Column(JSON, default=dict)
    indicators = Column(JSON, default=dict)
    generated_at = Column(DateTime)
    status = Column(String(32), default="pending")
    failure_reason = Column(Text)

    group = relationship("Group", back_populates="conversation_windows")
    digests = relationship("GroupDigest", back_populates="window")


class GroupDigest(Base):
    """Итоговый дайджест по окну обсуждений группы."""
    __tablename__ = "group_digests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    window_id = Column(UUID(as_uuid=True), ForeignKey("group_conversation_windows.id"), nullable=False)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    delivery_channel = Column(String(32), default="telegram")
    delivery_address = Column(String(255))
    format = Column(String(32), default="markdown")
    title = Column(String(255))
    summary = Column(Text)
    payload = Column(JSON, default=dict)
    generated_at = Column(DateTime, default=datetime.utcnow)
    delivered_at = Column(DateTime)
    delivery_status = Column(String(32), default="pending")
    delivery_metadata = Column(JSON, default=dict)
    evaluation_scores = Column(JSON, default=dict)

    window = relationship("GroupConversationWindow", back_populates="digests")
    requested_by = relationship("User")
    topics = relationship("GroupDigestTopic", back_populates="digest")
    participants = relationship("GroupDigestParticipant", back_populates="digest")
    metrics = relationship("GroupDigestMetric", back_populates="digest", uselist=False)


class GroupDigestTopic(Base):
    """Тематический блок внутри группового дайджеста."""
    __tablename__ = "group_digest_topics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    digest_id = Column(UUID(as_uuid=True), ForeignKey("group_digests.id"), nullable=False)
    topic = Column(String(255), nullable=False)
    priority = Column(String(16), default="medium")
    message_count = Column(Integer, default=0)
    representative_messages = Column(JSON, default=list)
    keywords = Column(JSON, default=list)
    actions = Column(JSON, default=list)

    digest = relationship("GroupDigest", back_populates="topics")


class GroupDigestParticipant(Base):
    """Участники, попавшие в дайджест."""
    __tablename__ = "group_digest_participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    digest_id = Column(UUID(as_uuid=True), ForeignKey("group_digests.id"), nullable=False)
    participant_tg_id = Column(BigInteger)
    participant_username = Column(String(255))
    role = Column(String(50), default="participant")
    message_count = Column(Integer, default=0)
    contribution_summary = Column(Text)

    digest = relationship("GroupDigest", back_populates="participants")


class GroupDigestMetric(Base):
    """Метрики настроений и динамики обсуждения."""
    __tablename__ = "group_digest_metrics"

    digest_id = Column(UUID(as_uuid=True), ForeignKey("group_digests.id"), primary_key=True)
    sentiment = Column(REAL)
    stress_index = Column(REAL)
    collaboration_index = Column(REAL)
    conflict_index = Column(REAL)
    enthusiasm_index = Column(REAL)
    raw_scores = Column(JSON, default=dict)
    evaluated_at = Column(DateTime, default=datetime.utcnow)

    digest = relationship("GroupDigest", back_populates="metrics")


class GroupDiscoveryRequest(Base):
    """Запрос на обнаружение доступных Telegram-групп для арендатора."""
    __tablename__ = "group_discovery_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    total = Column(Integer, nullable=False, default=0)
    connected_count = Column(Integer, nullable=False, default=0)
    results = Column(JSONB, nullable=False, default=list)
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime)


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
    """Модель репоста поста (Context7 P1.1: расширено для поддержки всех полей MessageFwdHeader)."""
    __tablename__ = "post_forwards"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)
    from_chat_id = Column(BigInteger)  # ID чата, откуда репост
    from_message_id = Column(BigInteger)  # ID сообщения, откуда репост
    from_chat_title = Column(Text)  # Название чата
    from_chat_username = Column(Text)  # Username чата
    forwarded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Context7 P1.1: Дополнительные поля из MessageFwdHeader
    from_id = Column(JSONB, nullable=True)  # Peer ID источника (JSONB для гибкости: user_id/channel_id/chat_id)
    from_name = Column(Text, nullable=True)  # Имя автора (если доступно)
    post_author_signature = Column(Text, nullable=True)  # Подпись автора (для каналов)
    saved_from_peer = Column(JSONB, nullable=True)  # Peer ID источника сохранённого форварда
    saved_from_msg_id = Column(BigInteger, nullable=True)  # Message ID сохранённого форварда
    psa_type = Column(String(255), nullable=True)  # Тип публичного объявления
    
    # Relationships
    post = relationship("Post", back_populates="forwards")


class PostReply(Base):
    """Модель комментария/ответа на пост (Context7 P1.1: расширено для поддержки thread_id)."""
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
    
    # Context7 P1.1: Поддержка thread_id для каналов с комментариями
    thread_id = Column(BigInteger, nullable=True)  # ID треда (для каналов с комментариями)
    
    # Relationships
    post = relationship("Post", back_populates="replies", foreign_keys=[post_id])
    reply_to_post = relationship("Post", foreign_keys=[reply_to_post_id])


# ============================================================================
# ДАЙДЖЕСТЫ И ТРЕНДЫ (Context7: добавлены согласно миграции 20250131_digest_trends)
# ============================================================================

class DigestSettings(Base):
    """Настройки дайджестов пользователя."""
    __tablename__ = "digest_settings"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    schedule_time = Column(Time, nullable=False, server_default="12:00:00")
    schedule_tz = Column(String(255), nullable=False, default="Europe/Moscow")
    frequency = Column(String(20), nullable=False, default="daily")  # daily, weekly, monthly
    topics = Column(JSONB, nullable=False, default=[])  # Массив тематик/тегов, указанных пользователем (обязательно для генерации)
    channels_filter = Column(JSONB, nullable=True)  # Список channel_id или null (все каналы пользователя)
    max_items_per_digest = Column(Integer, nullable=False, default=10)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User")


class DigestHistory(Base):
    """История отправленных дайджестов."""
    __tablename__ = "digest_history"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    digest_date = Column(Date, nullable=False)
    content = Column(Text, nullable=False)
    posts_count = Column(Integer, nullable=False, default=0)
    topics = Column(JSONB, nullable=False, default=[])  # Тематики, по которым был сгенерирован дайджест
    sent_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, sent, failed
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User")
    
    __table_args__ = (
        Index('idx_digest_history_user_id', 'user_id'),
        Index('idx_digest_history_tenant_id', 'tenant_id'),
        Index('idx_digest_history_digest_date', 'digest_date'),
        Index('idx_digest_history_status', 'status'),
        Index('idx_digest_history_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
    )


class RAGQueryHistory(Base):
    """История запросов пользователя для анализа намерений."""
    __tablename__ = "rag_query_history"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    query_text = Column(Text, nullable=False)
    query_type = Column(String(50), nullable=False)  # ask, search, recommend, trend, digest
    intent = Column(String(50), nullable=True)  # Определенное намерение GigaChat
    confidence = Column(REAL, nullable=True)  # Уверенность классификации (0.0-1.0)
    response_text = Column(Text, nullable=True)
    sources_count = Column(Integer, nullable=True, default=0)
    processing_time_ms = Column(Integer, nullable=True)
    audio_file_id = Column(String(255), nullable=True)
    transcription_text = Column(Text, nullable=True)
    transcription_provider = Column(String(50), nullable=False, default="salutespeech")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User")
    
    __table_args__ = (
        Index('idx_rag_query_history_user_id', 'user_id'),
        Index('idx_rag_query_history_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
        Index('idx_rag_query_history_intent', 'intent'),
        Index('idx_rag_query_history_query_type', 'query_type'),
    )


class UserInterest(Base):
    """Интересы пользователей (PostgreSQL для запросов и аналитики)."""
    __tablename__ = "user_interests"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    topic = Column(Text, nullable=False, primary_key=True)
    weight = Column(REAL, nullable=False, server_default="0.0")
    query_count = Column(Integer, nullable=False, server_default="0")
    view_count = Column(Integer, nullable=False, server_default="0")
    last_updated = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Relationships
    user = relationship("User")
    
    __table_args__ = (
        Index('idx_user_interests_user_id', 'user_id'),
        Index('idx_user_interests_weight', 'user_id', 'weight', postgresql_ops={'weight': 'DESC'}),
        Index('idx_user_interests_topic', 'topic'),
        Index('idx_user_interests_last_updated', 'last_updated', postgresql_ops={'last_updated': 'DESC'}),
    )


class UserCrawlTriggers(Base):
    """Персонализированные триггеры Crawl4ai на основе пользовательских интересов."""
    __tablename__ = "user_crawl_triggers"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    triggers = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    base_topics = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    dialog_topics = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    derived_keywords = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    metadata_payload = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    dialog_topics_updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User")
    tenant = relationship("Tenant")

    __table_args__ = (
        Index("idx_user_crawl_triggers_tenant_id", "tenant_id"),
        Index("idx_user_crawl_triggers_updated_at", "updated_at"),
    )


class TrendDetection(Base):
    """Обнаруженные тренды (глобальные, без учета пользовательских настроек)."""
    __tablename__ = "trends_detection"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trend_keyword = Column(String(500), nullable=False)  # Ключевое слово/фраза
    # Context7: trend_embedding использует vector(1536) из pgvector для векторного поиска
    # TypeDecorator автоматически конвертирует между list и vector типом
    trend_embedding = Column(VectorType(dimensions=1536), nullable=True)  # Embedding для поиска похожих (GigaChat EmbeddingsGigaR, 1536 dim)
    frequency_count = Column(Integer, nullable=False, default=0)  # Количество упоминаний
    growth_rate = Column(REAL, nullable=True)  # Процент роста упоминаний
    engagement_score = Column(REAL, nullable=True)  # Средний engagement (views + reactions + forwards + replies)
    first_mentioned_at = Column(DateTime(timezone=True), nullable=True)
    last_mentioned_at = Column(DateTime(timezone=True), nullable=True)
    channels_affected = Column(JSONB, nullable=False, default=[])  # Список channel_id
    posts_sample = Column(JSONB, nullable=False, default=[])  # Примеры постов (до 10) с их engagement метриками
    detected_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status = Column(String(20), nullable=False, default="active")  # active, archived
    
    __table_args__ = (
        Index('idx_trends_detection_keyword', 'trend_keyword'),
        Index('idx_trends_detection_last_mentioned', 'last_mentioned_at', postgresql_ops={'last_mentioned_at': 'DESC'}),
        Index('idx_trends_detection_detected_at', 'detected_at', postgresql_ops={'detected_at': 'DESC'}),
        Index('idx_trends_detection_status', 'status'),
        Index('idx_trends_detection_engagement', 'engagement_score', postgresql_ops={'engagement_score': 'DESC NULLS LAST'}),
    )


class TrendAlert(Base):
    """Уведомления пользователей о трендах."""
    __tablename__ = "trend_alerts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trend_id = Column(UUID(as_uuid=True), ForeignKey("trends_detection.id", ondelete="CASCADE"), nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User")
    trend = relationship("TrendDetection")
    
    __table_args__ = (
        Index('idx_trend_alerts_user_id', 'user_id'),
        Index('idx_trend_alerts_trend_id', 'trend_id'),
        Index('idx_trend_alerts_sent_at', 'sent_at', postgresql_ops={'sent_at': 'DESC'}),
    )


class TrendCluster(Base):
    """Горячие кластеры трендов (emerging/stable)."""
    __tablename__ = "trend_clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_key = Column(String(64), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="emerging")  # emerging/stable/archived
    label = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    keywords = Column(JSONB, nullable=False, default=[])
    primary_topic = Column(String(255), nullable=True)
    novelty_score = Column(REAL, nullable=True)
    coherence_score = Column(REAL, nullable=True)
    source_diversity = Column(Integer, nullable=True)
    trend_embedding = Column(VectorType(dimensions=1536), nullable=True)
    window_start = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    window_end = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    window_mentions = Column(Integer, nullable=False, server_default="0")
    freq_baseline = Column(Integer, nullable=False, server_default="0")
    burst_score = Column(REAL, nullable=True)
    sources_count = Column(Integer, nullable=False, server_default="0")
    channels_count = Column(Integer, nullable=False, server_default="0")
    why_important = Column(Text, nullable=True)
    topics = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    card_payload = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    first_detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_activity_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    resolved_trend_id = Column(UUID(as_uuid=True), ForeignKey("trends_detection.id", ondelete="SET NULL"), nullable=True)
    # Context7: Поля для мультиагентной системы улучшения качества
    quality_score = Column(REAL, nullable=True)
    quality_flags = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    editor_notes = Column(Text, nullable=True)
    taxonomy_categories = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    last_edited_at = Column(DateTime(timezone=True), nullable=True)
    is_generic = Column(Boolean, nullable=False, server_default=text("false"))
    # Context7: Поля для иерархической кластеризации (двухуровневая структура)
    parent_cluster_id = Column(UUID(as_uuid=True), ForeignKey("trend_clusters.id", ondelete="SET NULL"), nullable=True)  # Родительский кластер (NULL для level 1)
    cluster_level = Column(Integer, nullable=False, server_default=text("1"))  # Уровень иерархии: 1 = основной топик, 2 = подтема

    resolved_trend = relationship("TrendDetection", backref="clusters")

    __table_args__ = (
        Index('idx_trend_clusters_status', 'status'),
        Index('idx_trend_clusters_last_activity', 'last_activity_at', postgresql_ops={'last_activity_at': 'DESC'}),
        Index('idx_trend_clusters_novelty', 'novelty_score', postgresql_ops={'novelty_score': 'DESC NULLS LAST'}),
        Index('idx_trend_clusters_quality_score', 'quality_score', postgresql_ops={'quality_score': 'DESC NULLS LAST'}),
        # Context7: Индексы для иерархической кластеризации
        Index('idx_trend_clusters_parent', 'parent_cluster_id'),
        Index('idx_trend_clusters_level', 'cluster_level'),
        Index('idx_trend_clusters_parent_level', 'parent_cluster_id', 'cluster_level'),
    )


class TrendClusterPost(Base):
    """Примеры постов внутри кластера тренда."""
    __tablename__ = "trend_cluster_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    channel_title = Column(Text, nullable=True)
    content_snippet = Column(Text, nullable=True)
    posted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    cluster = relationship("TrendCluster", backref="sample_posts")
    post = relationship("Post")
    channel = relationship("Channel")

    __table_args__ = (
        UniqueConstraint("cluster_id", "post_id", name="uq_trend_cluster_post"),
        Index("idx_trend_cluster_posts_cluster_time", "cluster_id", "posted_at", "created_at"),
    )


class ChatTrendSubscription(Base):
    """Подписки чатов на автоматические дайджесты трендов."""
    __tablename__ = "chat_trend_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id = Column(BigInteger, nullable=False)
    frequency = Column(String(16), nullable=False)
    topics = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("chat_id", "frequency", name="uq_trend_subscription_chat_frequency"),
        Index("idx_trend_subscription_active", "is_active"),
        Index("idx_trend_subscription_last_sent", "last_sent_at"),
    )


class TrendMetrics(Base):
    """Срез метрик по кластерам трендов (time-series baseline)."""
    __tablename__ = "trend_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False)
    freq_short = Column(Integer, nullable=False, default=0)
    freq_long = Column(Integer, nullable=False, default=0)
    freq_baseline = Column(Integer, nullable=True)
    rate_of_change = Column(REAL, nullable=True)
    burst_score = Column(REAL, nullable=True)
    ewm_score = Column(REAL, nullable=True)
    source_diversity = Column(Integer, nullable=True)
    coherence_score = Column(REAL, nullable=True)
    window_short_minutes = Column(Integer, nullable=False, default=5)
    window_long_minutes = Column(Integer, nullable=False, default=60)
    metrics_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    cluster = relationship("TrendCluster", backref="metrics")

    __table_args__ = (
        UniqueConstraint('cluster_id', 'metrics_at', name='uq_trend_metrics_cluster_snapshot'),
        Index('idx_trend_metrics_cluster', 'cluster_id'),
        Index('idx_trend_metrics_metrics_at', 'metrics_at', postgresql_ops={'metrics_at': 'DESC'}),
    )


class UserTrendProfile(Base):
    """Профили интересов пользователей для персонализации трендов.
    
    Context7: Гибридное хранение - PostgreSQL для быстрых запросов,
    синхронизация с Neo4j для графовых рекомендаций.
    """
    __tablename__ = "user_trend_profiles"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    preferred_topics = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    ignored_topics = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    preferred_categories = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    typical_time_windows = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    interaction_stats = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    last_updated = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    
    user = relationship("User", backref="trend_profile")
    
    __table_args__ = (
        Index('idx_user_trend_profiles_last_updated', 'last_updated', postgresql_ops={'last_updated': 'DESC'}),
    )


class TrendInteraction(Base):
    """Взаимодействия пользователей с трендами.
    
    Context7: Отслеживание для построения профилей интересов и оценки релевантности.
    """
    __tablename__ = "trend_interactions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("trend_clusters.id", ondelete="CASCADE"), nullable=False)
    interaction_type = Column(String(32), nullable=False)  # 'view', 'click_details', 'dismiss', 'save'
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    user = relationship("User", backref="trend_interactions")
    cluster = relationship("TrendCluster", backref="interactions")
    
    __table_args__ = (
        Index('idx_trend_interactions_user_id', 'user_id'),
        Index('idx_trend_interactions_cluster_id', 'cluster_id'),
        Index('idx_trend_interactions_type', 'interaction_type'),
        Index('idx_trend_interactions_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
        Index('idx_trend_interactions_user_cluster', 'user_id', 'cluster_id'),
    )


class TrendThresholdSuggestion(Base):
    """Предложения по оптимизации порогов трендов от Threshold Tuner Agent.
    
    Context7: Offline-анализ эффективности порогов, предложения для ручного review.
    """
    __tablename__ = "trend_threshold_suggestions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    threshold_name = Column(String(64), nullable=False)  # 'TREND_FREQ_RATIO_THRESHOLD'
    current_value = Column(REAL, nullable=False)
    suggested_value = Column(REAL, nullable=False)
    reasoning = Column(Text, nullable=True)  # Объяснение предложения
    confidence = Column(REAL, nullable=True)  # Уверенность в предложении (0.0-1.0)
    analysis_period_start = Column(DateTime(timezone=True), nullable=False)  # Начало периода анализа
    analysis_period_end = Column(DateTime(timezone=True), nullable=False)  # Конец периода анализа
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())  # Время создания предложения
    status = Column(String(32), nullable=False, default="pending")  # 'pending', 'accepted', 'rejected'
    
    __table_args__ = (
        Index('idx_trend_threshold_suggestions_status', 'status'),
        Index('idx_trend_threshold_suggestions_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
        Index('idx_trend_threshold_suggestions_threshold_name', 'threshold_name'),
    )


class EpisodicMemory(Base):
    """Episodic Memory Layer - история действий, ошибок и попыток для self-tuning.
    
    Performance guardrails:
    - Логируем только высокоуровневые события: run_started/run_completed/error/retry
    - Retention: 30-90 дней (настраивается через TTL/partitioning)
    - Индексы только по полям для чтения: tenant_id, entity_type, created_at
    """
    __tablename__ = "episodic_memory"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(50), nullable=False)  # 'digest', 'trend', 'enrichment', 'indexing', 'rag'
    entity_id = Column(UUID(as_uuid=True), nullable=True)  # ID сущности (digest_id, trend_id и т.д.)
    event_type = Column(String(50), nullable=False)  # 'run_started', 'run_completed', 'error', 'retry', 'quality_low'
    event_metadata = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))  # Детали события (metadata - зарезервированное слово в SQLAlchemy)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    
    # Relationships
    tenant = relationship("Tenant", backref="episodic_memories")
    
    __table_args__ = (
        Index('idx_episodic_memory_tenant_entity', 'tenant_id', 'entity_type', 'created_at', postgresql_ops={'created_at': 'DESC'}),
        Index('idx_episodic_memory_entity', 'entity_type', 'entity_id', 'created_at', postgresql_ops={'created_at': 'DESC'}),
        Index('idx_episodic_memory_event_type', 'event_type', 'created_at', postgresql_ops={'created_at': 'DESC'}),
        # Partitioning hint: можно добавить partition by tenant_id и created_at для больших объемов
    )


class DLQEvent(Base):
    """Dead Letter Queue - события, которые не удалось обработать после всех попыток.
    
    Performance guardrails:
    - max_attempts per event (например 3)
    - Поле next_retry_at и exponential backoff
    - Если превышено - помечаем event как permanent_failure, только ручной разбор
    """
    __tablename__ = "dlq_events"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(50), nullable=False)  # 'digest', 'trend', 'enrichment', 'indexing', 'rag'
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    event_type = Column(String(100), nullable=False)  # Тип исходного события
    payload = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    stack_trace = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="pending")  # 'pending', 'reprocessed', 'permanent_failure'
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Relationships
    tenant = relationship("Tenant", backref="dlq_events")
    
    __table_args__ = (
        Index('idx_dlq_events_tenant_status', 'tenant_id', 'status', 'next_retry_at'),
        Index('idx_dlq_events_entity', 'entity_type', 'entity_id'),
        Index('idx_dlq_events_status', 'status', 'next_retry_at'),
        Index('idx_dlq_events_retry_count', 'retry_count', 'max_attempts'),
    )
