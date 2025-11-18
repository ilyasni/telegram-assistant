"""add user_crawl_triggers table for personalized crawl tags

Revision ID: 20251109_add_user_crawl_triggers
Revises: 20251109_add_tenant_to_digest_history
Create Date: 2025-11-09 20:15:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20251109_add_user_crawl_triggers"
down_revision = "20251109_add_digest_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_crawl_triggers",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "triggers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "base_topics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "dialog_topics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "derived_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metadata_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "dialog_topics_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "idx_user_crawl_triggers_tenant_id",
        "user_crawl_triggers",
        ["tenant_id"],
    )
    op.create_index(
        "idx_user_crawl_triggers_updated_at",
        "user_crawl_triggers",
        ["updated_at"],
    )

    op.execute(
        """
        INSERT INTO user_crawl_triggers (user_id, tenant_id, base_topics, dialog_topics, derived_keywords, triggers, metadata_payload)
        SELECT
            ds.user_id,
            u.tenant_id,
            COALESCE(ds.topics, '[]'::jsonb),
            '[]'::jsonb,
            '[]'::jsonb,
            COALESCE(ds.topics, '[]'::jsonb),
            jsonb_build_object('source', 'digest_settings', 'initialized_at', now())
        FROM digest_settings ds
        JOIN users u ON u.id = ds.user_id
        WHERE jsonb_array_length(COALESCE(ds.topics, '[]'::jsonb)) > 0
        ON CONFLICT (user_id) DO UPDATE
        SET
            base_topics = EXCLUDED.base_topics,
            triggers = EXCLUDED.triggers,
            metadata_payload = EXCLUDED.metadata_payload,
            updated_at = now()
        """
    )


def downgrade() -> None:
    op.drop_index("idx_user_crawl_triggers_updated_at", table_name="user_crawl_triggers")
    op.drop_index("idx_user_crawl_triggers_tenant_id", table_name="user_crawl_triggers")
    op.drop_table("user_crawl_triggers")

