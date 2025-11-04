"""API endpoints для управления инвайт-кодами [C7-ID: api-admin-002].

Endpoints:
- POST /api/admin/invites - создать инвайт
- GET /api/admin/invites - список инвайтов
- GET /api/admin/invites/{code} - детали инвайта
- PUT /api/admin/invites/{code} - редактировать инвайт
- DELETE /api/admin/invites/{code} - удалить инвайт
- POST /api/admin/invites/{code}/revoke - отозвать инвайт
- GET /api/admin/invites/{code}/usage - история использования инвайта
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
import uuid
import structlog
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from config import settings
from prometheus_client import Counter, Histogram
from sqlalchemy.orm import Session
from models.database import get_db, User
from dependencies.auth import get_admin_user
from middleware.tracing import get_trace_id

router = APIRouter(prefix="/api/admin", tags=["admin_invites"])
logger = structlog.get_logger()

# [C7-ID: api-admin-002] Метрики Prometheus для операций с инвайтами
INVITE_OPERATIONS_TOTAL = Counter(
    "invite_operations_total",
    "Total invite operations",
    ["operation", "status", "tenant_id"]
)
INVITE_OPERATION_DURATION = Histogram(
    "invite_operation_duration_seconds",
    "Invite operation duration",
    ["operation", "tenant_id"]
)
INVITE_AUTH_FAILURES = Counter(
    "invite_authorization_failures_total",
    "Invite authorization failures",
    ["reason", "tenant_id"]
)


class InviteCreate(BaseModel):
    """Модель для создания инвайт-кода."""
    tenant_id: str = Field(..., description="ID арендатора")
    role: str = Field("user", description="Роль пользователя (user|admin)")
    uses_limit: int = Field(1, ge=0, description="Лимит использований (0 = безлимит)")
    expires_at: Optional[datetime] = Field(None, description="Дата истечения")
    notes: Optional[str] = Field(None, description="Заметки")
    subscription_tier: Optional[str] = Field(None, description="Tier подписки, который получает пользователь при использовании инвайта")


class InviteUpdate(BaseModel):
    """Модель для обновления инвайт-кода."""
    uses_limit: Optional[int] = Field(None, ge=0, description="Лимит использований (0 = безлимит)")
    expires_at: Optional[datetime] = Field(None, description="Дата истечения")
    notes: Optional[str] = Field(None, description="Заметки")
    subscription_tier: Optional[str] = Field(None, description="Tier подписки")


class InviteResponse(BaseModel):
    """Модель ответа для инвайт-кода."""
    code: str
    tenant_id: str
    role: str
    uses_limit: int
    uses_count: int
    active: bool
    expires_at: Optional[datetime]
    created_at: datetime
    created_by: Optional[str]
    last_used_at: Optional[datetime]
    last_used_by: Optional[str]
    notes: Optional[str]


class InviteListResponse(BaseModel):
    """Модель ответа для списка инвайтов."""
    invites: List[InviteResponse]
    total: int
    limit: int
    offset: int


class InviteRevokeResponse(BaseModel):
    """Модель ответа для отзыва инвайта."""
    code: str
    status: str
    revoked_at: datetime


class InviteUsageItem(BaseModel):
    """Элемент истории использования инвайта."""
    user_id: Optional[str]
    used_at: datetime
    telegram_id: Optional[int]


class InviteUsageResponse(BaseModel):
    """История использования инвайта."""
    code: str
    usage: List[InviteUsageItem]
    total: int


def get_db_connection():
    """Получение подключения к БД."""
    try:
        conn = psycopg2.connect(settings.database_url)
        return conn
    except Exception as e:
        logger.error("Failed to connect to database", error=str(e))
        raise HTTPException(status_code=500, detail="Database connection failed")


def generate_invite_code() -> str:
    """Генерация 12-символьного инвайт-кода."""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))


def validate_role(role: str) -> bool:
    """Валидация роли пользователя."""
    return role in ["user", "admin"]


@router.post("/invites", response_model=InviteResponse)
async def create_invite(
    invite: InviteCreate,
    request: Request,
    admin_user: User = Depends(get_admin_user)
):
    """Создание нового инвайт-кода."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    if not validate_role(invite.role):
        INVITE_AUTH_FAILURES.labels(reason="invalid_role", tenant_id=tenant_id).inc()
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'user' or 'admin'")
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Генерируем уникальный код
            code = generate_invite_code()
            
            # Проверяем уникальность кода
            cursor.execute(
                "SELECT code FROM invite_codes WHERE code = %s",
                (code,)
            )
            while cursor.fetchone():
                code = generate_invite_code()
                cursor.execute(
                    "SELECT code FROM invite_codes WHERE code = %s",
                    (code,)
                )
            
            # Создаём инвайт
            cursor.execute(
                """
                INSERT INTO invite_codes 
                (code, tenant_id, role, uses_limit, expires_at, notes, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    code,
                    invite.tenant_id,
                    invite.role,
                    invite.uses_limit,
                    invite.expires_at,
                    invite.notes,
                    datetime.now(timezone.utc)
                )
            )
            
            result = cursor.fetchone()
            conn.commit()
            
            duration = time.time() - start_time
            INVITE_OPERATION_DURATION.labels(operation="create", tenant_id=tenant_id).observe(duration)
            INVITE_OPERATIONS_TOTAL.labels(operation="create", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Invite code created",
                code=code,
                tenant_id=invite.tenant_id,
                role=invite.role,
                admin_id=str(admin_user.id),
                trace_id=trace_id
            )
            
            return InviteResponse(
                code=result['code'],
                tenant_id=str(result['tenant_id']),
                role=result['role'],
                uses_limit=result['uses_limit'],
                uses_count=result['uses_count'],
                active=result['active'],
                expires_at=result['expires_at'],
                created_at=result['created_at'],
                created_by=str(result['created_by']) if result['created_by'] else None,
                last_used_at=result['last_used_at'],
                last_used_by=str(result['last_used_by']) if result['last_used_by'] else None,
                notes=result['notes']
            )
            
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        duration = time.time() - start_time
        INVITE_OPERATION_DURATION.labels(operation="create", tenant_id=tenant_id).observe(duration)
        INVITE_OPERATIONS_TOTAL.labels(operation="create", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to create invite",
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to create invite")
    finally:
        conn.close()


@router.get("/invites", response_model=InviteListResponse)
async def list_invites(
    request: Request,
    tenant_id: Optional[str] = Query(None, description="Фильтр по tenant_id"),
    status: Optional[str] = Query(None, description="Фильтр по статусу (active|revoked|expired)"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    admin_user: User = Depends(get_admin_user)
):
    """Получение списка инвайт-кодов."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    admin_tenant_id = str(admin_user.tenant_id)
    
    # Если tenant_id не указан, используем tenant_id админа
    if not tenant_id:
        tenant_id = admin_tenant_id
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Построение WHERE условия
            where_conditions = []
            params = []
            
            if tenant_id:
                where_conditions.append("tenant_id = %s")
                params.append(tenant_id)
            
            if status:
                if status == "active":
                    where_conditions.append("active = true AND (expires_at IS NULL OR expires_at > NOW())")
                elif status == "revoked":
                    where_conditions.append("active = false")
                elif status == "expired":
                    where_conditions.append("expires_at IS NOT NULL AND expires_at <= NOW()")
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            
            # Получение общего количества
            cursor.execute(f"SELECT COUNT(*) FROM invite_codes WHERE {where_clause}", params)
            total = cursor.fetchone()['count']
            
            # Получение записей
            cursor.execute(
                f"""
                SELECT * FROM invite_codes 
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset]
            )
            
            results = cursor.fetchall()
            
            invites = []
            for row in results:
                invites.append(InviteResponse(
                    code=row['code'],
                    tenant_id=str(row['tenant_id']),
                    role=row['role'],
                    uses_limit=row['uses_limit'],
                    uses_count=row['uses_count'],
                    active=row['active'],
                    expires_at=row['expires_at'],
                    created_at=row['created_at'],
                    created_by=str(row['created_by']) if row['created_by'] else None,
                    last_used_at=row['last_used_at'],
                    last_used_by=str(row['last_used_by']) if row['last_used_by'] else None,
                    notes=row['notes']
                ))
            
            duration = time.time() - start_time
            INVITE_OPERATION_DURATION.labels(operation="list", tenant_id=admin_tenant_id).observe(duration)
            INVITE_OPERATIONS_TOTAL.labels(operation="list", status="success", tenant_id=admin_tenant_id).inc()
            
            logger.info(
                "Admin listed invites",
                admin_id=str(admin_user.id),
                tenant_id=admin_tenant_id,
                total=total,
                trace_id=trace_id
            )
            
            return InviteListResponse(
                invites=invites,
                total=total,
                limit=limit,
                offset=offset
            )
            
    except Exception as e:
        duration = time.time() - start_time
        INVITE_OPERATION_DURATION.labels(operation="list", tenant_id=admin_tenant_id).observe(duration)
        INVITE_OPERATIONS_TOTAL.labels(operation="list", status="error", tenant_id=admin_tenant_id).inc()
        
        logger.error(
            "Failed to list invites",
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=admin_tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to list invites")
    finally:
        conn.close()


@router.get("/invites/{code}", response_model=InviteResponse)
async def get_invite(
    code: str,
    request: Request,
    admin_user: User = Depends(get_admin_user)
):
    """Получение информации об инвайт-коде."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM invite_codes WHERE code = %s",
                (code,)
            )
            
            result = cursor.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Invite code not found")
            
            # Проверяем статус
            now = datetime.now(timezone.utc)
            if result['expires_at'] and result['expires_at'] <= now:
                raise HTTPException(status_code=410, detail="Invite code expired")
            
            if not result['active']:
                raise HTTPException(status_code=410, detail="Invite code revoked")
            
            duration = time.time() - start_time
            INVITE_OPERATION_DURATION.labels(operation="get", tenant_id=tenant_id).observe(duration)
            INVITE_OPERATIONS_TOTAL.labels(operation="get", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Admin retrieved invite",
                code=code,
                admin_id=str(admin_user.id),
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return InviteResponse(
                code=result['code'],
                tenant_id=str(result['tenant_id']),
                role=result['role'],
                uses_limit=result['uses_limit'],
                uses_count=result['uses_count'],
                active=result['active'],
                expires_at=result['expires_at'],
                created_at=result['created_at'],
                created_by=str(result['created_by']) if result['created_by'] else None,
                last_used_at=result['last_used_at'],
                last_used_by=str(result['last_used_by']) if result['last_used_by'] else None,
                notes=result['notes']
            )
            
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        INVITE_OPERATION_DURATION.labels(operation="get", tenant_id=tenant_id).observe(duration)
        INVITE_OPERATIONS_TOTAL.labels(operation="get", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to get invite",
            code=code,
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to get invite")
    finally:
        conn.close()


@router.put("/invites/{code}", response_model=InviteResponse)
async def update_invite(
    code: str,
    invite_update: InviteUpdate,
    request: Request,
    admin_user: User = Depends(get_admin_user)
):
    """Редактирование инвайт-кода."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Проверяем существование инвайта
            cursor.execute(
                "SELECT * FROM invite_codes WHERE code = %s",
                (code,)
            )
            result = cursor.fetchone()
            
            if not result:
                INVITE_OPERATIONS_TOTAL.labels(operation="update", status="not_found", tenant_id=tenant_id).inc()
                raise HTTPException(status_code=404, detail="Invite code not found")
            
            # Подготавливаем данные для обновления
            update_fields = []
            update_values = []
            
            if invite_update.uses_limit is not None:
                update_fields.append("uses_limit = %s")
                update_values.append(invite_update.uses_limit)
            
            if invite_update.expires_at is not None:
                update_fields.append("expires_at = %s")
                update_values.append(invite_update.expires_at)
            
            if invite_update.notes is not None:
                update_fields.append("notes = %s")
                update_values.append(invite_update.notes)
            
            if invite_update.subscription_tier is not None:
                # Если в таблице есть поле subscription_tier, добавляем его
                # Иначе игнорируем (можно добавить в будущем)
                pass
            
            if not update_fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            
            update_values.append(code)
            
            # Обновляем инвайт
            cursor.execute(
                f"UPDATE invite_codes SET {', '.join(update_fields)} WHERE code = %s RETURNING *",
                update_values
            )
            
            updated_result = cursor.fetchone()
            if not updated_result:
                raise HTTPException(status_code=404, detail="Invite code not found")
            
            conn.commit()
            
            duration = time.time() - start_time
            INVITE_OPERATION_DURATION.labels(operation="update", tenant_id=tenant_id).observe(duration)
            INVITE_OPERATIONS_TOTAL.labels(operation="update", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Invite code updated",
                code=code,
                admin_id=str(admin_user.id),
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return InviteResponse(
                code=updated_result['code'],
                tenant_id=str(updated_result['tenant_id']),
                role=updated_result['role'],
                uses_limit=updated_result['uses_limit'],
                uses_count=updated_result['uses_count'],
                active=updated_result['active'],
                expires_at=updated_result['expires_at'],
                created_at=updated_result['created_at'],
                created_by=str(updated_result['created_by']) if updated_result['created_by'] else None,
                last_used_at=updated_result['last_used_at'],
                last_used_by=str(updated_result['last_used_by']) if updated_result['last_used_by'] else None,
                notes=updated_result['notes']
            )
            
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        duration = time.time() - start_time
        INVITE_OPERATION_DURATION.labels(operation="update", tenant_id=tenant_id).observe(duration)
        INVITE_OPERATIONS_TOTAL.labels(operation="update", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to update invite",
            code=code,
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to update invite")
    finally:
        conn.close()


@router.delete("/invites/{code}")
async def delete_invite(
    code: str,
    request: Request,
    admin_user: User = Depends(get_admin_user)
):
    """Удаление инвайт-кода (hard delete)."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Проверяем существование инвайта
            cursor.execute(
                "SELECT code FROM invite_codes WHERE code = %s",
                (code,)
            )
            result = cursor.fetchone()
            
            if not result:
                INVITE_OPERATIONS_TOTAL.labels(operation="delete", status="not_found", tenant_id=tenant_id).inc()
                raise HTTPException(status_code=404, detail="Invite code not found")
            
            # Удаляем инвайт
            cursor.execute(
                "DELETE FROM invite_codes WHERE code = %s",
                (code,)
            )
            
            conn.commit()
            
            duration = time.time() - start_time
            INVITE_OPERATION_DURATION.labels(operation="delete", tenant_id=tenant_id).observe(duration)
            INVITE_OPERATIONS_TOTAL.labels(operation="delete", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Invite code deleted",
                code=code,
                admin_id=str(admin_user.id),
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return {"code": code, "status": "deleted", "deleted_at": datetime.now(timezone.utc)}
            
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        duration = time.time() - start_time
        INVITE_OPERATION_DURATION.labels(operation="delete", tenant_id=tenant_id).observe(duration)
        INVITE_OPERATIONS_TOTAL.labels(operation="delete", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to delete invite",
            code=code,
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to delete invite")
    finally:
        conn.close()


@router.get("/invites/{code}/usage", response_model=InviteUsageResponse)
async def get_invite_usage(
    code: str,
    request: Request,
    admin_user: User = Depends(get_admin_user)
):
    """Получение истории использования инвайт-кода."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Проверяем существование инвайта
            cursor.execute(
                "SELECT code FROM invite_codes WHERE code = %s",
                (code,)
            )
            result = cursor.fetchone()
            
            if not result:
                INVITE_OPERATIONS_TOTAL.labels(operation="get_usage", status="not_found", tenant_id=tenant_id).inc()
                raise HTTPException(status_code=404, detail="Invite code not found")
            
            # Получаем историю использования из таблицы invite_code_usage (если существует)
            # Если таблицы нет, используем last_used_at и last_used_by из invite_codes
            # Для полноты истории может потребоваться отдельная таблица
            usage_items = []
            
            # Если есть last_used_at, добавляем его в историю
            cursor.execute(
                "SELECT last_used_at, last_used_by FROM invite_codes WHERE code = %s",
                (code,)
            )
            invite_data = cursor.fetchone()
            
            if invite_data and invite_data['last_used_at']:
                usage_items.append(InviteUsageItem(
                    user_id=str(invite_data['last_used_by']) if invite_data['last_used_by'] else None,
                    used_at=invite_data['last_used_at'],
                    telegram_id=None  # Можно получить из users если нужно
                ))
            
            duration = time.time() - start_time
            INVITE_OPERATION_DURATION.labels(operation="get_usage", tenant_id=tenant_id).observe(duration)
            INVITE_OPERATIONS_TOTAL.labels(operation="get_usage", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Retrieved invite usage",
                code=code,
                usage_count=len(usage_items),
                admin_id=str(admin_user.id),
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return InviteUsageResponse(
                code=code,
                usage=usage_items,
                total=len(usage_items)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        INVITE_OPERATION_DURATION.labels(operation="get_usage", tenant_id=tenant_id).observe(duration)
        INVITE_OPERATIONS_TOTAL.labels(operation="get_usage", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to get invite usage",
            code=code,
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to get invite usage")
    finally:
        conn.close()


@router.post("/invites/{code}/revoke", response_model=InviteRevokeResponse)
async def revoke_invite(
    code: str,
    request: Request,
    admin_user: User = Depends(get_admin_user)
):
    """Отзыв инвайт-кода."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Проверяем существование инвайта
            cursor.execute(
                "SELECT active FROM invite_codes WHERE code = %s",
                (code,)
            )
            
            result = cursor.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Invite code not found")
            
            if not result['active']:
                raise HTTPException(status_code=400, detail="Invite code already revoked")
            
            # Отзываем инвайт
            cursor.execute(
                "UPDATE invite_codes SET active = false WHERE code = %s",
                (code,)
            )
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Invite code not found")
            
            conn.commit()
            
            duration = time.time() - start_time
            INVITE_OPERATION_DURATION.labels(operation="revoke", tenant_id=tenant_id).observe(duration)
            INVITE_OPERATIONS_TOTAL.labels(operation="revoke", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Invite code revoked",
                code=code,
                admin_id=str(admin_user.id),
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return InviteRevokeResponse(
                code=code,
                status="revoked",
                revoked_at=datetime.now(timezone.utc)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        duration = time.time() - start_time
        INVITE_OPERATION_DURATION.labels(operation="revoke", tenant_id=tenant_id).observe(duration)
        INVITE_OPERATIONS_TOTAL.labels(operation="revoke", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to revoke invite",
            code=code,
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to revoke invite")
    finally:
        conn.close()
