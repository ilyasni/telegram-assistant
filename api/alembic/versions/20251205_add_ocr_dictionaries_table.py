"""add ocr_dictionaries table for automatic dictionary management

Revision ID: 20251205_ocr_dictionaries
Revises: 20251122_hierarchical_indexes
Create Date: 2025-12-05 12:00:00.000000

Context7: Добавление таблицы для автоматического управления словарями OCR по аналогии с trends.
Термины извлекаются автоматически из OCR текстов и обновляются на основе статистики.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251205_ocr_dictionaries"
down_revision = "20251122_hierarchical_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление таблицы ocr_dictionaries для автоматического управления словарями."""
    # Context7: Создание таблицы для автоматических OCR словарей
    # Аналогия с trend_clusters.keywords - автоматическое обновление на основе данных
    op.create_table(
        "ocr_dictionaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),  # politics, geography, media, organizations, etc.
        sa.Column("frequency", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("confidence", sa.REAL(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("correction_examples", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("term", "category", name="uq_ocr_dictionaries_term_category"),
    )
    
    # Context7: Индексы для быстрого поиска терминов
    op.create_index(
        "idx_ocr_dictionaries_term",
        "ocr_dictionaries",
        ["term"],
    )
    
    op.create_index(
        "idx_ocr_dictionaries_category",
        "ocr_dictionaries",
        ["category"],
    )
    
    op.create_index(
        "idx_ocr_dictionaries_frequency",
        "ocr_dictionaries",
        ["frequency"],
        postgresql_ops={"frequency": "DESC"},
    )
    
    op.create_index(
        "idx_ocr_dictionaries_last_seen",
        "ocr_dictionaries",
        ["last_seen_at"],
        postgresql_ops={"last_seen_at": "DESC"},
    )


def downgrade() -> None:
    """Удаление таблицы ocr_dictionaries."""
    op.drop_index("idx_ocr_dictionaries_last_seen", table_name="ocr_dictionaries")
    op.drop_index("idx_ocr_dictionaries_frequency", table_name="ocr_dictionaries")
    op.drop_index("idx_ocr_dictionaries_category", table_name="ocr_dictionaries")
    op.drop_index("idx_ocr_dictionaries_term", table_name="ocr_dictionaries")
    op.drop_table("ocr_dictionaries")

