"""add skipped status to indexing_status

[C7-ID: dev-mode-016] Context7 best practice: добавление статуса 'skipped' 
для постов без текста (только медиа, стикеры). Это семантически правильнее,
чем помечать их как 'failed', так как это ожидаемое поведение, а не ошибка.

Revision ID: 20251031_add_skipped_status
Revises: 20250128_media_vision
Create Date: 2025-10-31 16:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251031_add_skipped_status'
down_revision = '20250128_media_vision'  # Context7: Исправлено для соответствия revision ID из 20250128_add_media_registry_vision.py
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    [C7-ID: dev-mode-016] Добавление статуса 'skipped' в CHECK constraints
    для embedding_status и graph_status в таблице indexing_status.
    """
    # Удаляем старые constraints
    op.drop_constraint('indexing_status_embedding_status_check', 'indexing_status', type_='check')
    op.drop_constraint('indexing_status_graph_status_check', 'indexing_status', type_='check')
    
    # Создаем новые constraints с 'skipped'
    # Context7: Используем текст напрямую, как в других миграциях проекта
    op.execute("""
        ALTER TABLE indexing_status 
        ADD CONSTRAINT indexing_status_embedding_status_check 
        CHECK (embedding_status IN ('pending', 'processing', 'completed', 'failed', 'skipped'))
    """)
    
    op.execute("""
        ALTER TABLE indexing_status 
        ADD CONSTRAINT indexing_status_graph_status_check 
        CHECK (graph_status IN ('pending', 'processing', 'completed', 'failed', 'skipped'))
    """)
    
    # Обновляем существующие failed записи с "Post text is empty" на skipped
    op.execute("""
        UPDATE indexing_status 
        SET embedding_status = 'skipped', 
            graph_status = 'skipped', 
            error_message = 'Post text is empty - no content to index', 
            processing_completed_at = NOW() 
        WHERE embedding_status = 'failed' 
          AND graph_status = 'failed' 
          AND error_message = 'Post text is empty'
    """)


def downgrade() -> None:
    """
    Откат изменений: удаление статуса 'skipped' и возврат к старому формату.
    ВАЖНО: Посты со статусом 'skipped' будут переведены обратно в 'failed'.
    """
    # Обновляем skipped записи обратно на failed перед удалением constraint
    op.execute("""
        UPDATE indexing_status 
        SET embedding_status = 'failed', 
            graph_status = 'failed' 
        WHERE embedding_status = 'skipped' 
          AND graph_status = 'skipped'
    """)
    
    # Удаляем новые constraints
    op.drop_constraint('indexing_status_embedding_status_check', 'indexing_status', type_='check')
    op.drop_constraint('indexing_status_graph_status_check', 'indexing_status', type_='check')
    
    # Восстанавливаем старые constraints без 'skipped'
    op.execute("""
        ALTER TABLE indexing_status 
        ADD CONSTRAINT indexing_status_embedding_status_check 
        CHECK (embedding_status IN ('pending', 'processing', 'completed', 'failed'))
    """)
    
    op.execute("""
        ALTER TABLE indexing_status 
        ADD CONSTRAINT indexing_status_graph_status_check 
        CHECK (graph_status IN ('pending', 'processing', 'completed', 'failed'))
    """)
