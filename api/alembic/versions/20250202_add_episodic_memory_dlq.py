"""Add episodic_memory and dlq_events tables

Revision ID: 20250202_episodic_memory_dlq
Revises: 20250131_add_digest_trends_tables
Create Date: 2025-02-02

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250202_episodic_memory_dlq'
down_revision = '20251119_merge_branches'
branch_labels = None
depends_on = None


def upgrade():
    # Create episodic_memory table
    op.create_table(
        'episodic_memory',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('event_metadata', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for episodic_memory
    op.create_index(
        'idx_episodic_memory_tenant_entity',
        'episodic_memory',
        ['tenant_id', 'entity_type', 'created_at'],
        postgresql_ops={'created_at': 'DESC'}
    )
    op.create_index(
        'idx_episodic_memory_entity',
        'episodic_memory',
        ['entity_type', 'entity_id', 'created_at'],
        postgresql_ops={'created_at': 'DESC'}
    )
    op.create_index(
        'idx_episodic_memory_event_type',
        'episodic_memory',
        ['event_type', 'created_at'],
        postgresql_ops={'created_at': 'DESC'}
    )
    op.create_index(
        'idx_episodic_memory_created_at',
        'episodic_memory',
        ['created_at']
    )
    
    # Create dlq_events table
    op.create_table(
        'dlq_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('payload', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('error_code', sa.String(100), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('stack_trace', sa.Text, nullable=True),
        sa.Column('retry_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer, nullable=False, server_default='3'),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for dlq_events
    op.create_index(
        'idx_dlq_events_tenant_status',
        'dlq_events',
        ['tenant_id', 'status', 'next_retry_at']
    )
    op.create_index(
        'idx_dlq_events_entity',
        'dlq_events',
        ['entity_type', 'entity_id']
    )
    op.create_index(
        'idx_dlq_events_status',
        'dlq_events',
        ['status', 'next_retry_at']
    )
    op.create_index(
        'idx_dlq_events_retry_count',
        'dlq_events',
        ['retry_count', 'max_attempts']
    )


def downgrade():
    # Drop indexes
    op.drop_index('idx_dlq_events_retry_count', table_name='dlq_events')
    op.drop_index('idx_dlq_events_status', table_name='dlq_events')
    op.drop_index('idx_dlq_events_entity', table_name='dlq_events')
    op.drop_index('idx_dlq_events_tenant_status', table_name='dlq_events')
    op.drop_index('idx_episodic_memory_created_at', table_name='episodic_memory')
    op.drop_index('idx_episodic_memory_event_type', table_name='episodic_memory')
    op.drop_index('idx_episodic_memory_entity', table_name='episodic_memory')
    op.drop_index('idx_episodic_memory_tenant_entity', table_name='episodic_memory')
    
    # Drop tables
    op.drop_table('dlq_events')
    op.drop_table('episodic_memory')

