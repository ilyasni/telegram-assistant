"""add tenant_id column to digest_history (Context7 RLS alignment)

Revision ID: 20251109_add_digest_tenant
Revises: 20250202_install_pgvector
Create Date: 2025-11-09 17:30:00.000000

Context7: выравниваем схему digest_history с моделью SQLAlchemy.
- Добавляем tenant_id с FK на tenants (SET NULL при удалении).
- Заполняем существующие записи на основе users.tenant_id.
- Создаём индекс для выборок по tenant.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251109_add_digest_tenant"
down_revision = "20250202_install_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Добавляем колонку (пока nullable, чтобы выполнить backfill)
    op.add_column(
        "digest_history",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # 2. Создаём FK на tenants
    op.create_foreign_key(
        "fk_digest_history_tenant",
        "digest_history",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Бэкаповое заполнение tenant_id через users (если данные уже есть)
    op.execute(
        """
        UPDATE digest_history dh
        SET tenant_id = u.tenant_id
        FROM users u
        WHERE dh.user_id = u.id
          AND u.tenant_id IS NOT NULL
          AND (dh.tenant_id IS DISTINCT FROM u.tenant_id OR dh.tenant_id IS NULL)
        """
    )

    # 4. Индекс для выборок по tenant_id
    op.create_index(
        "idx_digest_history_tenant_id",
        "digest_history",
        ["tenant_id"],
    )


def downgrade() -> None:
    # Удаляем индекс, FK и колонку в обратном порядке
    op.drop_index("idx_digest_history_tenant_id", table_name="digest_history")
    op.drop_constraint("fk_digest_history_tenant", "digest_history", type_="foreignkey")
    op.drop_column("digest_history", "tenant_id")

