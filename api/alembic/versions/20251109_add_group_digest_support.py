"""add group digest support

Revision ID: 20251109_add_group_digest
Revises: 20251109_add_user_crawl_triggers
Create Date: 2025-11-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20251109_add_group_digest"
down_revision = "20251109_add_user_crawl_triggers"
branch_labels = None
depends_on = None


def upgrade():
    # group_messages extensions
    op.add_column(
        "group_messages",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "group_messages",
        sa.Column("media_urls", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "group_messages",
        sa.Column("reply_to", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "group_messages",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "group_messages",
        sa.Column("has_media", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "group_messages",
        sa.Column("is_service", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "group_messages",
        sa.Column("action_type", sa.String(length=100), nullable=True),
    )

    # backfill tenant_id from groups
    op.execute(
        """
        UPDATE group_messages gm
        SET tenant_id = g.tenant_id
        FROM groups g
        WHERE gm.group_id = g.id
        """
    )
    op.alter_column("group_messages", "tenant_id", nullable=False)

    # group_message_analytics
    op.create_table(
        "group_message_analytics",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_messages.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("embeddings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("entities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("emotions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("moderation_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("analysed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )

    # group_media_map
    op.create_table(
        "group_media_map",
        sa.Column("group_message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), sa.ForeignKey("media_objects.file_sha256"), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("group_message_id", "file_sha256"),
    )
    op.create_index("idx_group_media_map_message", "group_media_map", ["group_message_id"], unique=False)
    op.create_index("idx_group_media_map_sha", "group_media_map", ["file_sha256"], unique=False)

    # group_conversation_windows
    op.create_table(
        "group_conversation_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("window_size_hours", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("participant_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dominant_emotions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("indicators", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="'pending'", nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )
    op.create_index("idx_group_conv_windows_tenant", "group_conversation_windows", ["tenant_id"], unique=False)
    op.create_index("idx_group_conv_windows_status", "group_conversation_windows", ["status"], unique=False)

    # group_digests
    op.create_table(
        "group_digests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("window_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_conversation_windows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("delivery_channel", sa.String(length=32), server_default="'telegram'", nullable=False),
        sa.Column("delivery_address", sa.String(length=255), nullable=True),
        sa.Column("format", sa.String(length=32), server_default="'markdown'", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_status", sa.String(length=32), server_default="'pending'", nullable=False),
        sa.Column("delivery_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("evaluation_scores", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("idx_group_digests_window", "group_digests", ["window_id"], unique=False)
    op.create_index("idx_group_digests_status", "group_digests", ["delivery_status"], unique=False)

    # group_digest_topics
    op.create_table(
        "group_digest_topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("digest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_digests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.String(length=16), server_default="'medium'", nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("representative_messages", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.create_index("idx_group_digest_topics_digest", "group_digest_topics", ["digest_id"], unique=False)

    # group_digest_participants
    op.create_table(
        "group_digest_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("digest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_digests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("participant_username", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=50), server_default="'participant'", nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("contribution_summary", sa.Text(), nullable=True),
    )
    op.create_index("idx_group_digest_participants_digest", "group_digest_participants", ["digest_id"], unique=False)

    # group_digest_metrics
    op.create_table(
        "group_digest_metrics",
        sa.Column("digest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("group_digests.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column("stress_index", sa.Float(), nullable=True),
        sa.Column("collaboration_index", sa.Float(), nullable=True),
        sa.Column("conflict_index", sa.Float(), nullable=True),
        sa.Column("enthusiasm_index", sa.Float(), nullable=True),
        sa.Column("raw_scores", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("group_digest_metrics")
    op.drop_index("idx_group_digest_participants_digest", table_name="group_digest_participants")
    op.drop_table("group_digest_participants")
    op.drop_index("idx_group_digest_topics_digest", table_name="group_digest_topics")
    op.drop_table("group_digest_topics")
    op.drop_index("idx_group_digests_status", table_name="group_digests")
    op.drop_index("idx_group_digests_window", table_name="group_digests")
    op.drop_table("group_digests")
    op.drop_index("idx_group_conv_windows_status", table_name="group_conversation_windows")
    op.drop_index("idx_group_conv_windows_tenant", table_name="group_conversation_windows")
    op.drop_table("group_conversation_windows")
    op.drop_index("idx_group_media_map_sha", table_name="group_media_map")
    op.drop_index("idx_group_media_map_message", table_name="group_media_map")
    op.drop_table("group_media_map")
    op.drop_table("group_message_analytics")
    op.drop_column("group_messages", "action_type")
    op.drop_column("group_messages", "is_service")
    op.drop_column("group_messages", "has_media")
    op.drop_column("group_messages", "updated_at")
    op.drop_column("group_messages", "reply_to")
    op.drop_column("group_messages", "media_urls")
    op.drop_column("group_messages", "tenant_id")

