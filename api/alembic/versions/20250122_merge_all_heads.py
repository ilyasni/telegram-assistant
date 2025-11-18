"""merge all head revisions

Revision ID: 20250122_merge_heads
Revises: ('20250120_entity_metadata', '20250122_tenant_storage', '20251109_add_group_discovery')
Create Date: 2025-01-22 20:00:00.000000

Context7: Объединение всех head revision миграций в единую последовательность.
Это позволяет корректно развертывать миграции без конфликтов.

Ветки:
- 20250120_entity_metadata: Entity metadata для Telethon (P1.2)
- 20250122_tenant_storage: Tenant storage usage tracking
- 20251109_add_group_discovery: Group discovery tables
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250122_merge_heads'
down_revision = ('20250120_entity_metadata', '20250122_tenant_storage', '20251109_add_group_discovery')
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Merge миграция - пустая, так как все изменения уже применены.
    
    Эта миграция служит для объединения истории миграций в единую последовательность.
    Все три head revision уже применены в БД, поэтому эта миграция не делает никаких изменений схемы.
    """
    # Context7: Merge миграция не делает изменений схемы - все изменения уже применены
    # Это просто точка объединения истории миграций
    pass


def downgrade() -> None:
    """
    Context7: Downgrade для merge миграции также пустой.
    
    При откате нужно откатывать каждую ветку отдельно:
    - alembic downgrade 20250120_entity_metadata
    - alembic downgrade 20250122_tenant_storage  
    - alembic downgrade 20251109_add_group_discovery
    """
    # Context7: При downgrade нужно откатывать каждую ветку отдельно
    pass

