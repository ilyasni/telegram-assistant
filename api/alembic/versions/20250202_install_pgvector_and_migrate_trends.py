"""install pgvector and migrate trends embedding

Revision ID: 20250202_install_pgvector
Revises: 20250201_user_interests
Create Date: 2025-02-02 10:00:00.000000

Context7: Установка pgvector extension, конвертация JSONB → vector(1536) для trend_embedding,
создание HNSW индекса для быстрого векторного поиска и исправление collation version mismatch.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import logging

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision = '20250202_install_pgvector'
down_revision = '20250201_user_interests'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Установка pgvector extension и миграция trend_embedding из JSONB в vector(1536).
    
    Этапы:
    1. Установка pgvector extension
    2. Конвертация JSONB → vector(1536) для существующих данных
    3. Исправление collation version mismatch
    4. Создание HNSW индекса для векторного поиска
    """
    conn = op.get_bind()
    vector_extension_exists = False
    
    # ============================================================================
    # 1. Установка pgvector extension
    # ============================================================================
    logger.info("Checking for pgvector extension...")
    
    # Проверяем наличие расширения
    savepoint_name = "check_vector_extension"
    try:
        conn.execute(sa.text(f"SAVEPOINT {savepoint_name}"))
        result = conn.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        vector_extension_exists = result.fetchone() is not None
        conn.execute(sa.text(f"RELEASE SAVEPOINT {savepoint_name}"))
        
        if vector_extension_exists:
            logger.info("pgvector extension already exists")
        else:
            logger.info("pgvector extension not found, attempting to create...")
    except Exception as e:
        try:
            conn.execute(sa.text(f"ROLLBACK TO SAVEPOINT {savepoint_name}"))
        except Exception:
            pass
        logger.warning(f"Error checking vector extension: {e}")
        vector_extension_exists = False
    
    # Пытаемся создать расширение
    if not vector_extension_exists:
        try:
            savepoint_name = "create_vector_extension"
            conn.execute(sa.text(f"SAVEPOINT {savepoint_name}"))
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(sa.text(f"RELEASE SAVEPOINT {savepoint_name}"))
            
            # Проверяем снова
            result = conn.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
            vector_extension_exists = result.fetchone() is not None
            
            if vector_extension_exists:
                logger.info("pgvector extension created successfully")
            else:
                logger.warning("pgvector extension creation failed silently")
        except Exception as e:
            try:
                conn.execute(sa.text(f"ROLLBACK TO SAVEPOINT {savepoint_name}"))
            except Exception:
                pass
            logger.warning(f"Could not create vector extension: {e}. Continuing with JSONB fallback.")
            vector_extension_exists = False
    
    # ============================================================================
    # 2. Конвертация JSONB → vector(1536) для trend_embedding
    # ============================================================================
    if vector_extension_exists:
        try:
            # Проверяем текущий тип колонки
            result = conn.execute(sa.text("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'trends_detection' 
                AND column_name = 'trend_embedding'
            """))
            column_info = result.fetchone()
            
            if column_info and column_info[0] == 'jsonb':
                logger.info("Converting trend_embedding from JSONB to vector(1536)...")
                
                # Проверяем наличие данных
                result = conn.execute(sa.text("""
                    SELECT COUNT(*) 
                    FROM trends_detection 
                    WHERE trend_embedding IS NOT NULL
                """))
                count = result.fetchone()[0]
                logger.info(f"Found {count} trends with embeddings to convert")
                
                if count > 0:
                    # Конвертируем JSONB → vector(1536)
                    # JSONB массив нужно преобразовать в формат vector
                    savepoint_name = "convert_jsonb_to_vector"
                    try:
                        conn.execute(sa.text(f"SAVEPOINT {savepoint_name}"))
                        
                        # Конвертируем через ALTER COLUMN
                        # JSONB массив [0.1, 0.2, ...] → vector(1536)
                        # Используем array_to_string для конвертации JSONB массива в строку
                        op.execute("""
                            ALTER TABLE trends_detection 
                            ALTER COLUMN trend_embedding 
                            TYPE vector(1536) 
                            USING CASE 
                                WHEN trend_embedding IS NULL THEN NULL
                                ELSE ('[' || array_to_string(ARRAY(SELECT jsonb_array_elements_text(trend_embedding)), ',') || ']')::vector(1536)
                            END
                        """)
                        
                        conn.execute(sa.text(f"RELEASE SAVEPOINT {savepoint_name}"))
                        logger.info("Successfully converted trend_embedding to vector(1536)")
                    except Exception as e:
                        try:
                            conn.execute(sa.text(f"ROLLBACK TO SAVEPOINT {savepoint_name}"))
                        except Exception:
                            pass
                        # Пробуем альтернативный способ конвертации
                        try:
                            logger.info("Trying alternative conversion method...")
                            conn.execute(sa.text(f"SAVEPOINT {savepoint_name}_alt"))
                            
                            # Альтернативный способ: через text
                            op.execute("""
                                ALTER TABLE trends_detection 
                                ALTER COLUMN trend_embedding 
                                TYPE vector(1536) 
                                USING CASE 
                                    WHEN trend_embedding IS NULL THEN NULL
                                    ELSE (trend_embedding::text)::vector(1536)
                                END
                            """)
                            
                            conn.execute(sa.text(f"RELEASE SAVEPOINT {savepoint_name}_alt"))
                            logger.info("Successfully converted using alternative method")
                        except Exception as e2:
                            try:
                                conn.execute(sa.text(f"ROLLBACK TO SAVEPOINT {savepoint_name}_alt"))
                            except Exception:
                                pass
                            logger.error(f"Error converting trend_embedding with both methods: {e}, {e2}")
                            raise
                else:
                    # Нет данных, просто меняем тип
                    logger.info("No existing embeddings, converting column type only...")
                    op.execute("""
                        ALTER TABLE trends_detection 
                        ALTER COLUMN trend_embedding 
                        TYPE vector(1536) 
                        USING NULL
                    """)
            elif column_info and column_info[0] == 'USER-DEFINED':
                # Уже vector тип, проверяем размерность
                logger.info("trend_embedding is already vector type, checking dimension...")
                result = conn.execute(sa.text("""
                    SELECT pg_typeof(trend_embedding)::text 
                    FROM trends_detection 
                    WHERE trend_embedding IS NOT NULL 
                    LIMIT 1
                """))
                type_info = result.fetchone()
                if type_info:
                    logger.info(f"Current vector type: {type_info[0]}")
            else:
                logger.info(f"trend_embedding type is {column_info[0] if column_info else 'unknown'}, skipping conversion")
        except Exception as e:
            logger.error(f"Error during trend_embedding conversion: {e}")
            # Не прерываем миграцию, продолжаем с JSONB
            vector_extension_exists = False
    
    # ============================================================================
    # 3. Исправление collation version mismatch
    # ============================================================================
    try:
        logger.info("Fixing collation version mismatch...")
        # Проверяем версию collation
        result = conn.execute(sa.text("""
            SELECT datcollate, datctype 
            FROM pg_database 
            WHERE datname = current_database()
        """))
        db_info = result.fetchone()
        if db_info:
            logger.info(f"Database collation: {db_info[0]}, ctype: {db_info[1]}")
        
        # Обновляем версию collation (не критично, может не работать в некоторых окружениях)
        try:
            op.execute("ALTER DATABASE postgres REFRESH COLLATION VERSION")
            logger.info("Collation version refreshed successfully")
        except Exception as e:
            logger.warning(f"Could not refresh collation version (non-critical): {e}")
    except Exception as e:
        logger.warning(f"Error fixing collation version mismatch (non-critical): {e}")
    
    # ============================================================================
    # 4. Создание HNSW индекса для векторного поиска
    # ============================================================================
    if vector_extension_exists:
        try:
            # Проверяем существование индекса
            result = conn.execute(sa.text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'trends_detection' 
                AND indexname = 'idx_trends_embedding_hnsw'
            """))
            index_exists = result.fetchone() is not None
            
            if not index_exists:
                logger.info("Creating HNSW index for trend_embedding...")
                
                # Создаем HNSW индекс с cosine distance
                # Context7: vector_cosine_ops для cosine similarity (лучше для нормализованных векторов)
                # Параметры: m=16 (баланс памяти/скорости), ef_construction=64 (качество индекса)
                op.execute("""
                    CREATE INDEX idx_trends_embedding_hnsw 
                    ON trends_detection 
                    USING hnsw (trend_embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """)
                logger.info("HNSW index created successfully")
            else:
                logger.info("HNSW index already exists")
        except Exception as e:
            logger.error(f"Error creating HNSW index: {e}")
            # Индекс не критичен для работы, продолжаем
    else:
        logger.warning("Skipping HNSW index creation (pgvector not available)")


