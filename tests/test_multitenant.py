"""
Тесты для multi-tenant функциональности.
Context7: Проверка изоляции данных, RLS, identities/memberships.
"""

import pytest
import uuid
from sqlalchemy.orm import Session
from api.models.database import Identity, User, Tenant, get_db
from api.routers.users import create_user, UserCreate
from api.routers.tg_webapp_auth import verify_webapp_init_data, create_jwt, WebAppAuthRequest
from config import settings


@pytest.fixture
def db_session():
    """Создание тестовой БД сессии."""
    from api.models.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def tenant_1(db_session: Session) -> str:
    """Создание первого tenant для тестов."""
    tenant = Tenant(name="Test Tenant 1")
    db_session.add(tenant)
    db_session.commit()
    return str(tenant.id)


@pytest.fixture
def tenant_2(db_session: Session) -> str:
    """Создание второго tenant для тестов."""
    tenant = Tenant(name="Test Tenant 2")
    db_session.add(tenant)
    db_session.commit()
    return str(tenant.id)


def test_create_identity_and_membership(db_session: Session, tenant_1: str):
    """Тест: создание identity и membership для одного telegram_id."""
    telegram_id = 123456789
    
    # 1) Создаём identity
    identity = Identity(telegram_id=telegram_id)
    db_session.add(identity)
    db_session.commit()
    
    # 2) Создаём membership в tenant_1
    user1 = User(
        tenant_id=uuid.UUID(tenant_1),
        identity_id=identity.id,
        telegram_id=telegram_id,
        username="test_user",
        first_name="Test"
    )
    db_session.add(user1)
    db_session.commit()
    
    # Проверка: identity существует
    assert identity.id is not None
    assert identity.telegram_id == telegram_id
    
    # Проверка: membership создан
    assert user1.id is not None
    assert user1.tenant_id == uuid.UUID(tenant_1)
    assert user1.identity_id == identity.id
    
    print(f"✓ Identity created: {identity.id}")
    print(f"✓ Membership created: {user1.id} in tenant {tenant_1}")


def test_same_telegram_id_multiple_tenants(db_session: Session, tenant_1: str, tenant_2: str):
    """Тест: один telegram_id может быть в нескольких tenants."""
    telegram_id = 987654321
    
    # 1) Создаём identity (глобальная)
    identity = Identity(telegram_id=telegram_id)
    db_session.add(identity)
    db_session.commit()
    
    # 2) Создаём membership в tenant_1
    user1 = User(
        tenant_id=uuid.UUID(tenant_1),
        identity_id=identity.id,
        telegram_id=telegram_id,
        username="test_user",
        tier="pro"
    )
    db_session.add(user1)
    db_session.commit()
    
    # 3) Создаём membership в tenant_2 (тот же telegram_id, но другой tenant)
    user2 = User(
        tenant_id=uuid.UUID(tenant_2),
        identity_id=identity.id,
        telegram_id=telegram_id,
        username="test_user",
        tier="free"
    )
    db_session.add(user2)
    db_session.commit()
    
    # Проверка: одна identity, два memberships
    identities_count = db_session.query(Identity).filter(Identity.telegram_id == telegram_id).count()
    assert identities_count == 1, "Should have exactly one identity"
    
    users_count = db_session.query(User).filter(User.identity_id == identity.id).count()
    assert users_count == 2, "Should have two memberships"
    
    # Проверка: разные tier в разных tenants
    user1_db = db_session.query(User).filter(User.id == user1.id).first()
    user2_db = db_session.query(User).filter(User.id == user2.id).first()
    assert user1_db.tier == "pro"
    assert user2_db.tier == "free"
    
    print(f"✓ Identity: {identity.id}")
    print(f"✓ Membership 1 (tenant_1): {user1.id}, tier={user1_db.tier}")
    print(f"✓ Membership 2 (tenant_2): {user2.id}, tier={user2_db.tier}")


