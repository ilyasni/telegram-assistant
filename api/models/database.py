"""Модели базы данных."""

from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text, JSON, BigInteger, ForeignKey, UniqueConstraint, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.dialects.postgresql import UUID
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
    channels = relationship("Channel", back_populates="tenant")


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
    
    # Relationships
    tenant = relationship("Tenant", back_populates="users")


class Channel(Base):
    """Модель канала."""
    __tablename__ = "channels"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    telegram_id = Column(BigInteger, nullable=False)
    username = Column(String(255))
    title = Column(String(500), nullable=False)
    is_active = Column(Boolean, default=True)
    last_message_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    settings = Column(JSON, default={})
    
    # Relationships
    tenant = relationship("Tenant", back_populates="channels")
    posts = relationship("Post", back_populates="channel")


class Post(Base):
    """Модель поста."""
    __tablename__ = "posts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id"), nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    content = Column(Text)
    media_urls = Column(JSON, default=[])
    created_at = Column(DateTime, default=datetime.utcnow)
    is_processed = Column(Boolean, default=False)
    
    # Relationships
    tenant = relationship("Tenant")
    channel = relationship("Channel", back_populates="posts")
    indexing_status = relationship("IndexingStatus", back_populates="post")


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

# Relationships уже определены в классах выше
