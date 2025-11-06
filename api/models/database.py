"""Модели базы данных."""

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text, JSON, BigInteger, ForeignKey, UniqueConstraint, Index, CheckConstraint, PrimaryKeyConstraint, func, text, event, REAL, Time, Date, Computed, TypeDecorator
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.dialects.postgresql import UUID, JSONB
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
    
    # Context7: Агрегаты альбомов
    album_size = Column(Integer, nullable=True)  # Количество элементов в альбоме
    vision_labels_agg = Column(JSONB, nullable=True)  # Агрегированные метки vision из всех элементов альбома
    ocr_present = Column(Boolean, default=False)  # Наличие OCR текста в альбоме
    
    # Legacy поля (deprecated, будут удалены после миграции)
    tags = Column(JSONB, default=[])  # DEPRECATED: использовать data->'tags'
    vision_labels = Column(JSONB, default=[])  # DEPRECATED: использовать data->'labels'
    ocr_text = Column(Text)  # DEPRECATED: использовать data->'ocr'->>'text'
    crawl_md = Column(Text)  # DEPRECATED: использовать data->>'crawl_md'
    enrichment_provider = Column(String(50))  # DEPRECATED: использовать provider
    enriched_at = Column(DateTime, default=datetime.utcnow)  # DEPRECATED: использовать created_at
    enrichment_latency_ms = Column(Integer)  # DEPRECATED: использовать data->>'latency_ms'
    enrichment_metadata = Column(JSONB, default={})  # DEPRECATED: использовать data
    summary = Column(Text)  # DEPRECATED: использовать data->>'caption' или data->>'summary'
    
    # Legacy Vision поля (deprecated)
    vision_classification = Column(JSONB)  # DEPRECATED: использовать data->'labels'
    vision_description = Column(Text)  # DEPRECATED: использовать data->>'caption'
    vision_ocr_text = Column(Text)  # DEPRECATED: использовать data->'ocr'->>'text'
    vision_is_meme = Column(Boolean, default=False)  # DEPRECATED: использовать data->>'is_meme'
    vision_context = Column(JSONB)  # DEPRECATED: использовать data->'context'
    vision_provider = Column(String(50))  # DEPRECATED: использовать provider
    vision_model = Column(String(100))  # DEPRECATED: использовать data->>'model'
    vision_analyzed_at = Column(DateTime)  # DEPRECATED: использовать created_at
    vision_file_id = Column(String(255))  # DEPRECATED: использовать data->>'file_id'
    vision_tokens_used = Column(Integer, default=0)  # DEPRECATED: использовать data->>'tokens_used'
    vision_cost_microunits = Column(Integer, default=0)  # DEPRECATED: использовать data->>'cost_microunits'
    vision_analysis_reason = Column(String(50))  # DEPRECATED: использовать data->>'analysis_reason'
    
    # Legacy S3 references (deprecated)
    s3_media_keys = Column(JSONB, default=[])  # DEPRECATED: использовать post_media_map + media_objects
    s3_vision_keys = Column(JSONB, default=[])  # DEPRECATED: использовать data->'s3_keys'
    s3_crawl_keys = Column(JSONB, default=[])  # DEPRECATED: использовать data->'s3_keys'
    
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


# ============================================================================
# ДАЙДЖЕСТЫ И ТРЕНДЫ (Context7: добавлены согласно миграции 20250131_digest_trends)
# ============================================================================

class DigestSettings(Base):
    """Настройки дайджестов пользователя."""
    __tablename__ = "digest_settings"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    schedule_time = Column(Time, nullable=False, server_default="09:00:00")
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
