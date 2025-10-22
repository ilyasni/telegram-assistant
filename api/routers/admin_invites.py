"""API endpoints для управления инвайт-кодами.

Endpoints:
- POST /api/admin/invites - создать инвайт
- GET /api/admin/invites - список инвайтов
- GET /api/admin/invites/{code} - детали инвайта
- POST /api/admin/invites/{code}/revoke - отозвать инвайт
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
import uuid
import structlog
import psycopg2
from psycopg2.extras import RealDictCursor
from config import settings

router = APIRouter(prefix="/api/admin", tags=["admin_invites"])
logger = structlog.get_logger()


class InviteCreate(BaseModel):
    """Модель для создания инвайт-кода."""
    tenant_id: str = Field(..., description="ID арендатора")
    role: str = Field("user", description="Роль пользователя (user|admin)")
    uses_limit: int = Field(1, ge=0, description="Лимит использований (0 = безлимит)")
    expires_at: Optional[datetime] = Field(None, description="Дата истечения")
    notes: Optional[str] = Field(None, description="Заметки")


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
async def create_invite(invite: InviteCreate):
    """Создание нового инвайт-кода."""
    if not validate_role(invite.role):
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
            
            logger.info("Invite code created", code=code, tenant_id=invite.tenant_id, role=invite.role)
            
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
            
    except Exception as e:
        conn.rollback()
        logger.error("Failed to create invite", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create invite")
    finally:
        conn.close()


@router.get("/invites", response_model=InviteListResponse)
async def list_invites(
    tenant_id: Optional[str] = Query(None, description="Фильтр по tenant_id"),
    status: Optional[str] = Query(None, description="Фильтр по статусу (active|revoked|expired)"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение")
):
    """Получение списка инвайт-кодов."""
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
            
            return InviteListResponse(
                invites=invites,
                total=total,
                limit=limit,
                offset=offset
            )
            
    except Exception as e:
        logger.error("Failed to list invites", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to list invites")
    finally:
        conn.close()


@router.get("/invites/{code}", response_model=InviteResponse)
async def get_invite(code: str):
    """Получение информации об инвайт-коде."""
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
        logger.error("Failed to get invite", code=code, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get invite")
    finally:
        conn.close()


@router.post("/invites/{code}/revoke", response_model=InviteRevokeResponse)
async def revoke_invite(code: str):
    """Отзыв инвайт-кода."""
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
            
            logger.info("Invite code revoked", code=code)
            
            return InviteRevokeResponse(
                code=code,
                status="revoked",
                revoked_at=datetime.now(timezone.utc)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Failed to revoke invite", code=code, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to revoke invite")
    finally:
        conn.close()
