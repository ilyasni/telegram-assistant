"""Admin API endpoints for user management [C7-ID: api-admin-001]

Endpoints:
- GET /api/admin/users - список пользователей с фильтрами
- GET /api/admin/users/{user_id} - детали пользователя
- PUT /api/admin/users/{user_id}/tier - изменение tier пользователя
- PUT /api/admin/users/{user_id}/role - изменение роли пользователя
- GET /api/admin/users/{user_id}/subscriptions - подписки пользователя
- PUT /api/admin/users/{user_id}/subscriptions/{subscription_id} - редактирование подписки
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import UUID
import uuid
import structlog
import time
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text
from prometheus_client import Counter, Histogram
from models.database import get_db, User, Identity, UserChannel, UserGroup, Channel, Group, UserAuditLog
from dependencies.auth import get_admin_user, get_current_tenant_id
from middleware.tracing import get_trace_id

logger = structlog.get_logger()
router = APIRouter(prefix="/api/admin", tags=["admin"])

# [C7-ID: api-admin-001] Метрики Prometheus для admin операций
ADMIN_OPERATIONS_TOTAL = Counter(
    "admin_operations_total",
    "Total admin operations",
    ["operation", "status", "tenant_id"]
)
ADMIN_OPERATION_DURATION = Histogram(
    "admin_operation_duration_seconds",
    "Admin operation duration",
    ["operation", "tenant_id"]
)
ADMIN_AUTH_FAILURES = Counter(
    "admin_authorization_failures_total",
    "Admin authorization failures",
    ["reason", "tenant_id"]
)
# Context7: Метрики для OCC (Optimistic Concurrency Control)
VERSION_CONFLICTS_TOTAL = Counter(
    "user_update_version_conflicts_total",
    "Version conflicts on user updates",
    ["actor", "field", "tenant_id"]
)
USER_ADMIN_UPDATES_TOTAL = Counter(
    "user_admin_updates_total",
    "Successful admin updates on role/tier",
    ["field", "result", "tenant_id"]
)


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class UserDetailResponse(BaseModel):
    """Детальная информация о пользователе."""
    id: str
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    tier: str
    role: str
    created_at: str
    last_active_at: Optional[str]
    tenant_id: str
    version: Optional[int] = Field(None, description="Версия записи для OCC")


class UserListItemResponse(BaseModel):
    """Элемент списка пользователей."""
    id: str
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    tier: str
    role: str
    created_at: str
    last_active_at: Optional[str]
    version: Optional[int] = Field(None, description="Версия записи для OCC")


class UserListResponse(BaseModel):
    """Список пользователей."""
    users: List[UserListItemResponse]
    total: int
    limit: int
    offset: int


class UpdateTierRequest(BaseModel):
    """Запрос на изменение tier."""
    tier: str = Field(..., description="Новый tier (free, basic, premium)")
    version: Optional[int] = Field(None, description="Ожидаемая версия для OCC (optional, для совместимости)")


class UpdateRoleRequest(BaseModel):
    """Запрос на изменение роли."""
    role: str = Field(..., description="Новая роль (user, admin)")
    version: Optional[int] = Field(None, description="Ожидаемая версия для OCC (optional, для совместимости)")


class SubscriptionResponse(BaseModel):
    """Информация о подписке."""
    id: str
    channel_id: Optional[str]
    group_id: Optional[str]
    channel_title: Optional[str]
    group_title: Optional[str]
    subscribed_at: str
    is_active: bool
    type: str  # 'channel' or 'group'


class UserSubscriptionsResponse(BaseModel):
    """Подписки пользователя."""
    user_id: str
    subscriptions: List[SubscriptionResponse]
    total: int


class UpdateSubscriptionRequest(BaseModel):
    """Запрос на обновление подписки."""
    is_active: bool = Field(..., description="Активна ли подписка")


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/users", response_model=UserListResponse)
async def list_users(
    request: Request,
    tier: Optional[str] = Query(None, description="Фильтр по tier"),
    role: Optional[str] = Query(None, description="Фильтр по роли"),
    search: Optional[str] = Query(None, description="Поиск по username, first_name, last_name"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Получение списка пользователей с фильтрами."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    try:
        # [C7-ID: api-admin-001] Построение запроса с фильтрами
        # Context7: Админ видит всех пользователей (без фильтрации по tenant_id)
        query = db.query(User)
        
        if tier:
            query = query.filter(User.tier == tier)
        
        if role:
            query = query.filter(User.role == role)
        
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    User.username.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern)
                )
            )
        
        # Получение общего количества
        total = query.count()
        
        # Получение записей с пагинацией
        users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()
        
        # Формирование ответа
        user_list = []
        for user in users:
            # Context7: Нормализация пустых строк в None для корректной обработки на фронтенде
            username = user.username if user.username and user.username.strip() else None
            first_name = user.first_name if user.first_name and user.first_name.strip() else None
            last_name = user.last_name if user.last_name and user.last_name.strip() else None
            
            user_list.append(UserListItemResponse(
                id=str(user.id),
                telegram_id=user.telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                tier=user.tier or "free",
                role=user.role or "user",
                created_at=user.created_at.isoformat(),
                last_active_at=user.last_active_at.isoformat() if user.last_active_at else None,
                version=user.version
            ))
        
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="list_users", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="list_users", status="success", tenant_id=tenant_id).inc()
        
        logger.info(
            "Admin listed users",
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            total=total,
            limit=limit,
            offset=offset,
            trace_id=trace_id
        )
        
        return UserListResponse(
            users=user_list,
            total=total,
            limit=limit,
            offset=offset
        )
        
    except Exception as e:
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="list_users", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="list_users", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to list users",
            error=str(e),
            admin_id=str(admin_user.id),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to list users")


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    user_id: str,
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Получение детальной информации о пользователе."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    try:
        # [C7-ID: api-admin-001] Поиск пользователя (админ видит всех пользователей)
        try:
            user_uuid = uuid.UUID(user_id)
            user = db.query(User).filter(User.id == user_uuid).first()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format")
        
        if not user:
            ADMIN_OPERATIONS_TOTAL.labels(operation="get_user_detail", status="not_found", tenant_id=tenant_id).inc()
            raise HTTPException(status_code=404, detail="User not found")
        
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="get_user_detail", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="get_user_detail", status="success", tenant_id=tenant_id).inc()
        
        logger.info(
            "Admin retrieved user detail",
            admin_id=str(admin_user.id),
            user_id=user_id,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        
        # Context7: Нормализация пустых строк в None для корректной обработки на фронтенде
        username = user.username if user.username and user.username.strip() else None
        first_name = user.first_name if user.first_name and user.first_name.strip() else None
        last_name = user.last_name if user.last_name and user.last_name.strip() else None
        
        return UserDetailResponse(
            id=str(user.id),
            telegram_id=user.telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            tier=user.tier or "free",
            role=user.role or "user",
            created_at=user.created_at.isoformat(),
            last_active_at=user.last_active_at.isoformat() if user.last_active_at else None,
            tenant_id=str(user.tenant_id),
            version=user.version
        )
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="get_user_detail", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="get_user_detail", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to get user detail",
            error=str(e),
            admin_id=str(admin_user.id),
            user_id=user_id,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to get user detail")


@router.put("/users/{user_id}/tier", response_model=UserDetailResponse)
async def update_user_tier(
    user_id: str,
    request: UpdateTierRequest,
    admin_request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Изменение tier пользователя с OCC (Optimistic Concurrency Control)."""
    start_time = time.time()
    trace_id = get_trace_id(admin_request)
    tenant_id = str(admin_user.tenant_id)
    actor_id = str(admin_user.id)
    
    # Context7: Детальное логирование входа в эндпоинт
    logger.info(
        "update_user_tier: request started",
        actor_id=actor_id,
        target_user_id=user_id,
        new_tier=request.tier,
        expected_version=request.version,
        tenant_id=tenant_id,
        trace_id=trace_id,
        auth_mode="jwt_admin"
    )
    
    # Валидация tier
    valid_tiers = ["free", "basic", "premium", "pro", "enterprise"]
    if request.tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {', '.join(valid_tiers)}")
    
    try:
        user_uuid = uuid.UUID(user_id)
        
        # Context7: Получаем текущую версию пользователя
        user = db.query(User).filter(User.id == user_uuid).first()
        if not user:
            ADMIN_OPERATIONS_TOTAL.labels(operation="update_user_tier", status="not_found", tenant_id=tenant_id).inc()
            logger.warning(
                "update_user_tier: user not found",
                actor_id=actor_id,
                target_user_id=user_id,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            raise HTTPException(status_code=404, detail="User not found")
        
        # Context7: Логируем текущее состояние пользователя
        target_telegram_id = user.telegram_id
        user_tenant_id = str(user.tenant_id) if user.tenant_id else None
        logger.info(
            "update_user_tier: user found",
            actor_id=actor_id,
            target_user_id=user_id,
            target_telegram_id=target_telegram_id,
            user_tenant_id=user_tenant_id,
            current_tier=user.tier,
            current_version=user.version,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        
        expected_version = request.version if request.version is not None else user.version
        old_tier = user.tier
        
        # Context7: Атомарный UPDATE с проверкой версии (OCC)
        # Используем прямой SQL для гарантии атомарности
        # НЕ фильтруем по tenant_id - админ может обновлять пользователей из любых tenant
        result = db.execute(
            text("""
                UPDATE users
                SET tier = :tier,
                    version = version + 1,
                    updated_at = NOW()
                WHERE id = :id
                  AND version = :expected_version
                RETURNING id, tier, role, version, updated_at
            """),
            {
                "tier": request.tier,
                "id": str(user_uuid),
                "expected_version": expected_version
            }
        )
        
        row = result.fetchone()
        affected_rows = result.rowcount
        
        # Context7: Детальное логирование результата UPDATE
        logger.info(
            "update_user_tier: UPDATE executed",
            actor_id=actor_id,
            target_user_id=user_id,
            target_telegram_id=target_telegram_id,
            affected_rows=affected_rows,
            expected_version=expected_version,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        
        # Context7: Если RETURNING пуст - версия изменилась (конфликт) или affected_rows = 0
        if not row or affected_rows == 0:
            VERSION_CONFLICTS_TOTAL.labels(actor="admin", field="tier", tenant_id=tenant_id).inc()
            USER_ADMIN_UPDATES_TOTAL.labels(field="tier", result="conflict", tenant_id=tenant_id).inc()
            
            # Получаем актуальную версию для логирования
            db.refresh(user)
            logger.warning(
                "update_user_tier: version conflict or no rows affected",
                actor_id=actor_id,
                target_user_id=user_id,
                target_telegram_id=target_telegram_id,
                expected_version=expected_version,
                actual_version=user.version,
                affected_rows=affected_rows,
                tenant_id=tenant_id,
                trace_id=trace_id,
                tier_before=old_tier,
                tier_after=request.tier
            )
            raise HTTPException(
                status_code=409,
                detail=f"Version conflict: expected version {expected_version}, but current version is {user.version}. Please refresh and try again."
            )
        
        # Context7: Обновляем объект из результата UPDATE
        # SQLAlchemy Row объект доступен по индексу или имени колонки
        user.tier = row[1]  # tier
        user.version = row[3]  # version
        user.updated_at = row[4]  # updated_at
        
        # Context7: Записываем в audit log
        audit_log = UserAuditLog(
            user_id=user_uuid,
            action="tier_changed",
            old_value=old_tier,
            new_value=request.tier,
            changed_by=admin_user.id,
            notes=f"Updated by admin {admin_user.id}"
        )
        db.add(audit_log)
        
        # Context7: Явный flush перед commit для проверки ошибок целостности
        try:
            db.flush()
            logger.debug(
                "update_user_tier: flush successful",
                actor_id=actor_id,
                target_user_id=user_id,
                trace_id=trace_id
            )
        except Exception as flush_error:
            logger.error(
                "update_user_tier: flush failed",
                actor_id=actor_id,
                target_user_id=user_id,
                error=str(flush_error),
                error_type=type(flush_error).__name__,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Database flush failed: {str(flush_error)}")
        
        # Context7: Коммит транзакции
        try:
            db.commit()
            logger.debug(
                "update_user_tier: commit successful",
                actor_id=actor_id,
                target_user_id=user_id,
                trace_id=trace_id
            )
        except Exception as commit_error:
            logger.error(
                "update_user_tier: commit failed",
                actor_id=actor_id,
                target_user_id=user_id,
                error=str(commit_error),
                error_type=type(commit_error).__name__,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Database commit failed: {str(commit_error)}")
        
        # Context7: Обновляем объект после commit (перезагружаем из БД)
        db.refresh(user)
        
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="update_user_tier", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="update_user_tier", status="success", tenant_id=tenant_id).inc()
        USER_ADMIN_UPDATES_TOTAL.labels(field="tier", result="success", tenant_id=tenant_id).inc()
        
        # Context7: Детальное логирование успешного обновления
        logger.info(
            "update_user_tier: success",
            actor_id=actor_id,
            target_user_id=user_id,
            target_telegram_id=target_telegram_id,
            tier_before=old_tier,
            tier_after=request.tier,
            version_before=expected_version,
            version_after=user.version,
            affected_rows=affected_rows,
            tenant_id=tenant_id,
            trace_id=trace_id,
            duration_ms=duration * 1000
        )
        
        # Context7: Нормализация пустых строк в None
        username = user.username if user.username and user.username.strip() else None
        first_name = user.first_name if user.first_name and user.first_name.strip() else None
        last_name = user.last_name if user.last_name and user.last_name.strip() else None
        
        return UserDetailResponse(
            id=str(user.id),
            telegram_id=user.telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            tier=user.tier,
            role=user.role or "user",
            created_at=user.created_at.isoformat(),
            last_active_at=user.last_active_at.isoformat() if user.last_active_at else None,
            tenant_id=str(user.tenant_id),
            version=user.version
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="update_user_tier", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="update_user_tier", status="error", tenant_id=tenant_id).inc()
        USER_ADMIN_UPDATES_TOTAL.labels(field="tier", result="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to update user tier",
            error=str(e),
            admin_id=str(admin_user.id),
            user_id=user_id,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to update user tier")


@router.put("/users/{user_id}/role", response_model=UserDetailResponse)
async def update_user_role(
    user_id: str,
    request: UpdateRoleRequest,
    admin_request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Изменение роли пользователя с OCC (Optimistic Concurrency Control)."""
    start_time = time.time()
    trace_id = get_trace_id(admin_request)
    tenant_id = str(admin_user.tenant_id)
    actor_id = str(admin_user.id)
    
    # Context7: Детальное логирование входа в эндпоинт
    logger.info(
        "update_user_role: request started",
        actor_id=actor_id,
        target_user_id=user_id,
        new_role=request.role,
        expected_version=request.version,
        tenant_id=tenant_id,
        trace_id=trace_id,
        auth_mode="jwt_admin"
    )
    
    # Валидация роли
    valid_roles = ["user", "admin"]
    if request.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}")
    
    try:
        user_uuid = uuid.UUID(user_id)
        
        # Context7: Получаем текущую версию пользователя
        user = db.query(User).filter(User.id == user_uuid).first()
        if not user:
            ADMIN_OPERATIONS_TOTAL.labels(operation="update_user_role", status="not_found", tenant_id=tenant_id).inc()
            logger.warning(
                "update_user_role: user not found",
                actor_id=actor_id,
                target_user_id=user_id,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            raise HTTPException(status_code=404, detail="User not found")
        
        # Context7: Логируем текущее состояние пользователя
        target_telegram_id = user.telegram_id
        user_tenant_id = str(user.tenant_id) if user.tenant_id else None
        logger.info(
            "update_user_role: user found",
            actor_id=actor_id,
            target_user_id=user_id,
            target_telegram_id=target_telegram_id,
            user_tenant_id=user_tenant_id,
            current_role=user.role,
            current_version=user.version,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        
        expected_version = request.version if request.version is not None else user.version
        old_role = user.role
        
        # Context7: Атомарный UPDATE с проверкой версии (OCC)
        # Используем прямой SQL для гарантии атомарности
        # НЕ фильтруем по tenant_id - админ может обновлять пользователей из любых tenant
        result = db.execute(
            text("""
                UPDATE users
                SET role = :role,
                    version = version + 1,
                    updated_at = NOW()
                WHERE id = :id
                  AND version = :expected_version
                RETURNING id, tier, role, version, updated_at
            """),
            {
                "role": request.role,
                "id": str(user_uuid),
                "expected_version": expected_version
            }
        )
        
        row = result.fetchone()
        affected_rows = result.rowcount
        
        # Context7: Детальное логирование результата UPDATE
        logger.info(
            "update_user_role: UPDATE executed",
            actor_id=actor_id,
            target_user_id=user_id,
            target_telegram_id=target_telegram_id,
            affected_rows=affected_rows,
            expected_version=expected_version,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        
        # Context7: Если RETURNING пуст - версия изменилась (конфликт) или affected_rows = 0
        if not row or affected_rows == 0:
            VERSION_CONFLICTS_TOTAL.labels(actor="admin", field="role", tenant_id=tenant_id).inc()
            USER_ADMIN_UPDATES_TOTAL.labels(field="role", result="conflict", tenant_id=tenant_id).inc()
            
            # Получаем актуальную версию для логирования
            db.refresh(user)
            logger.warning(
                "update_user_role: version conflict or no rows affected",
                actor_id=actor_id,
                target_user_id=user_id,
                target_telegram_id=target_telegram_id,
                expected_version=expected_version,
                actual_version=user.version,
                affected_rows=affected_rows,
                tenant_id=tenant_id,
                trace_id=trace_id,
                role_before=old_role,
                role_after=request.role
            )
            raise HTTPException(
                status_code=409,
                detail=f"Version conflict: expected version {expected_version}, but current version is {user.version}. Please refresh and try again."
            )
        
        # Context7: Обновляем объект из результата UPDATE
        # SQLAlchemy Row объект доступен по индексу или имени колонки
        user.role = row[2]  # role
        user.version = row[3]  # version
        user.updated_at = row[4]  # updated_at
        
        # Context7: Записываем в audit log
        audit_log = UserAuditLog(
            user_id=user_uuid,
            action="role_changed",
            old_value=old_role,
            new_value=request.role,
            changed_by=admin_user.id,
            notes=f"Updated by admin {admin_user.id}"
        )
        db.add(audit_log)
        
        # Context7: Явный flush перед commit для проверки ошибок целостности
        try:
            db.flush()
            logger.debug(
                "update_user_role: flush successful",
                actor_id=actor_id,
                target_user_id=user_id,
                trace_id=trace_id
            )
        except Exception as flush_error:
            logger.error(
                "update_user_role: flush failed",
                actor_id=actor_id,
                target_user_id=user_id,
                error=str(flush_error),
                error_type=type(flush_error).__name__,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Database flush failed: {str(flush_error)}")
        
        # Context7: Коммит транзакции
        try:
            db.commit()
            logger.debug(
                "update_user_role: commit successful",
                actor_id=actor_id,
                target_user_id=user_id,
                trace_id=trace_id
            )
        except Exception as commit_error:
            logger.error(
                "update_user_role: commit failed",
                actor_id=actor_id,
                target_user_id=user_id,
                error=str(commit_error),
                error_type=type(commit_error).__name__,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Database commit failed: {str(commit_error)}")
        
        # Context7: Обновляем объект после commit (перезагружаем из БД)
        db.refresh(user)
        
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="update_user_role", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="update_user_role", status="success", tenant_id=tenant_id).inc()
        USER_ADMIN_UPDATES_TOTAL.labels(field="role", result="success", tenant_id=tenant_id).inc()
        
        # Context7: Детальное логирование успешного обновления
        logger.info(
            "update_user_role: success",
            actor_id=actor_id,
            target_user_id=user_id,
            target_telegram_id=target_telegram_id,
            role_before=old_role,
            role_after=request.role,
            version_before=expected_version,
            version_after=user.version,
            affected_rows=affected_rows,
            tenant_id=tenant_id,
            trace_id=trace_id,
            duration_ms=duration * 1000
        )
        
        # Context7: Нормализация пустых строк в None
        username = user.username if user.username and user.username.strip() else None
        first_name = user.first_name if user.first_name and user.first_name.strip() else None
        last_name = user.last_name if user.last_name and user.last_name.strip() else None
        
        return UserDetailResponse(
            id=str(user.id),
            telegram_id=user.telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            tier=user.tier or "free",
            role=user.role,
            created_at=user.created_at.isoformat(),
            last_active_at=user.last_active_at.isoformat() if user.last_active_at else None,
            tenant_id=str(user.tenant_id),
            version=user.version
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="update_user_role", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="update_user_role", status="error", tenant_id=tenant_id).inc()
        USER_ADMIN_UPDATES_TOTAL.labels(field="role", result="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to update user role",
            error=str(e),
            admin_id=str(admin_user.id),
            user_id=user_id,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to update user role")


@router.get("/users/{user_id}/subscriptions", response_model=UserSubscriptionsResponse)
async def get_user_subscriptions(
    user_id: str,
    request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Получение подписок пользователя (каналы и группы)."""
    start_time = time.time()
    trace_id = get_trace_id(request)
    tenant_id = str(admin_user.tenant_id)
    
    try:
        user_uuid = uuid.UUID(user_id)
        user = db.query(User).filter(User.id == user_uuid).first()
        
        if not user:
            ADMIN_OPERATIONS_TOTAL.labels(operation="get_user_subscriptions", status="not_found", tenant_id=tenant_id).inc()
            raise HTTPException(status_code=404, detail="User not found")
        
        # Подписки на каналы
        channel_subs = db.query(UserChannel, Channel).join(
            Channel, UserChannel.channel_id == Channel.id
        ).filter(UserChannel.user_id == user_uuid).all()
        
        # Подписки на группы
        group_subs = db.query(UserGroup, Group).join(
            Group, UserGroup.group_id == Group.id
        ).filter(UserGroup.user_id == user_uuid).all()
        
        subscriptions = []
        
        # Обработка подписок на каналы
        for user_channel, channel in channel_subs:
            subscriptions.append(SubscriptionResponse(
                id=f"channel_{user_channel.channel_id}",
                channel_id=str(channel.id),
                group_id=None,
                channel_title=channel.title,
                group_title=None,
                subscribed_at=user_channel.subscribed_at.isoformat(),
                is_active=user_channel.is_active,
                type="channel"
            ))
        
        # Обработка подписок на группы
        for user_group, group in group_subs:
            subscriptions.append(SubscriptionResponse(
                id=f"group_{user_group.group_id}",
                channel_id=None,
                group_id=str(group.id),
                channel_title=None,
                group_title=group.title,
                subscribed_at=user_group.subscribed_at.isoformat(),
                is_active=user_group.is_active,
                type="group"
            ))
        
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="get_user_subscriptions", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="get_user_subscriptions", status="success", tenant_id=tenant_id).inc()
        
        logger.info(
            "Admin retrieved user subscriptions",
            admin_id=str(admin_user.id),
            user_id=user_id,
            subscriptions_count=len(subscriptions),
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        
        return UserSubscriptionsResponse(
            user_id=user_id,
            subscriptions=subscriptions,
            total=len(subscriptions)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="get_user_subscriptions", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="get_user_subscriptions", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to get user subscriptions",
            error=str(e),
            admin_id=str(admin_user.id),
            user_id=user_id,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to get user subscriptions")


@router.put("/users/{user_id}/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(
    user_id: str,
    subscription_id: str,
    request: UpdateSubscriptionRequest,
    admin_request: Request,
    admin_user: User = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Редактирование подписки (активация/деактивация)."""
    start_time = time.time()
    trace_id = get_trace_id(admin_request)
    tenant_id = str(admin_user.tenant_id)
    
    try:
        user_uuid = uuid.UUID(user_id)
        user = db.query(User).filter(User.id == user_uuid).first()
        
        if not user:
            ADMIN_OPERATIONS_TOTAL.labels(operation="update_subscription", status="not_found", tenant_id=tenant_id).inc()
            raise HTTPException(status_code=404, detail="User not found")
        
        # Определяем тип подписки и обновляем
        if subscription_id.startswith("channel_"):
            channel_id = uuid.UUID(subscription_id.replace("channel_", ""))
            user_channel = db.query(UserChannel).filter(
                and_(
                    UserChannel.user_id == user_uuid,
                    UserChannel.channel_id == channel_id
                )
            ).first()
            
            if not user_channel:
                raise HTTPException(status_code=404, detail="Subscription not found")
            
            user_channel.is_active = request.is_active
            db.commit()
            db.refresh(user_channel)
            
            channel = db.query(Channel).filter(Channel.id == channel_id).first()
            
            duration = time.time() - start_time
            ADMIN_OPERATION_DURATION.labels(operation="update_subscription", tenant_id=tenant_id).observe(duration)
            ADMIN_OPERATIONS_TOTAL.labels(operation="update_subscription", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Admin updated subscription",
                admin_id=str(admin_user.id),
                user_id=user_id,
                subscription_id=subscription_id,
                is_active=request.is_active,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return SubscriptionResponse(
                id=subscription_id,
                channel_id=str(channel.id) if channel else None,
                group_id=None,
                channel_title=channel.title if channel else None,
                group_title=None,
                subscribed_at=user_channel.subscribed_at.isoformat(),
                is_active=user_channel.is_active,
                type="channel"
            )
            
        elif subscription_id.startswith("group_"):
            group_id = uuid.UUID(subscription_id.replace("group_", ""))
            user_group = db.query(UserGroup).filter(
                and_(
                    UserGroup.user_id == user_uuid,
                    UserGroup.group_id == group_id
                )
            ).first()
            
            if not user_group:
                raise HTTPException(status_code=404, detail="Subscription not found")
            
            user_group.is_active = request.is_active
            db.commit()
            db.refresh(user_group)
            
            group = db.query(Group).filter(Group.id == group_id).first()
            
            duration = time.time() - start_time
            ADMIN_OPERATION_DURATION.labels(operation="update_subscription", tenant_id=tenant_id).observe(duration)
            ADMIN_OPERATIONS_TOTAL.labels(operation="update_subscription", status="success", tenant_id=tenant_id).inc()
            
            logger.info(
                "Admin updated subscription",
                admin_id=str(admin_user.id),
                user_id=user_id,
                subscription_id=subscription_id,
                is_active=request.is_active,
                tenant_id=tenant_id,
                trace_id=trace_id
            )
            
            return SubscriptionResponse(
                id=subscription_id,
                channel_id=None,
                group_id=str(group.id) if group else None,
                channel_title=None,
                group_title=group.title if group else None,
                subscribed_at=user_group.subscribed_at.isoformat(),
                is_active=user_group.is_active,
                type="group"
            )
        else:
            raise HTTPException(status_code=400, detail="Invalid subscription_id format")
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        duration = time.time() - start_time
        ADMIN_OPERATION_DURATION.labels(operation="update_subscription", tenant_id=tenant_id).observe(duration)
        ADMIN_OPERATIONS_TOTAL.labels(operation="update_subscription", status="error", tenant_id=tenant_id).inc()
        
        logger.error(
            "Failed to update subscription",
            error=str(e),
            admin_id=str(admin_user.id),
            user_id=user_id,
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            trace_id=trace_id
        )
        raise HTTPException(status_code=500, detail="Failed to update subscription")