def downgrade() -> None:
    """
    Откат миграции: удаление индекса и конвертация обратно в JSONB.
    """
    conn = op.get_bind()
    
    # Удаляем HNSW индекс
    try:
        op.execute("DROP INDEX IF EXISTS idx_trends_embedding_hnsw")
        logger.info("HNSW index dropped")
    except Exception as e:
        logger.warning(f"Error dropping HNSW index: {e}")
    
    # Конвертируем обратно в JSONB
    try:
        # Проверяем текущий тип
        result = conn.execute(sa.text("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name = 'trends_detection' 
            AND column_name = 'trend_embedding'
        """))
        column_info = result.fetchone()
        
        if column_info and column_info[0] == 'USER-DEFINED':
            # Конвертируем vector → JSONB
            logger.info("Converting trend_embedding from vector to JSONB...")
            
            # Конвертируем vector в JSONB массив
            op.execute("""
                ALTER TABLE trends_detection 
                ALTER COLUMN trend_embedding 
                TYPE jsonb 
                USING CASE 
                    WHEN trend_embedding IS NULL THEN NULL
                    ELSE trend_embedding::text::jsonb
                END
            """)
            logger.info("trend_embedding converted back to JSONB")
    except Exception as e:
        logger.error(f"Error converting trend_embedding back to JSONB: {e}")
    
    # Не удаляем pgvector extension при откате (может использоваться другими таблицами)