def test_unique_constraint_tenant_identity(db_session: Session, tenant_1: str):
    """Тест: уникальность (tenant_id, identity_id) - нельзя создать дубликат."""
    telegram_id = 111222333
    
    # 1) Создаём identity и membership
    identity = Identity(telegram_id=telegram_id)
    db_session.add(identity)
    db_session.commit()
    
    user1 = User(
        tenant_id=uuid.UUID(tenant_1),
        identity_id=identity.id,
        telegram_id=telegram_id,
        username="test"
    )
    db_session.add(user1)
    db_session.commit()
    
    # 2) Пытаемся создать дубликат membership (тот же tenant + identity)
    user2 = User(
        tenant_id=uuid.UUID(tenant_1),
        identity_id=identity.id,
        telegram_id=telegram_id,
        username="test_duplicate"
    )
    db_session.add(user2)
    
    # Должна быть ошибка уникальности
    with pytest.raises(Exception):  # IntegrityError или аналогичная
        db_session.commit()
    
    db_session.rollback()
    print("✓ Unique constraint (tenant_id, identity_id) works correctly")


def test_identity_telegram_id_unique(db_session: Session):
    """Тест: telegram_id уникален в identities."""
    telegram_id = 999888777
    
    # 1) Создаём первую identity
    identity1 = Identity(telegram_id=telegram_id)
    db_session.add(identity1)
    db_session.commit()
    
    # 2) Пытаемся создать вторую identity с тем же telegram_id
    identity2 = Identity(telegram_id=telegram_id)
    db_session.add(identity2)
    
    # Должна быть ошибка уникальности
    with pytest.raises(Exception):
        db_session.commit()
    
    db_session.rollback()
    print("✓ Unique constraint on identities.telegram_id works correctly")


def test_create_user_endpoint_creates_identity_and_membership(db_session: Session, tenant_1: str):
    """Тест: endpoint create_user создаёт identity и membership."""
    telegram_id = 555666777
    user_data = UserCreate(
        telegram_id=telegram_id,
        username="endpoint_test",
        first_name="Endpoint",
        last_name="Test"
    )
    
    # Временно переопределяем default_tenant_id для теста
    old_tenant_id = settings.default_tenant_id
    settings.default_tenant_id = uuid.UUID(tenant_1)
    
    try:
        # Создаём пользователя через endpoint
        response = create_user(user_data, db_session)
        
        # Проверка: identity создана
        identity = db_session.query(Identity).filter(Identity.telegram_id == telegram_id).first()
        assert identity is not None, "Identity should be created"
        
        # Проверка: membership создан
        user = db_session.query(User).filter(
            User.tenant_id == uuid.UUID(tenant_1),
            User.identity_id == identity.id
        ).first()
        assert user is not None, "Membership should be created"
        assert user.username == "endpoint_test"
        
        print(f"✓ create_user endpoint creates identity: {identity.id}")
        print(f"✓ create_user endpoint creates membership: {user.id}")
    finally:
        settings.default_tenant_id = old_tenant_id


def test_jwt_contains_tenant_membership_identity():
    """Тест: JWT токен содержит tenant_id, membership_id, identity_id, tier."""
    user_id = 123456789
    tenant_id = str(uuid.uuid4())
    membership_id = str(uuid.uuid4())
    identity_id = str(uuid.uuid4())
    tier = "pro"
    
    jwt_token = create_jwt(
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        identity_id=identity_id,
        tier=tier
    )
    
    # Декодируем JWT
    import jwt as jwt_lib
    payload = jwt_lib.decode(jwt_token, settings.jwt_secret, algorithms=["HS256"])
    
    # Проверка полей
    assert payload.get("sub") == str(user_id)
    assert payload.get("tenant_id") == tenant_id
    assert payload.get("membership_id") == membership_id
    assert payload.get("identity_id") == identity_id
    assert payload.get("tier") == tier
    
    print("✓ JWT contains all required fields: tenant_id, membership_id, identity_id, tier")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

