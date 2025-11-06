"""
Trends API endpoints for accessing detected trends.
Context7: фильтрация по min_frequency, min_growth, min_engagement
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Body
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
from datetime import datetime
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from models.database import get_db, TrendDetection
from services.trend_detection_service import get_trend_detection_service
from config import settings

logger = structlog.get_logger()
router = APIRouter(prefix="/trends", tags=["trends"])

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class TrendResponse(BaseModel):
    """Ответ с информацией о тренде."""
    id: UUID
    trend_keyword: str
    frequency_count: int
    growth_rate: Optional[float]
    engagement_score: Optional[float]
    first_mentioned_at: Optional[datetime]
    last_mentioned_at: Optional[datetime]
    channels_affected: List[str]
    posts_sample: List[dict]
    detected_at: datetime
    status: str


class TrendListResponse(BaseModel):
    """Ответ со списком трендов."""
    trends: List[TrendResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# API ENDPOINTS
# ============================================================================

@router.get("/", response_model=TrendListResponse)
async def get_trends(
    min_frequency: int = Query(10, ge=1, description="Минимальная частота упоминаний"),
    min_growth: float = Query(0.0, ge=0.0, description="Минимальный рост (z-score)"),
    min_engagement: float = Query(0.0, ge=0.0, description="Минимальный engagement score"),
    status: Optional[str] = Query(None, description="Статус тренда (active, archived)"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    page_size: int = Query(20, ge=1, le=100, description="Размер страницы"),
    db: Session = Depends(get_db)
):
    """
    Получить список обнаруженных трендов с фильтрацией.
    
    Context7: Фильтрация по min_frequency, min_growth, min_engagement.
    """
    try:
        # Строим запрос с фильтрами
        query = db.query(TrendDetection)
        
        # Фильтры
        query = query.filter(TrendDetection.frequency_count >= min_frequency)
        
        if min_growth > 0:
            query = query.filter(
                or_(
                    TrendDetection.growth_rate >= min_growth,
                    TrendDetection.growth_rate.is_(None)
                )
            )
        
        if min_engagement > 0:
            query = query.filter(
                or_(
                    TrendDetection.engagement_score >= min_engagement,
                    TrendDetection.engagement_score.is_(None)
                )
            )
        
        if status:
            query = query.filter(TrendDetection.status == status)
        
        # Подсчет общего количества
        total = query.count()
        
        # Пагинация
        offset = (page - 1) * page_size
        trends = query.order_by(
            TrendDetection.detected_at.desc()
        ).offset(offset).limit(page_size).all()
        
        # Форматирование ответа
        trends_response = [
            TrendResponse(
                id=trend.id,
                trend_keyword=trend.trend_keyword,
                frequency_count=trend.frequency_count,
                growth_rate=trend.growth_rate,
                engagement_score=trend.engagement_score,
                first_mentioned_at=trend.first_mentioned_at,
                last_mentioned_at=trend.last_mentioned_at,
                channels_affected=trend.channels_affected,
                posts_sample=trend.posts_sample,
                detected_at=trend.detected_at,
                status=trend.status
            )
            for trend in trends
        ]
        
        logger.info(
            "Trends retrieved",
            total=total,
            page=page,
            filters={
                "min_frequency": min_frequency,
                "min_growth": min_growth,
                "min_engagement": min_engagement,
                "status": status
            }
        )
        
        return TrendListResponse(
            trends=trends_response,
            total=total,
            page=page,
            page_size=page_size
        )
    
    except Exception as e:
        logger.error("Error retrieving trends", error=str(e))
        raise HTTPException(status_code=500, detail="Ошибка получения трендов")


@router.get("/{trend_id}", response_model=TrendResponse)
async def get_trend(trend_id: UUID, db: Session = Depends(get_db)):
    """Получить информацию о конкретном тренде."""
    trend = db.query(TrendDetection).filter(TrendDetection.id == trend_id).first()
    
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    
    return TrendResponse(
        id=trend.id,
        trend_keyword=trend.trend_keyword,
        frequency_count=trend.frequency_count,
        growth_rate=trend.growth_rate,
        engagement_score=trend.engagement_score,
        first_mentioned_at=trend.first_mentioned_at,
        last_mentioned_at=trend.last_mentioned_at,
        channels_affected=trend.channels_affected,
        posts_sample=trend.posts_sample,
        detected_at=trend.detected_at,
        status=trend.status
    )


@router.post("/{trend_id}/archive")
async def archive_trend(trend_id: UUID, db: Session = Depends(get_db)):
    """Архивировать тренд."""
    trend = db.query(TrendDetection).filter(TrendDetection.id == trend_id).first()
    
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    
    trend.status = "archived"
    db.commit()
    db.refresh(trend)
    
    logger.info("Trend archived", trend_id=str(trend_id))
    
    return {"message": "Trend archived", "trend_id": str(trend_id)}


@router.post("/detect")
async def detect_trends_now(
    days: int = Query(7, ge=1, le=30, description="Количество дней для анализа"),
    min_frequency: int = Query(10, ge=1, description="Минимальная частота упоминаний"),
    min_growth: float = Query(0.2, ge=0.0, description="Минимальный рост"),
    min_engagement: float = Query(5.0, ge=0.0, description="Минимальный engagement score"),
    db: Session = Depends(get_db)
):
    """
    Запустить обнаружение трендов немедленно.
    
    Context7: Использует TrendDetectionService для анализа всех постов.
    """
    try:
        trend_service = get_trend_detection_service()
        
        trends = await trend_service.detect_trends(
            days=days,
            min_frequency=min_frequency,
            min_growth=min_growth,
            min_engagement=min_engagement,
            db=db
        )
        
        logger.info(
            "Trend detection completed",
            trends_count=len(trends),
            days=days
        )
        
        return {
            "message": "Trend detection completed",
            "trends_count": len(trends),
            "trends": [
                {
                    "trend_id": str(t.trend_id),
                    "keyword": t.keyword,
                    "frequency": t.frequency,
                    "engagement_score": t.engagement_score
                }
                for t in trends
            ]
        }
    
    except Exception as e:
        logger.error("Error detecting trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка обнаружения трендов: {str(e)}")


# ============================================================================
# VECTOR SEARCH ENDPOINTS
# ============================================================================

@router.get("/{trend_id}/similar", response_model=List[dict])
async def get_similar_trends(
    trend_id: UUID,
    limit: int = Query(10, ge=1, le=50, description="Максимальное количество результатов"),
    threshold: float = Query(0.7, ge=0.0, le=1.0, description="Минимальная similarity"),
    db: Session = Depends(get_db)
):
    """
    Получить похожие тренды по embedding.
    
    Context7: Использует векторный поиск через pgvector с cosine distance.
    """
    try:
        trend_service = get_trend_detection_service()
        
        similar = await trend_service.find_similar_trends(
            trend_id=trend_id,
            limit=limit,
            threshold=threshold,
            db=db
        )
        
        logger.info(
            "Similar trends retrieved",
            trend_id=str(trend_id),
            count=len(similar)
        )
        
        return similar
    
    except Exception as e:
        logger.error("Error retrieving similar trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка поиска похожих трендов: {str(e)}")


class DeduplicateRequest(BaseModel):
    """Запрос на дедупликацию трендов."""
    threshold: float = Field(0.85, ge=0.0, le=1.0, description="Минимальная similarity для дубликатов")


@router.post("/deduplicate")
async def deduplicate_trends(
    request: DeduplicateRequest = Body(...),
    db: Session = Depends(get_db)
):
    """
    Дедупликация трендов по смыслу.
    
    Context7: Находит группы похожих трендов и помечает дубликаты как archived.
    """
    try:
        trend_service = get_trend_detection_service()
        
        result = await trend_service.deduplicate_trends(
            threshold=request.threshold,
            db=db
        )
        
        logger.info(
            "Trend deduplication completed",
            duplicates_found=result.get('duplicates_found', 0),
            trends_archived=result.get('trends_archived', 0)
        )
        
        return result
    
    except Exception as e:
        logger.error("Error deduplicating trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка дедупликации трендов: {str(e)}")


class GroupRequest(BaseModel):
    """Запрос на группировку трендов."""
    trend_ids: List[UUID] = Field(..., description="Список ID трендов для группировки")
    similarity_threshold: float = Field(0.6, ge=0.0, le=1.0, description="Минимальная similarity для включения в группу")


@router.post("/group")
async def group_related_trends(
    request: GroupRequest = Body(...),
    db: Session = Depends(get_db)
):
    """
    Группировка связанных трендов через векторный поиск и Neo4j.
    
    Context7: Использует векторный поиск и Neo4j для анализа связей.
    """
    try:
        trend_service = get_trend_detection_service()
        
        groups = await trend_service.group_related_trends(
            trend_ids=request.trend_ids,
            similarity_threshold=request.similarity_threshold,
            db=db
        )
        
        logger.info(
            "Related trends grouped",
            input_count=len(request.trend_ids),
            groups_count=len(groups)
        )
        
        return {
            "groups": groups,
            "total_groups": len(groups)
        }
    
    except Exception as e:
        logger.error("Error grouping trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка группировки трендов: {str(e)}")


class ClusterRequest(BaseModel):
    """Запрос на кластеризацию трендов."""
    n_clusters: int = Field(10, ge=1, le=50, description="Желаемое количество кластеров")
    min_similarity: float = Field(0.6, ge=0.0, le=1.0, description="Минимальная similarity для включения в кластер")


@router.post("/cluster")
async def cluster_trends(
    request: ClusterRequest = Body(...),
    db: Session = Depends(get_db)
):
    """
    Кластеризация трендов по embedding.
    
    Context7: Использует векторный поиск для формирования кластеров.
    """
    try:
        trend_service = get_trend_detection_service()
        
        clusters = await trend_service.cluster_trends(
            n_clusters=request.n_clusters,
            min_similarity=request.min_similarity,
            db=db
        )
        
        logger.info(
            "Trend clustering completed",
            clusters_count=len(clusters)
        )
        
        return {
            "clusters": clusters,
            "total_clusters": len(clusters)
        }
    
    except Exception as e:
        logger.error("Error clustering trends", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ошибка кластеризации трендов: {str(e)}")

