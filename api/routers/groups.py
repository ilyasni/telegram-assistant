"""
Groups API — управление Telegram-группами и запуск групповых дайджестов.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from models.database import get_db, Group, GroupDiscoveryRequest
from api.tasks.scheduler_tasks import enqueue_group_digest

logger = structlog.get_logger()

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    tg_chat_id: int
    title: str
    username: Optional[str]
    is_active: bool
    last_checked_at: Optional[datetime]
    created_at: datetime
    settings: Dict[str, Any]


class GroupListResponse(BaseModel):
    groups: List[GroupResponse]
    total: int
    limit: int
    offset: int


class GroupCreateRequest(BaseModel):
    tenant_id: UUID
    tg_chat_id: int
    title: str = Field(..., max_length=500)
    username: Optional[str] = Field(default=None, max_length=255)
    invite_link: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class GroupUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=500)
    username: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None
    settings: Optional[Dict[str, Any]] = None


def _validate_uuid_string(value: Any, field_name: str = "field") -> UUID:
    """Context7: Конвертация строки в UUID для использования в BeforeValidator."""
    logger.info(
        f"GroupDigestRequest BeforeValidator - {field_name}",
        field_value=value,
        field_type=type(value).__name__,
        field_value_repr=repr(value),
    )
    
    if value is None:
        logger.error(f"GroupDigestRequest BeforeValidator - {field_name} is None")
        raise ValueError(f"{field_name} не может быть None")
    
    if isinstance(value, UUID):
        logger.debug(f"GroupDigestRequest BeforeValidator - {field_name} already UUID", uuid=str(value))
        return value
    
    value_str = str(value).strip()
    if not value_str or value_str.lower() in ('none', 'null', ''):
        logger.error(f"GroupDigestRequest BeforeValidator - {field_name} is empty or None-like", original_value=value, value_str=value_str)
        raise ValueError(f"{field_name} не может быть пустой строкой или None")
    
    try:
        uuid_obj = UUID(value_str)
        logger.info(f"GroupDigestRequest BeforeValidator - {field_name} converted to UUID", original=value_str, uuid=str(uuid_obj))
        return uuid_obj
    except ValueError as e:
        logger.error(f"GroupDigestRequest BeforeValidator - {field_name} conversion failed", value=value, value_str=value_str, error=str(e), error_type=type(e).__name__)
        raise ValueError(f"Невалидный формат UUID для {field_name}: {value}") from e


def _validate_tenant_id(value: Any) -> UUID:
    """Context7: Валидатор для tenant_id."""
    return _validate_uuid_string(value, "tenant_id")


def _validate_user_id(value: Any) -> UUID:
    """Context7: Валидатор для user_id."""
    return _validate_uuid_string(value, "user_id")


class GroupDigestRequest(BaseModel):
    # Context7: Поля объявлены как UUID, но model_validator конвертирует строки ПЕРЕД валидацией
    tenant_id: UUID
    user_id: UUID
    window_size_hours: int = Field(..., description="Размер окна в часах", example=24)
    delivery_channel: str = Field(default="telegram", pattern="^(telegram)$")
    delivery_format: str = Field(default="telegram_html", pattern="^(telegram_html|json|cards)$")
    trigger: str = Field(default="manual")

    @model_validator(mode="before")
    @classmethod
    def validate_and_convert_uuids(cls, data: Any) -> Any:
        """Context7: Конвертация строк в UUID для tenant_id и user_id ПЕРЕД валидацией полей."""
        if not isinstance(data, dict):
            return data
        
        logger.info(
            "GroupDigestRequest model_validator - before",
            tenant_id_raw=data.get("tenant_id"),
            user_id_raw=data.get("user_id"),
            tenant_id_type=type(data.get("tenant_id")).__name__ if data.get("tenant_id") is not None else "None",
            user_id_type=type(data.get("user_id")).__name__ if data.get("user_id") is not None else "None",
        )
        
        # Конвертируем tenant_id
        if "tenant_id" in data:
            tenant_id_raw = data["tenant_id"]
            try:
                tenant_id_converted = _validate_tenant_id(tenant_id_raw)
                data["tenant_id"] = tenant_id_converted
                logger.info("GroupDigestRequest model_validator - tenant_id converted", original=tenant_id_raw, converted=str(tenant_id_converted))
            except Exception as e:
                logger.error("GroupDigestRequest model_validator - tenant_id conversion failed", value=tenant_id_raw, error=str(e))
                raise ValueError(f"Невалидный формат UUID для tenant_id: {tenant_id_raw}") from e
        
        # Конвертируем user_id
        if "user_id" in data:
            user_id_raw = data["user_id"]
            try:
                user_id_converted = _validate_user_id(user_id_raw)
                data["user_id"] = user_id_converted
                logger.info("GroupDigestRequest model_validator - user_id converted", original=user_id_raw, converted=str(user_id_converted))
            except Exception as e:
                logger.error("GroupDigestRequest model_validator - user_id conversion failed", value=user_id_raw, error=str(e))
                raise ValueError(f"Невалидный формат UUID для user_id: {user_id_raw}") from e
        
        return data

    @field_validator("window_size_hours")
    @classmethod
    def validate_window(cls, value: int) -> int:
        if value not in (4, 6, 12, 24):
            raise ValueError("window_size_hours должен быть одним из значений: 4, 6, 12, 24")
        return value


class GroupDigestResponse(BaseModel):
    history_id: UUID
    group_window_id: UUID
    message_count: int
    participant_count: int
    status: str = "queued"


class GroupDiscoveryCandidate(BaseModel):
    tg_chat_id: int
    title: str
    username: Optional[str] = None
    is_megagroup: bool = False
    is_gigagroup: bool = False
    is_channel: bool = False
    is_broadcast: bool = False
    category: str = Field(default="group", pattern="^(group|supergroup|channel)$")
    is_private: bool = False
    participants_count: Optional[int] = None
    invite_required: bool = False
    is_connected: bool = False
    connected_group_id: Optional[UUID] = None


class GroupDiscoveryCreateRequest(BaseModel):
    tenant_id: UUID
    user_id: UUID


class GroupDiscoveryRequestResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    status: str
    total: int
    connected_count: int
    results: List[GroupDiscoveryCandidate]
    error: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]


def _to_response(group: Group) -> GroupResponse:
    return GroupResponse(
        id=group.id,
        tenant_id=group.tenant_id,
        tg_chat_id=group.tg_chat_id,
        title=group.title,
        username=group.username,
        is_active=group.is_active,
        last_checked_at=group.last_checked_at,
        created_at=group.created_at,
        settings=group.settings or {},
    )


def _build_discovery_candidates(discovery: GroupDiscoveryRequest, db: Session) -> List[GroupDiscoveryCandidate]:
    raw_results = discovery.results or []
    if not raw_results:
        return []

    connected_groups = (
        db.query(Group)
        .filter(Group.tenant_id == discovery.tenant_id)
        .all()
    )
    connected_map: Dict[int, Dict[str, Any]] = {}
    for group in connected_groups:
        if group.tg_chat_id is not None:
            connected_map[int(group.tg_chat_id)] = {
                "group_id": group.id,
            }

    candidates: List[GroupDiscoveryCandidate] = []
    for item in raw_results:
        tg_chat_id = int(item.get("tg_chat_id"))
        category = item.get("category") or ("channel" if item.get("is_channel") else "group")
        payload: Dict[str, Any] = {
            "tg_chat_id": tg_chat_id,
            "title": item.get("title") or "",
            "username": item.get("username"),
            "is_megagroup": bool(item.get("is_megagroup")),
            "is_gigagroup": bool(item.get("is_gigagroup")),
            "is_channel": bool(item.get("is_channel")),
            "is_broadcast": bool(item.get("is_broadcast")),
            "category": category,
            "is_private": bool(item.get("is_private")),
            "participants_count": item.get("participants_count"),
            "invite_required": bool(item.get("invite_required")),
            "is_connected": bool(item.get("is_connected")),
            "connected_group_id": None,
        }

        if tg_chat_id in connected_map:
            payload["is_connected"] = True
            payload["connected_group_id"] = connected_map[tg_chat_id]["group_id"]

        candidates.append(GroupDiscoveryCandidate(**payload))

    return candidates


def _to_discovery_response(discovery: GroupDiscoveryRequest, db: Session) -> GroupDiscoveryRequestResponse:
    candidates = _build_discovery_candidates(discovery, db)
    total = len(candidates) if candidates else discovery.total
    connected_count = sum(1 for candidate in candidates if candidate.is_connected)

    return GroupDiscoveryRequestResponse(
        id=discovery.id,
        tenant_id=discovery.tenant_id,
        user_id=discovery.user_id,
        status=discovery.status,
        total=total,
        connected_count=connected_count if candidates else discovery.connected_count,
        results=candidates,
        error=discovery.error,
        created_at=discovery.created_at,
        completed_at=discovery.completed_at,
    )


@router.get("/", response_model=GroupListResponse)
async def list_groups(
    tenant_id: UUID = Query(..., description="ID арендатора"),
    user_id: Optional[UUID] = Query(default=None, description="ID пользователя для фильтрации по подпискам"),
    status: Optional[str] = Query(default=None, pattern="^(active|disabled)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Context7: Список групп.
    Если указан user_id, возвращает только группы, на которые пользователь подписан через user_group.
    Иначе возвращает все группы tenant (для обратной совместимости).
    """
    from models.database import UserGroup
    
    # Context7: Если указан user_id, фильтруем через user_group по подпискам пользователя
    if user_id:
        query = (
            db.query(Group)
            .join(UserGroup, Group.id == UserGroup.group_id)
            .filter(
                UserGroup.user_id == user_id,
                UserGroup.is_active.is_(True),
                Group.tenant_id == tenant_id
            )
        )
    else:
        # Context7: Для обратной совместимости - все группы tenant
        query = db.query(Group).filter(Group.tenant_id == tenant_id)

    if status == "active":
        query = query.filter(Group.is_active.is_(True))
    elif status == "disabled":
        query = query.filter(Group.is_active.is_(False))

    total = query.count()
    groups = (
        query.order_by(Group.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return GroupListResponse(
        groups=[_to_response(group) for group in groups],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/", response_model=GroupResponse, status_code=201)
async def create_group(request: GroupCreateRequest, db: Session = Depends(get_db)):
    """
    Context7: Создание группы.
    Группы глобальные (без tenant_id), как каналы.
    Изоляция происходит через user_group при запросах пользователя.
    """
    # Context7: Группы глобальные - проверяем только по tg_chat_id
    existing = (
        db.query(Group)
        .filter(Group.tg_chat_id == request.tg_chat_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Group already registered")
    
    # Context7: Используем утилиту для получения системного tenant_id
    # TODO: В будущем миграции нужно убрать tenant_id из groups
    from utils.tenant_utils import get_system_tenant_id
    try:
        system_tenant_id = get_system_tenant_id(db)
    except ValueError:
        raise HTTPException(status_code=500, detail="No tenant found in database")

    group = Group(
        tenant_id=system_tenant_id,  # Context7: Системный tenant_id для совместимости
        tg_chat_id=request.tg_chat_id,
        title=request.title,
        username=request.username,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        settings=request.settings or {},
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    
    # Context7: Автоматически создаём подписку для пользователя, который добавил группу
    # Используем added_by из settings, если он указан
    settings = request.settings or {}
    added_by = settings.get("added_by") or settings.get("discovery", {}).get("requested_by")
    if added_by:
        try:
            from models.database import UserGroup
            user_uuid = UUID(str(added_by))
            # Проверяем, существует ли пользователь
            from models.database import User
            user = db.query(User).filter(User.id == user_uuid).first()
            if user:
                # Проверяем, нет ли уже подписки
                existing_subscription = (
                    db.query(UserGroup)
                    .filter(
                        UserGroup.user_id == user_uuid,
                        UserGroup.group_id == group.id
                    )
                    .first()
                )
                if not existing_subscription:
                    subscription = UserGroup(
                        user_id=user_uuid,
                        group_id=group.id,
                        is_active=True,
                        monitor_mentions=True,
                        subscribed_at=datetime.now(timezone.utc),
                        settings={},
                    )
                    db.add(subscription)
                    db.commit()
                    logger.info(
                        "Auto-created user_group subscription on group creation",
                        user_id=str(user_uuid),
                        group_id=str(group.id),
                        group_title=group.title,
                    )
                else:
                    # Активируем существующую подписку
                    existing_subscription.is_active = True
                    db.commit()
                    logger.info(
                        "Activated existing user_group subscription on group creation",
                        user_id=str(user_uuid),
                        group_id=str(group.id),
                    )
        except (ValueError, TypeError) as e:
            logger.warning(
                "Failed to create user_group subscription on group creation",
                group_id=str(group.id),
                added_by=added_by,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                "Error creating user_group subscription on group creation",
                group_id=str(group.id),
                added_by=added_by,
                error=str(e),
                exc_info=True,
            )
    
    return _to_response(group)


@router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(group_id: UUID, request: GroupUpdateRequest, db: Session = Depends(get_db)):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if request.title is not None:
        group.title = request.title
    if request.username is not None:
        group.username = request.username
    if request.is_active is not None:
        group.is_active = request.is_active
    if request.settings is not None:
        group.settings = request.settings

    db.commit()
    db.refresh(group)
    return _to_response(group)


@router.post("/{group_id}/digest", response_model=GroupDigestResponse, status_code=202)
async def trigger_group_digest(group_id: UUID, request: GroupDigestRequest, db: Session = Depends(get_db)):
    # Context7: Детальное логирование входящего запроса (после успешной валидации)
    logger.info(
        "Group digest trigger request received (validation passed)",
        group_id=str(group_id),
        tenant_id=str(request.tenant_id),
        user_id=str(request.user_id),
        window_size_hours=request.window_size_hours,
        tenant_id_type=type(request.tenant_id).__name__,
        user_id_type=type(request.user_id).__name__,
        tenant_id_is_uuid=isinstance(request.tenant_id, UUID),
        user_id_is_uuid=isinstance(request.user_id, UUID),
    )
    
    # Context7: Проверка существования группы и принадлежности к tenant
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        logger.warning(
            "Group not found for digest request",
            group_id=str(group_id),
            tenant_id=str(request.tenant_id),
            user_id=str(request.user_id),
        )
        raise HTTPException(
            status_code=404,
            detail=f"Group {group_id} not found"
        )
    
    # Context7: Проверка принадлежности группы к указанному tenant_id
    if group.tenant_id != request.tenant_id:
        logger.warning(
            "Group tenant mismatch for digest request",
            group_id=str(group_id),
            group_tenant_id=str(group.tenant_id),
            request_tenant_id=str(request.tenant_id),
            user_id=str(request.user_id),
        )
        raise HTTPException(
            status_code=403,
            detail=f"Group {group_id} does not belong to tenant {request.tenant_id}"
        )

    try:
        result = await enqueue_group_digest(
            tenant_id=str(request.tenant_id),
            user_id=str(request.user_id),
            group_id=str(group_id),
            window_size_hours=request.window_size_hours,
            delivery_channel=request.delivery_channel,
            delivery_format=request.delivery_format,
            trigger=request.trigger,
            requested_by=str(request.user_id),
        )
    except PermissionError as exc:
        logger.warning(
            "Permission denied for group digest",
            group_id=str(group_id),
            tenant_id=str(request.tenant_id),
            user_id=str(request.user_id),
            error=str(exc),
        )
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning(
            "Validation error for group digest",
            group_id=str(group_id),
            tenant_id=str(request.tenant_id),
            user_id=str(request.user_id),
            error=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Unexpected error during group digest enqueue",
            group_id=str(group_id),
            tenant_id=str(request.tenant_id),
            user_id=str(request.user_id),
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal server error while processing digest request"
        ) from exc

    return GroupDigestResponse(
        history_id=UUID(result["history_id"]),
        group_window_id=UUID(result["group_window_id"]),
        message_count=result["message_count"],
        participant_count=result["participant_count"],
    )


@router.post("/discovery", response_model=GroupDiscoveryRequestResponse, status_code=202)
async def create_group_discovery_request(request: GroupDiscoveryCreateRequest, db: Session = Depends(get_db)):
    discovery = GroupDiscoveryRequest(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
        results=[],
    )
    db.add(discovery)
    db.commit()
    db.refresh(discovery)
    return _to_discovery_response(discovery, db)


@router.get("/discovery/{request_id}", response_model=GroupDiscoveryRequestResponse)
async def get_group_discovery_request(
    request_id: UUID,
    tenant_id: UUID = Query(..., description="ID арендатора для проверки доступа"),
    db: Session = Depends(get_db),
):
    discovery = (
        db.query(GroupDiscoveryRequest)
        .filter(
            GroupDiscoveryRequest.id == request_id,
            GroupDiscoveryRequest.tenant_id == tenant_id,
        )
        .first()
    )
    if not discovery:
        raise HTTPException(status_code=404, detail="Discovery request not found")
    return _to_discovery_response(discovery, db)


@router.get("/discovery/latest", response_model=GroupDiscoveryRequestResponse)
async def get_latest_group_discovery(
    tenant_id: UUID = Query(..., description="ID арендатора"),
    status: Optional[str] = Query(default=None, pattern="^(pending|processing|completed|failed)$"),
    db: Session = Depends(get_db),
):
    query = (
        db.query(GroupDiscoveryRequest)
        .filter(GroupDiscoveryRequest.tenant_id == tenant_id)
        .order_by(GroupDiscoveryRequest.created_at.desc())
    )
    if status:
        query = query.filter(GroupDiscoveryRequest.status == status)

    discovery = query.first()
    if not discovery:
        raise HTTPException(status_code=404, detail="Discovery request not found")
    return _to_discovery_response(discovery, db)

