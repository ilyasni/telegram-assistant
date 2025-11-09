"""RAG API endpoints for intelligent search."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.database import get_db, User
from api.services.rag_service import get_rag_service, RAGResult, RAGSource
from api.services.enrichment_trigger_service import update_triggers_from_dialog
from fastapi import Request

logger = structlog.get_logger()
router = APIRouter(prefix="/rag", tags=["rag"])


class RAGQuery(BaseModel):
    query: str
    user_id: UUID
    limit: Optional[int] = 5
    channel_ids: Optional[List[str]] = None
    audio_file_id: Optional[str] = None
    transcription_text: Optional[str] = None
    intent_override: Optional[str] = None


class RAGResultResponse(BaseModel):
    """Response модель для RAG результата."""
    answer: str
    sources: List[dict]
    confidence: float


class RAGResponse(BaseModel):
    query: str
    result: RAGResultResponse
    processing_time_ms: int


@router.post("/query", response_model=RAGResponse)
async def rag_query(query_data: RAGQuery, request: Request, db: Session = Depends(get_db)):
    """Выполнить RAG-поиск по контенту каналов пользователя."""
    # Проверить существование пользователя
    user = db.query(User).filter(User.id == query_data.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем tenant_id из request.state или из user
    tenant_id = getattr(request.state, 'tenant_id', None) or str(user.tenant_id)
    
    # Получаем RAG Service
    rag_service = get_rag_service()
    
    # Выполняем RAG запрос
    result = await rag_service.query(
        query=query_data.query,
        user_id=query_data.user_id,
        tenant_id=tenant_id,
        db=db,
        limit=query_data.limit or 5,
        channel_ids=query_data.channel_ids,
        audio_file_id=query_data.audio_file_id,
        transcription_text=query_data.transcription_text,
        intent_override=query_data.intent_override
    )
    
    # Преобразуем источники в dict для ответа
    sources_dict = [
        {
            "post_id": source.post_id,
            "channel_id": source.channel_id,
            "channel_title": source.channel_title,
            "channel_username": source.channel_username,
            "content": source.content[:200],  # Ограничиваем длину
            "score": source.score,
            "permalink": source.permalink
        }
        for source in result.sources
    ]
    
    logger.info(
        "RAG query processed",
        user_id=str(query_data.user_id),
        query=query_data.query,
        intent=result.intent,
        sources_count=len(result.sources),
        processing_time_ms=result.processing_time_ms
    )

    try:
        update_triggers_from_dialog(db, user, query_data.query, result.intent)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning(
            "Failed to update crawl triggers from dialog",
            user_id=str(query_data.user_id),
            error=str(exc)
        )
    
    return RAGResponse(
        query=query_data.query,
        result=RAGResultResponse(
            answer=result.answer,
            sources=sources_dict,
            confidence=result.confidence
        ),
        processing_time_ms=result.processing_time_ms
    )


@router.get("/status/{user_id}")
async def get_rag_status(user_id: UUID, request: Request, db: Session = Depends(get_db)):
    """Получить статус индексации RAG для пользователя."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем tenant_id
    tenant_id = getattr(request.state, 'tenant_id', None) or str(user.tenant_id)
    
    try:
        from api.services.rag_service import get_rag_service
        
        rag_service = get_rag_service()
        
        # Проверяем наличие коллекции в Qdrant
        collection_name = f"t{tenant_id}_posts"
        collections = rag_service.qdrant_client.get_collections()
        collection_exists = collection_name in [c.name for c in collections.collections]
        
        if not collection_exists:
            return {
                "user_id": str(user_id),
                "indexed_posts": 0,
                "total_posts": 0,
                "last_indexed_at": None,
                "status": "not_indexed",
                "message": "Коллекция Qdrant не найдена. Индексация еще не начата."
            }
        
        # Получаем информацию о коллекции
        collection_info = rag_service.qdrant_client.get_collection(collection_name)
        indexed_count = collection_info.points_count if collection_info else 0
        
        # Получаем общее количество постов пользователя (только для его tenant)
        # Context7: Фильтрация по tenant_id через JOIN с user_channel
        total_posts_query = text("""
            SELECT COUNT(DISTINCT p.id)
            FROM posts p
            JOIN channels c ON p.channel_id = c.id
            JOIN user_channel uc ON uc.channel_id = c.id
            JOIN users u ON u.id = uc.user_id
            WHERE u.tenant_id = :tenant_id::uuid
        """)
        total_posts = db.execute(total_posts_query, {"tenant_id": tenant_id}).scalar() or 0
        
        # Проверяем последнюю индексацию (можно добавить поле в БД для отслеживания)
        status = "indexed" if indexed_count > 0 else "not_indexed"
        
        logger.info(
            "RAG status checked",
            user_id=str(user_id),
            tenant_id=tenant_id,
            indexed_count=indexed_count,
            total_posts=total_posts
        )
        
        return {
            "user_id": str(user_id),
            "indexed_posts": indexed_count,
            "total_posts": total_posts,
            "last_indexed_at": None,  # TODO: Добавить отслеживание времени последней индексации
            "status": status,
            "collection_name": collection_name
        }
    
    except Exception as e:
        logger.error("Error checking RAG status", user_id=str(user_id), error=str(e))
        return {
            "user_id": str(user_id),
            "indexed_posts": 0,
            "total_posts": 0,
            "last_indexed_at": None,
            "status": "error",
            "message": f"Ошибка проверки статуса: {str(e)}"
        }
