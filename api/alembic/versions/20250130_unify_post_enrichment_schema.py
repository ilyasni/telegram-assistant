"""unify post_enrichment schema with kind/provider/data JSONB

Context7 best practice: единая модель post_enrichment с kind/provider/data JSONB
для модульного хранения разных видов обогащений без конфликтов.

Revision ID: 20250130_unify_enrichment
Revises: 20251031_add_skipped_status
Create Date: 2025-01-30 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250130_unify_enrichment'
down_revision = '20251031_add_skipped_status'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Унификация схемы post_enrichment на единую модель с kind/provider/data.
    
    Шаги:
    1. Добавляем новые колонки как nullable
    2. Бекфилл существующих данных в новую структуру
    3. Создаём уникальный индекс (post_id, kind)
    4. Делаем kind/provider NOT NULL
    5. Помечаем legacy поля как deprecated (удаление в следующей миграции)
    """
    
    # ============================================================================
    # ШАГ 1: Добавление новых колонок как nullable (идемпотентно)
    # ============================================================================
    
    # Context7: Проверяем существование колонок перед добавлением для идемпотентности
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('post_enrichment')]
    
    # Context7: Используем встроенную функцию encode(sha256(...), 'hex') вместо digest из pgcrypto
    
    if 'kind' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('kind', sa.Text(), nullable=True))
    
    if 'provider' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('provider', sa.Text(), nullable=True))
    
    if 'params_hash' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('params_hash', sa.Text(), nullable=True))
    
    if 'data' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('data', postgresql.JSONB(), nullable=True))
    
    if 'status' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('status', sa.Text(), nullable=True, server_default='ok'))
    
    if 'error' not in existing_columns:
        op.add_column('post_enrichment', sa.Column('error', sa.Text(), nullable=True))
    
    # ============================================================================
    # ШАГ 2: Бекфилл существующих данных
    # ============================================================================
    
    # 2.1 Vision данные → kind='vision', data = агрегация vision полей
    op.execute("""
        UPDATE post_enrichment
        SET 
            kind = 'vision',
            provider = COALESCE(vision_provider, 'unknown'),
            status = CASE 
                WHEN vision_analysis_reason = 'error' THEN 'error'
                WHEN vision_analysis_reason = 'skipped' THEN 'partial'
                ELSE 'ok'
            END,
            data = jsonb_build_object(
                'model', COALESCE(vision_model, 'unknown'),
                'model_version', NULL,
                'provider', COALESCE(vision_provider, 'unknown'),
                'analyzed_at', vision_analyzed_at,
                'labels', COALESCE(vision_classification, '[]'::jsonb),
                'caption', vision_description,
                'ocr', jsonb_build_object(
                    'text', vision_ocr_text,
                    'engine', CASE WHEN vision_provider = 'ocr_fallback' THEN 'tesseract' ELSE NULL END
                ),
                'is_meme', COALESCE(vision_is_meme, false),
                'context', COALESCE(vision_context, '{}'::jsonb),
                'file_id', vision_file_id,
                'tokens_used', COALESCE(vision_tokens_used, 0),
                'cost_microunits', COALESCE(vision_cost_microunits, 0),
                'analysis_reason', vision_analysis_reason,
                's3_keys', COALESCE(s3_vision_keys, '[]'::jsonb)
            )
        WHERE (
            vision_classification IS NOT NULL 
            OR vision_description IS NOT NULL 
            OR vision_ocr_text IS NOT NULL
            OR vision_context IS NOT NULL
            OR vision_provider IS NOT NULL
        )
        AND kind IS NULL
    """)
    
    # 2.2 Crawl данные → kind='crawl', data = crawl_md + metadata
    op.execute("""
        UPDATE post_enrichment
        SET 
            kind = 'crawl',
            provider = COALESCE(enrichment_provider, 'crawl4ai'),
            status = 'ok',
            data = jsonb_build_object(
                'crawl_md', crawl_md,
                'urls', COALESCE(metadata->'source_urls', '[]'::jsonb),
                'word_count', COALESCE(metadata->>'total_word_count', '0')::int,
                'crawled_at', COALESCE(metadata->>'crawled_at', enriched_at::text),
                'latency_ms', COALESCE(enrichment_latency_ms, 0),
                's3_keys', COALESCE(s3_crawl_keys, '[]'::jsonb),
                'metadata', COALESCE(metadata, '{}'::jsonb)
            )
        WHERE crawl_md IS NOT NULL
        AND kind IS NULL
    """)
    
    # 2.3 Tags → kind='tags', data = {"tags": [...]}
    # Context7: tags имеет тип text[] в БД, используем to_jsonb для конвертации
    # Используем существующие колонки: enrichment_provider, enrichment_latency_ms, metadata
    op.execute("""
        UPDATE post_enrichment
        SET 
            kind = 'tags',
            provider = COALESCE(enrichment_provider, 'gigachat'),
            status = 'ok',
            data = jsonb_build_object(
                'tags', COALESCE(to_jsonb(tags), '[]'::jsonb),
                'provider', COALESCE(enrichment_provider, 'gigachat'),
                'latency_ms', COALESCE(enrichment_latency_ms, 0),
                'metadata', COALESCE(metadata, '{}'::jsonb)
            )
        WHERE tags IS NOT NULL 
        AND array_length(tags, 1) > 0
        AND kind IS NULL
    """)
    
    # 2.4 Остальные записи → kind='general' (fallback)
    # Используем существующие колонки: metadata (не enrichment_metadata)
    op.execute("""
        UPDATE post_enrichment
        SET 
            kind = 'general',
            provider = COALESCE(enrichment_provider, 'unknown'),
            status = 'ok',
            data = jsonb_build_object(
                'legacy_data', jsonb_build_object(
                    'ocr_text', ocr_text,
                    'vision_labels', vision_labels,
                    'enriched_at', enriched_at,
                    'metadata', COALESCE(metadata, '{}'::jsonb)
                )
            )
        WHERE kind IS NULL
    """)
    
    # 2.5 Генерация params_hash для Vision (на основе model + provider)
    # Context7: Используем md5 для простоты (можно перейти на sha256 через расширение позже)
    op.execute("""
        UPDATE post_enrichment
        SET params_hash = md5(
            COALESCE(vision_model, '') || '|' || COALESCE(vision_provider, '')
        )
        WHERE kind = 'vision' 
        AND params_hash IS NULL
        AND vision_model IS NOT NULL
    """)
    
    # ============================================================================
    # ШАГ 3: Создание уникального индекса (post_id, kind)
    # ============================================================================
    
    # Context7: Проверяем существование constraint перед созданием
    # Проверяем как constraint, так и индекс (может быть создан как UNIQUE INDEX)
    existing_constraints = [c['name'] for c in inspector.get_unique_constraints('post_enrichment')]
    existing_indexes = [idx['name'] for idx in inspector.get_indexes('post_enrichment')]
    
    if 'ux_post_enrichment_post_kind' not in existing_constraints and 'ux_post_enrichment_post_kind' not in existing_indexes:
        # Проверяем, нет ли дублей перед созданием уникального индекса
        # Если есть дубли - оставляем только последнюю запись
        op.execute("""
            DELETE FROM post_enrichment pe1
            USING post_enrichment pe2
            WHERE pe1.post_id = pe2.post_id
            AND pe1.kind = pe2.kind
            AND pe1.updated_at < pe2.updated_at
        """)
        
        # Создаём уникальный constraint
        op.create_unique_constraint(
            'ux_post_enrichment_post_kind',
            'post_enrichment',
            ['post_id', 'kind']
        )
    
    # ============================================================================
    # ШАГ 4: Делаем kind/provider NOT NULL после бекфилла
    # ============================================================================
    
    # Context7: Сначала заполняем NULL значения перед установкой NOT NULL
    op.execute("""
        UPDATE post_enrichment 
        SET provider = COALESCE(provider, 'unknown')
        WHERE provider IS NULL
    """)
    
    op.execute("""
        UPDATE post_enrichment 
        SET status = COALESCE(status, 'ok')
        WHERE status IS NULL
    """)
    
    op.execute("""
        UPDATE post_enrichment 
        SET data = COALESCE(data, '{}'::jsonb)
        WHERE data IS NULL
    """)
    
    # Теперь устанавливаем NOT NULL
    op.alter_column('post_enrichment', 'kind', nullable=False)
    op.alter_column('post_enrichment', 'provider', nullable=False, server_default='unknown')
    op.alter_column('post_enrichment', 'status', nullable=False, server_default='ok')
    op.alter_column('post_enrichment', 'data', nullable=False, server_default='{}')
    
    # Удаляем server_default после установки значений
    op.alter_column('post_enrichment', 'provider', server_default=None)
    op.alter_column('post_enrichment', 'status', server_default=None)
    op.alter_column('post_enrichment', 'data', server_default=None)
    
    # ============================================================================
    # ШАГ 5: Добавление индексов для производительности (идемпотентно)
    # ============================================================================
    
    # Context7: Проверяем существование индексов через прямой SQL запрос
    # Используем pg_indexes, так как inspector может не находить все индексы
    result = conn.execute(sa.text("""
        SELECT indexname FROM pg_indexes 
        WHERE schemaname = 'public' AND tablename = 'post_enrichment'
    """))
    existing_index_names = [row[0] for row in result]
    
    if 'idx_post_enrichment_post_kind' not in existing_index_names:
        op.create_index(
            'idx_post_enrichment_post_kind',
            'post_enrichment',
            ['post_id', 'kind']
        )
    
    if 'idx_post_enrichment_kind' not in existing_index_names:
        op.create_index(
            'idx_post_enrichment_kind',
            'post_enrichment',
            ['kind'],
            postgresql_where=sa.text('kind IS NOT NULL')
        )
    
    if 'idx_post_enrichment_updated_at' not in existing_index_names:
        op.create_index(
            'idx_post_enrichment_updated_at',
            'post_enrichment',
            ['updated_at'],
            postgresql_ops={'updated_at': 'DESC'}
        )
    
    # GIN индекс для JSONB поля data (для частых фильтраций)
    if 'idx_post_enrichment_data_gin' not in existing_index_names:
        op.execute("""
            CREATE INDEX idx_post_enrichment_data_gin 
            ON post_enrichment USING GIN (data)
        """)
    
    # ============================================================================
    # ШАГ 6: CHECK constraints для валидации (идемпотентно)
    # ============================================================================
    
    # Context7: Проверяем существование constraints через прямой SQL запрос
    result = conn.execute(sa.text("""
        SELECT conname FROM pg_constraint 
        WHERE conrelid = 'post_enrichment'::regclass 
        AND contype = 'c'
    """))
    existing_check_constraints = [row[0] for row in result]
    
    if 'chk_enrichment_status' not in existing_check_constraints:
        op.create_check_constraint(
            'chk_enrichment_status',
            'post_enrichment',
            "status IN ('ok', 'partial', 'error')"
        )
    
    if 'chk_enrichment_kind' not in existing_check_constraints:
        op.create_check_constraint(
            'chk_enrichment_kind',
            'post_enrichment',
            "kind IN ('vision', 'vision_ocr', 'crawl', 'tags', 'classify', 'general')"
        )


