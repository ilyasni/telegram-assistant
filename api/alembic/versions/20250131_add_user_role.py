"""add user role field for admin panel [C7-ID: db-admin-001]

Revision ID: 20250131_add_user_role
Revises: 20251103_enable_rls
Create Date: 2025-01-31
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250131_add_user_role'
down_revision = '20251103_remove_telegram_id_uniq'  # Зависит от последней применённой миграции
branch_labels = None
depends_on = None


def upgrade() -> None:
    # [C7-ID: db-admin-001] Добавляем поле role в таблицу users
    op.add_column('users', sa.Column('role', sa.String(length=20), server_default='user', nullable=False))


def downgrade() -> None:
    # Удаляем поле role из таблицы users
    op.drop_column('users', 'role')

