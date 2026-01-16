"""SQLAlchemy модели для themes и theme_channels.
Context7: Используем декларативные модели с валидацией и индексами.
"""

from sqlalchemy import Column, String, Integer, Float, Text, ForeignKey, UniqueConstraint, CheckConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime, timezone
import uuid

from database.connection import db_connection
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class Theme(Base):
    """Модель темы из TGStat.
    
    Context7: Использует UUID для первичного ключа и timestamptz для времени.
    """
    
    __tablename__ = "themes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    channels_count = Column(Integer, default=0)
    indexed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Context7: Связь с каналами
    channels = relationship("ThemeChannel", back_populates="theme", cascade="all, delete-orphan")
    
    __table_args__ = (
        CheckConstraint('length(slug) >= 1', name='themes_slug_check'),
        CheckConstraint('length(name) >= 1', name='themes_name_check'),
        CheckConstraint('channels_count >= 0 AND channels_count <= 30', name='themes_channels_count_check'),
    )


class ThemeChannel(Base):
    """Модель канала в теме.
    
    Context7: Использует UUID для первичного ключа и внешний ключ с CASCADE.
    """
    
    __tablename__ = "theme_channels"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    theme_id = Column(UUID(as_uuid=True), ForeignKey("themes.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_username = Column(String(255), nullable=False)
    title = Column(String(500), nullable=False)
    subscribers = Column(Integer, nullable=False)
    er = Column(Float, nullable=True)
    url = Column(Text, nullable=False)
    rank_in_theme = Column(Integer, nullable=False)
    indexed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Context7: Связь с темой
    theme = relationship("Theme", back_populates="channels")
    
    __table_args__ = (
        UniqueConstraint('theme_id', 'channel_username', name='uq_theme_channel'),
        CheckConstraint('length(channel_username) >= 1', name='theme_channels_username_check'),
        CheckConstraint('length(title) >= 1', name='theme_channels_title_check'),
        CheckConstraint('subscribers >= 0', name='theme_channels_subscribers_check'),
        CheckConstraint('er IS NULL OR (er >= 0 AND er <= 100)', name='theme_channels_er_check'),
        CheckConstraint('rank_in_theme >= 1 AND rank_in_theme <= 30', name='theme_channels_rank_check'),
    )


class ThemeRepository:
    """Репозиторий для работы с темами и каналами.
    
    Context7: Реализует операции с БД с использованием транзакций и batch операций.
    """
    
    def __init__(self, session=None):
        """Инициализация репозитория.
        
        Args:
            session: SQLAlchemy Session (Context7: по умолчанию создаётся новая)
        """
        self.session = session or db_connection.get_session()
        self._own_session = session is None
    
    def __enter__(self):
        """Context manager entry.
        
        Context7: Поддержка использования в with statement.
        """
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit.
        
        Context7: Автоматическое закрытие сессии при выходе.
        Context7: Если был явный commit(), не делаем повторный commit.
        """
        if self._own_session:
            try:
                if exc_type:
                    self.session.rollback()
                else:
                    # Context7: Коммит только если не было ошибок и не был явный commit
                    # Context7: Проверяем, был ли уже commit через флаг
                    if not getattr(self, '_committed', False):
                        self.session.commit()
            except Exception as e:
                # Context7: Логируем ошибку, но не падаем
                import structlog
                logger = structlog.get_logger()
                logger.error("Error in __exit__ commit", error=str(e))
                self.session.rollback()
            finally:
                self.session.close()
    
    def commit(self):
        """Явный коммит транзакции.
        
        Context7: Позволяет явно закоммитить изменения до выхода из context manager.
        """
        if self._own_session:
            self.session.commit()
    
    def upsert_theme(self, slug: str, name: str, description: str = None) -> Theme:
        """Создание или обновление темы.
        
        Context7: Idempотентная операция - можно вызывать многократно.
        
        Args:
            slug: Slug темы
            name: Название темы
            description: Описание темы (опционально)
            
        Returns:
            Объект Theme
        """
        import structlog
        logger = structlog.get_logger()
        
        theme = self.session.query(Theme).filter(Theme.slug == slug).first()
        
        if theme:
            # Context7: Обновление существующей темы
            theme.name = name
            if description:
                theme.description = description
            logger.debug("Theme updated", slug=slug, theme_id=str(theme.id))
        else:
            # Context7: Создание новой темы
            # Context7: UUID генерируется автоматически при создании объекта
            theme = Theme(
                slug=slug,
                name=name,
                description=description
            )
            self.session.add(theme)
            # Context7: Flush для новых тем для получения ID до коммита
            # Context7: Это необходимо для проверки theme.id в sync_theme_channels
            # Context7: Выполняем flush и проверяем, что ID установлен
            self.session.flush()
            # Context7: После flush объект должен иметь ID
            if not theme.id:
                # Context7: Если ID не установлен, пробуем refresh
                self.session.refresh(theme)
            logger.info("New theme created", slug=slug, name=name, theme_id=str(theme.id))
        
        return theme
    
    def get_theme_by_slug(self, slug: str) -> Theme:
        """Получение темы по slug.
        
        Context7: Параметризованный запрос для безопасности.
        
        Args:
            slug: Slug темы
            
        Returns:
            Объект Theme или None
        """
        return self.session.query(Theme).filter(Theme.slug == slug).first()
    
    def upsert_theme_channels(
        self,
        theme_id: uuid.UUID,
        channels_data: list,
        max_channels: int = 30
    ) -> int:
        """Массовое сохранение каналов темы.
        
        Context7: Использует транзакции и batch операции для производительности.
        
        Args:
            theme_id: ID темы
            channels_data: Список словарей с данными каналов
            max_channels: Максимальное количество каналов (Context7: по умолчанию 30)
            
        Returns:
            Количество сохранённых каналов
        """
        if not channels_data:
            return 0
        
        # Context7: Ограничение до max_channels
        channels_data = channels_data[:max_channels]
        
        # Context7: Получение существующих каналов темы
        existing_channels = {
            (ch.theme_id, ch.channel_username): ch
            for ch in self.session.query(ThemeChannel)
            .filter(ThemeChannel.theme_id == theme_id)
            .all()
        }
        
        # Context7: Определение каналов для удаления (не попали в новый топ)
        existing_usernames = {ch.channel_username for ch in existing_channels.values()}
        new_usernames = {ch['channel_username'] for ch in channels_data}
        usernames_to_delete = existing_usernames - new_usernames
        
        # Context7: Удаление каналов, вышедших из топа
        if usernames_to_delete:
            self.session.query(ThemeChannel).filter(
                ThemeChannel.theme_id == theme_id,
                ThemeChannel.channel_username.in_(usernames_to_delete)
            ).delete(synchronize_session=False)
        
        # Context7: Вставка/обновление каналов
        saved_count = 0
        for rank, channel_data in enumerate(channels_data, start=1):
            username = channel_data['channel_username']
            key = (theme_id, username)
            
            if key in existing_channels:
                # Context7: Обновление существующего канала
                channel = existing_channels[key]
                channel.title = channel_data['title']
                channel.subscribers = channel_data['subscribers']
                channel.er = channel_data.get('er')
                channel.url = channel_data['url']
                channel.rank_in_theme = rank
                channel.indexed_at = datetime.now(timezone.utc)
            else:
                # Context7: Создание нового канала
                channel = ThemeChannel(
                    theme_id=theme_id,
                    channel_username=username,
                    title=channel_data['title'],
                    subscribers=channel_data['subscribers'],
                    er=channel_data.get('er'),
                    url=channel_data['url'],
                    rank_in_theme=rank
                )
                self.session.add(channel)
            
            saved_count += 1
        
        # Context7: Обновление indexed_at и channels_count для темы
        theme = self.session.query(Theme).filter(Theme.id == theme_id).first()
        if theme:
            theme.indexed_at = datetime.now(timezone.utc)
            theme.channels_count = saved_count
        
        return saved_count
    
    def delete_old_channels(self, theme_id: uuid.UUID, keep_ranks: list) -> int:
        """Удаление каналов, вышедших из топа.
        
        Context7: Используется для очистки старых данных.
        
        Args:
            theme_id: ID темы
            keep_ranks: Список рангов для сохранения
            
        Returns:
            Количество удалённых каналов
        """
        deleted = self.session.query(ThemeChannel).filter(
            ThemeChannel.theme_id == theme_id,
            ~ThemeChannel.rank_in_theme.in_(keep_ranks)
        ).delete(synchronize_session=False)
        
        return deleted
    
    def commit(self) -> None:
        """Коммит транзакции.
        
        Context7: Явный коммит для контроля транзакций.
        Context7: Устанавливаем флаг, чтобы __exit__ не делал повторный commit.
        """
        self.session.commit()
        self._committed = True
    
    def rollback(self) -> None:
        """Откат транзакции.
        
        Context7: Откат при ошибках.
        """
        self.session.rollback()
