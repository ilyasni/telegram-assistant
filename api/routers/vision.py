"""
Vision Analysis Results API
Context7 best practice: async endpoints, error handling, trace propagation
[C7-ID: VISION-API-001]
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import structlog

from fastapi import Request
from config import settings
import asyncpg
from contextlib import asynccontextmanager

def get_trace_id(request: Request) -> str:
    """Получение trace_id из request."""
    return getattr(request.state, 'trace_id', 'unknown')

logger = structlog.get_logger()

router = APIRouter(prefix="/vision", tags=["vision"])

# Database connection pool (asyncpg)
_db_pool = None

async def get_db_pool():
    """Получение connection pool для asyncpg."""
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(settings.database_url)
    return _db_pool

async def get_db_connection():
    """Dependency для получения connection из pool."""
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        yield conn

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class VisionClassification(BaseModel):
    """Vision classification result."""
    type: Optional[str] = None  # meme | photo | doc | infographic | screenshot | text
    confidence: Optional[float] = None
    tags: Optional[List[str]] = None
    is_meme: Optional[bool] = None
    description: Optional[str] = None


class VisionAnalysisResponse(BaseModel):
    """Vision analysis result for a post."""
    post_id: str
    analyzed_at: Optional[datetime] = None
    provider: Optional[str] = None  # gigachat | ocr | cached
    model: Optional[str] = None
    tokens_used: Optional[int] = None
    classification: Optional[VisionClassification] = None
    ocr_text: Optional[str] = None
    is_meme: Optional[bool] = None
    media_count: int = 0
    s3_vision_keys: List[str] = Field(default_factory=list)
    s3_media_keys: List[str] = Field(default_factory=list)
    trace_id: Optional[str] = None


class VisionMediaFile(BaseModel):
    """Media file info with vision analysis."""
    sha256: str
    s3_key: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    vision_classification: Optional[Dict[str, Any]] = None
    analyzed_at: Optional[datetime] = None


class VisionAnalysisListResponse(BaseModel):
    """List of vision analysis results."""
    results: List[VisionAnalysisResponse]
    total: int
    page: int = 1
    page_size: int = 50


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/posts/{post_id}", response_model=VisionAnalysisResponse)
async def get_vision_analysis(
    post_id: UUID,
    request: Request,
    db=Depends(get_db_connection)
):
    """
    Получить результаты Vision анализа для поста.
    
    Returns:
        VisionAnalysisResponse с результатами анализа или 404 если нет данных
    """
    trace_id = get_trace_id(request)
    try:
        # Получаем данные из post_enrichment
        row = await db.fetchrow("""
            SELECT 
                post_id,
                vision_classification,
                vision_description,
                vision_ocr_text,
                vision_is_meme,
                vision_provider,
                vision_model,
                vision_analyzed_at,
                vision_tokens_used,
                s3_vision_keys,
                s3_media_keys,
                (SELECT COUNT(*) FROM post_media_map WHERE post_media_map.post_id = post_enrichment.post_id) as media_count
            FROM post_enrichment
            WHERE post_id = $1
        """, post_id)
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Vision analysis not found for post {post_id}"
            )
        
        # Парсим classification
        classification = None
        if row.get('vision_classification'):
            try:
                import json
                if isinstance(row['vision_classification'], str):
                    cls_data = json.loads(row['vision_classification'])
                else:
                    cls_data = row['vision_classification']
                
                classification = VisionClassification(
                    type=cls_data.get('type'),
                    confidence=cls_data.get('confidence'),
                    tags=cls_data.get('tags'),
                    is_meme=cls_data.get('is_meme'),
                    description=row.get('vision_description')
                )
            except Exception as e:
                logger.warning("Failed to parse vision_classification", 
                             post_id=str(post_id), 
                             error=str(e),
                             trace_id=trace_id)
        
        result = VisionAnalysisResponse(
            post_id=str(row['post_id']),
            analyzed_at=row.get('vision_analyzed_at'),
            provider=row.get('vision_provider'),
            model=row.get('vision_model'),
            tokens_used=row.get('vision_tokens_used'),
            classification=classification,
            ocr_text=row.get('vision_ocr_text'),
            is_meme=row.get('vision_is_meme'),
            media_count=row.get('media_count', 0),
            s3_vision_keys=row.get('s3_vision_keys') or [],
            s3_media_keys=row.get('s3_media_keys') or [],
            trace_id=trace_id
        )
        
        logger.info("Vision analysis retrieved", 
                   post_id=str(post_id),
                   provider=result.provider,
                   trace_id=trace_id)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get vision analysis", 
                    post_id=str(post_id),
                    error=str(e),
                    trace_id=trace_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve vision analysis: {str(e)}"
        )


@router.get("/posts", response_model=VisionAnalysisListResponse)
async def list_vision_analyses(
    request: Request,
    channel_id: Optional[UUID] = Query(None, description="Filter by channel ID"),
    has_meme: Optional[bool] = Query(None, description="Filter by is_meme flag"),
    provider: Optional[str] = Query(None, description="Filter by provider (gigachat, ocr)"),
    analyzed_after: Optional[datetime] = Query(None, description="Filter by analyzed_at >= date"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Page size"),
    db=Depends(get_db_connection)
):
    """
    Список результатов Vision анализа с фильтрацией.
    
    Filters:
    - channel_id: фильтр по каналу
    - has_meme: фильтр по наличию мемов
    - provider: фильтр по провайдеру
    - analyzed_after: фильтр по дате анализа
    """
    trace_id = get_trace_id(request)
    try:
        # Базовый запрос
        base_query = """
            SELECT 
                pe.post_id,
                pe.vision_classification,
                pe.vision_description,
                pe.vision_ocr_text,
                pe.vision_is_meme,
                pe.vision_provider,
                pe.vision_model,
                pe.vision_analyzed_at,
                pe.vision_tokens_used,
                pe.s3_vision_keys,
                pe.s3_media_keys,
                (SELECT COUNT(*) FROM post_media_map WHERE post_media_map.post_id = pe.post_id) as media_count
            FROM post_enrichment pe
            WHERE pe.vision_analyzed_at IS NOT NULL
        """
        
        params = []
        param_idx = 1
        
        # Фильтры
        if channel_id:
            base_query += f" AND pe.post_id IN (SELECT id FROM posts WHERE channel_id = ${param_idx})"
            params.append(channel_id)
            param_idx += 1
        
        if has_meme is not None:
            base_query += f" AND pe.vision_is_meme = ${param_idx}"
            params.append(has_meme)
            param_idx += 1
        
        if provider:
            base_query += f" AND pe.vision_provider = ${param_idx}"
            params.append(provider)
            param_idx += 1
        
        if analyzed_after:
            base_query += f" AND pe.vision_analyzed_at >= ${param_idx}"
            params.append(analyzed_after)
            param_idx += 1
        
        # Подсчёт всего
        count_query = f"SELECT COUNT(*) as total FROM ({base_query}) subq"
        total = await db.fetchval(count_query, *params)
        
        # Пагинация
        offset = (page - 1) * page_size
        base_query += f" ORDER BY pe.vision_analyzed_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([page_size, offset])
        
        rows = await db.fetch(base_query, *params)
        
        # Преобразование результатов
        results = []
        for row in rows:
            classification = None
            if row.get('vision_classification'):
                try:
                    import json
                    if isinstance(row['vision_classification'], str):
                        cls_data = json.loads(row['vision_classification'])
                    else:
                        cls_data = row['vision_classification']
                    
                    classification = VisionClassification(
                        type=cls_data.get('type'),
                        confidence=cls_data.get('confidence'),
                        tags=cls_data.get('tags'),
                        is_meme=cls_data.get('is_meme'),
                        description=row.get('vision_description')
                    )
                except Exception:
                    pass
            
            results.append(VisionAnalysisResponse(
                post_id=str(row['post_id']),
                analyzed_at=row.get('vision_analyzed_at'),
                provider=row.get('vision_provider'),
                model=row.get('vision_model'),
                tokens_used=row.get('vision_tokens_used'),
                classification=classification,
                ocr_text=row.get('vision_ocr_text'),
                is_meme=row.get('vision_is_meme'),
                media_count=row.get('media_count', 0),
                s3_vision_keys=row.get('s3_vision_keys') or [],
                s3_media_keys=row.get('s3_media_keys') or [],
                trace_id=trace_id
            ))
        
        logger.info("Vision analyses listed", 
                   total=total,
                   page=page,
                   filters={
                       "channel_id": str(channel_id) if channel_id else None,
                       "has_meme": has_meme,
                       "provider": provider
                   },
                   trace_id=trace_id)
        
        return VisionAnalysisListResponse(
            results=results,
            total=total,
            page=page,
            page_size=page_size
        )
        
    except Exception as e:
        logger.error("Failed to list vision analyses", 
                    error=str(e),
                    trace_id=trace_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list vision analyses: {str(e)}"
        )


@router.get("/media/{sha256}")
async def get_media_vision_info(
    sha256: str,
    request: Request,
    db=Depends(get_db_connection)
):
    """
    Получить информацию о медиа файле и его Vision анализе.
    
    Returns:
        VisionMediaFile с информацией о файле и анализе
    """
    trace_id = get_trace_id(request)
    try:
        # Получаем информацию из media_objects
        row = await db.fetchrow("""
            SELECT 
                mo.file_sha256,
                mo.s3_key,
                mo.mime,
                mo.size_bytes,
                mo.refs_count,
                mo.last_seen_at,
                pe.vision_classification,
                pe.vision_analyzed_at
            FROM media_objects mo
            LEFT JOIN post_media_map pmm ON mo.file_sha256 = pmm.file_sha256
            LEFT JOIN post_enrichment pe ON pmm.post_id = pe.post_id
            WHERE mo.file_sha256 = $1
            ORDER BY pe.vision_analyzed_at DESC NULLS LAST
            LIMIT 1
        """, sha256)
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Media file {sha256} not found"
            )
        
        result = VisionMediaFile(
            sha256=row['file_sha256'],
            s3_key=row.get('s3_key'),
            mime_type=row.get('mime'),
            size_bytes=row.get('size_bytes'),
            vision_classification=row.get('vision_classification'),
            analyzed_at=row.get('vision_analyzed_at')
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get media vision info", 
                    sha256=sha256,
                    error=str(e),
                    trace_id=trace_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve media info: {str(e)}"
        )


@router.get("/stats")
async def get_vision_stats(
    request: Request,
    db=Depends(get_db_connection)
):
    """
    Статистика Vision анализа.
    
    Returns:
        Dict с статистикой: total_analyzed, by_provider, by_type, memes_count, etc.
    """
    trace_id = get_trace_id(request)
    try:
        # Общая статистика
        stats = await db.fetchrow("""
            SELECT 
                COUNT(*) as total_analyzed,
                COUNT(*) FILTER (WHERE vision_is_meme = true) as memes_count,
                COUNT(*) FILTER (WHERE vision_provider = 'gigachat') as gigachat_count,
                COUNT(*) FILTER (WHERE vision_provider = 'ocr') as ocr_count,
                SUM(vision_tokens_used) as total_tokens,
                AVG(vision_tokens_used) as avg_tokens
            FROM post_enrichment
            WHERE vision_analyzed_at IS NOT NULL
        """)
        
        # По типам (из classification JSON)
        type_stats = await db.fetch("""
            SELECT 
                vision_classification->>'type' as type,
                COUNT(*) as count
            FROM post_enrichment
            WHERE vision_classification IS NOT NULL
            GROUP BY vision_classification->>'type'
        """)
        
        return {
            "total_analyzed": stats['total_analyzed'] or 0,
            "memes_count": stats['memes_count'] or 0,
            "by_provider": {
                "gigachat": stats['gigachat_count'] or 0,
                "ocr": stats['ocr_count'] or 0,
                "cached": (stats['total_analyzed'] or 0) - (stats['gigachat_count'] or 0) - (stats['ocr_count'] or 0)
            },
            "tokens": {
                "total": int(stats['total_tokens'] or 0),
                "average": float(stats['avg_tokens'] or 0)
            },
            "by_type": {
                row['type']: row['count'] 
                for row in type_stats 
                if row['type']
            },
            "trace_id": trace_id
        }
        
    except Exception as e:
        logger.error("Failed to get vision stats", 
                    error=str(e),
                    trace_id=trace_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve stats: {str(e)}"
        )

