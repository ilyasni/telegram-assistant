"""merge january and november migration branches

Context7 best practice: Объединение веток миграций для устранения multiple heads.
Исправляет хронологическую аномалию, где январские миграции зависели от ноябрьских.

Revision ID: 20251119_merge_branches
Revises: ('20250122_merge_heads', '20250122_migrate_vision_labels', '20251118_user_feedback')
Create Date: 2025-11-19 10:00:00.000000

Ветки:
- 20250122_merge_heads: Существующая merge migration, объединяющая 20250120_entity_metadata, 20250122_tenant_storage, 20251109_add_group_discovery
- 20250122_migrate_vision_labels: Последняя миграция в цепочке январских миграций
  (20250202_install_pgvector -> 20250121_add_source_field -> 20250122_add_pe_kind_single -> 20250122_migrate_vision_labels)
- 20251118_user_feedback: Последний head в ветке от 20251116_trend_agents
  (20251116_trend_agents -> 20251117_group_msg_unique -> 20251117_remove_legacy -> 20251118_user_feedback)
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20251119_merge_branches'
down_revision = ('20250122_merge_heads', '20250122_migrate_vision_labels', '20251118_user_feedback')
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Merge миграция - пустая, так как все изменения уже применены.
    
    Эта миграция служит для объединения истории миграций в единую последовательность.
    Все изменения уже применены в БД, поэтому эта миграция не делает никаких изменений схемы.
    """
    # Context7: Merge миграция не делает изменений схемы - все изменения уже применены
    # Это просто точка объединения истории миграций
    pass


def downgrade() -> None:
    """
    Context7: Downgrade для merge миграции также пустой.
    
    При откате нужно откатывать каждую ветку отдельно:
    - alembic downgrade 20250122_merge_heads
    - alembic downgrade 20250122_migrate_vision_labels
    - alembic downgrade 20251118_user_feedback
    """
    # Context7: При downgrade нужно откатывать каждую ветку отдельно
    pass