def downgrade() -> None:
    """
    Откат миграции: удаление новых колонок и индексов.
    ВАЖНО: Данные в legacy полях будут восстановлены из data JSONB если возможно.
    """
    
    # Удаляем constraints
    op.drop_constraint('chk_enrichment_kind', 'post_enrichment')
    op.drop_constraint('chk_enrichment_status', 'post_enrichment')
    
    # Удаляем индексы
    op.execute("DROP INDEX IF EXISTS idx_post_enrichment_data_gin")
    op.drop_index('idx_post_enrichment_updated_at', 'post_enrichment')
    op.drop_index('idx_post_enrichment_kind', 'post_enrichment')
    op.drop_index('idx_post_enrichment_post_kind', 'post_enrichment')
    
    # Удаляем уникальный constraint
    op.drop_constraint('ux_post_enrichment_post_kind', 'post_enrichment')
    
    # Восстанавливаем данные из data JSONB в legacy поля (если возможно)
    op.execute("""
        UPDATE post_enrichment
        SET 
            vision_ocr_text = COALESCE(data->'ocr'->>'text', vision_ocr_text),
            vision_description = COALESCE(data->>'caption', vision_description),
            vision_classification = COALESCE(data->'labels', vision_classification::jsonb),
            vision_context = COALESCE(data->'context', vision_context::jsonb),
            crawl_md = COALESCE(data->>'crawl_md', crawl_md),
            tags = COALESCE((data->'tags')::text[], tags)
        WHERE data IS NOT NULL
    """)
    
    # Удаляем колонки
    op.drop_column('post_enrichment', 'error')
    op.drop_column('post_enrichment', 'status')
    op.drop_column('post_enrichment', 'data')
    op.drop_column('post_enrichment', 'params_hash')
    op.drop_column('post_enrichment', 'provider')
    op.drop_column('post_enrichment', 'kind')

