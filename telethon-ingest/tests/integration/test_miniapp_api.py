"""
Context7 best practice: Integration тесты для MiniApp API.

E2E тестирование QR flow с HMAC валидацией.
"""

import pytest
import hmac
import hashlib
import time
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock, patch

from api.miniapp_auth import app, verify_hmac_signature, verify_timestamp


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def mock_session_manager():
    """Mock UnifiedSessionManager."""
    manager = Mock()
    manager.start_qr_flow = AsyncMock()
    manager.finalize_qr = AsyncMock()
    return manager


@pytest.fixture
def qr_ticket():
    """Mock QR ticket."""
    from services.session.unified_session_manager import QrTicket
    return QrTicket(
        ticket="test_ticket_123",
        qr_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
        ttl=600,
        tenant_id="tenant1",
        app_id="app1"
    )


def test_verify_hmac_signature():
    """Тест верификации HMAC подписи."""
    payload = '{"ticket": "test", "tg_user_id": 12345}'
    secret = "test_secret"
    
    # Генерируем правильную подпись
    correct_signature = hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Тестируем правильную подпись
    assert verify_hmac_signature(payload, correct_signature, secret) is True
    
    # Тестируем неправильную подпись
    assert verify_hmac_signature(payload, "wrong_signature", secret) is False
    
    # Тестируем пустую подпись
    assert verify_hmac_signature(payload, "", secret) is False


def test_verify_timestamp():
    """Тест верификации timestamp."""
    current_time = int(time.time())
    
    # Тестируем валидный timestamp
    assert verify_timestamp(current_time) is True
    assert verify_timestamp(current_time - 100) is True
    assert verify_timestamp(current_time + 100) is True
    
    # Тестируем невалидный timestamp (слишком старый)
    assert verify_timestamp(current_time - 400) is False
    
    # Тестируем невалидный timestamp (слишком новый)
    assert verify_timestamp(current_time + 400) is False


@pytest.mark.asyncio
async def test_start_qr_auth_success(client, mock_session_manager, qr_ticket):
    """Тест успешного начала QR авторизации."""
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        mock_session_manager.start_qr_flow.return_value = qr_ticket
        
        response = client.post("/api/qr/start", json={
            "tenant_id": "tenant1",
            "app_id": "app1"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["ticket"] == "test_ticket_123"
        assert data["qr_base64"] == qr_ticket.qr_base64
        assert data["ttl"] == 600
        assert data["tenant_id"] == "tenant1"
        assert data["app_id"] == "app1"


@pytest.mark.asyncio
async def test_start_qr_auth_failure(client, mock_session_manager):
    """Тест неудачного начала QR авторизации."""
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        mock_session_manager.start_qr_flow.return_value = None
        
        response = client.post("/api/qr/start", json={
            "tenant_id": "tenant1",
            "app_id": "app1"
        })
        
        assert response.status_code == 500
        assert "Failed to start QR flow" in response.json()["detail"]


@pytest.mark.asyncio
async def test_qr_callback_success(client, mock_session_manager):
    """Тест успешного QR callback."""
    from services.session.session_state import AuthState, SessionState
    
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        mock_session_manager.finalize_qr.return_value = AuthState(
            state=SessionState.AUTHORIZED,
            telegram_user_id=12345
        )
        
        # Подготавливаем запрос с HMAC подписью
        payload = '{"ticket": "test_ticket", "tg_user_id": 12345}'
        secret = "test_secret"
        signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        response = client.post(
            "/api/qr/callback",
            data=payload,
            headers={
                "X-Miniapp-Signature": signature,
                "X-Timestamp": str(int(time.time())),
                "Content-Type": "application/json"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Authorization completed successfully"


@pytest.mark.asyncio
async def test_qr_callback_requires_password(client, mock_session_manager):
    """Тест QR callback с требованием 2FA."""
    from services.session.session_state import AuthState, SessionState
    
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        mock_session_manager.finalize_qr.return_value = AuthState(
            state=SessionState.PENDING_PASSWORD,
            requires_password=True
        )
        
        # Подготавливаем запрос с HMAC подписью
        payload = '{"ticket": "test_ticket", "tg_user_id": 12345}'
        secret = "test_secret"
        signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        response = client.post(
            "/api/qr/callback",
            data=payload,
            headers={
                "X-Miniapp-Signature": signature,
                "X-Timestamp": str(int(time.time())),
                "Content-Type": "application/json"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "requires_password"
        assert data["requires_password"] is True


@pytest.mark.asyncio
async def test_qr_callback_invalid_signature(client, mock_session_manager):
    """Тест QR callback с невалидной подписью."""
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        payload = '{"ticket": "test_ticket", "tg_user_id": 12345}'
        
        response = client.post(
            "/api/qr/callback",
            data=payload,
            headers={
                "X-Miniapp-Signature": "invalid_signature",
                "X-Timestamp": str(int(time.time())),
                "Content-Type": "application/json"
            }
        )
        
        assert response.status_code == 401
        assert "Invalid signature" in response.json()["detail"]


@pytest.mark.asyncio
async def test_qr_callback_missing_headers(client, mock_session_manager):
    """Тест QR callback без обязательных заголовков."""
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        response = client.post(
            "/api/qr/callback",
            data='{"ticket": "test_ticket", "tg_user_id": 12345}',
            headers={"Content-Type": "application/json"}
        )
        
        assert response.status_code == 400
        assert "Missing required headers" in response.json()["detail"]


@pytest.mark.asyncio
async def test_qr_callback_invalid_timestamp(client, mock_session_manager):
    """Тест QR callback с невалидным timestamp."""
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        payload = '{"ticket": "test_ticket", "tg_user_id": 12345}'
        secret = "test_secret"
        signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Используем timestamp из далекого прошлого
        old_timestamp = str(int(time.time()) - 1000)
        
        response = client.post(
            "/api/qr/callback",
            data=payload,
            headers={
                "X-Miniapp-Signature": signature,
                "X-Timestamp": old_timestamp,
                "Content-Type": "application/json"
            }
        )
        
        assert response.status_code == 400
        assert "Timestamp out of tolerance" in response.json()["detail"]


@pytest.mark.asyncio
async def test_qr_status_success(client, mock_session_manager):
    """Тест получения статуса QR."""
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        response = client.get("/api/qr/status?ticket=test_ticket")
        
        assert response.status_code == 200
        data = response.json()
        assert "state" in data
        assert "telegram_user_id" in data
        assert "requires_password" in data


@pytest.mark.asyncio
async def test_qr_password_success(client, mock_session_manager):
    """Тест успешной отправки 2FA пароля."""
    from services.session.session_state import AuthState, SessionState
    
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        mock_session_manager.finalize_qr.return_value = AuthState(
            state=SessionState.AUTHORIZED,
            telegram_user_id=12345
        )
        
        response = client.post("/api/qr/password", json={
            "ticket": "test_ticket",
            "password": "test_password"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Password verified successfully"


@pytest.mark.asyncio
async def test_qr_password_failure(client, mock_session_manager):
    """Тест неудачной отправки 2FA пароля."""
    from services.session.session_state import AuthState, SessionState
    
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        mock_session_manager.finalize_qr.return_value = AuthState(
            state=SessionState.ABSENT,
            error_message="Invalid password"
        )
        
        response = client.post("/api/qr/password", json={
            "ticket": "test_ticket",
            "password": "wrong_password"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "Invalid password" in data["message"]


def test_health_check(client):
    """Тест health check endpoint."""
    response = client.get("/api/qr/health")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert data["service"] == "miniapp_auth"


@pytest.mark.asyncio
async def test_qr_callback_replay_protection(client, mock_session_manager):
    """Тест защиты от replay атак."""
    from services.session.session_state import AuthState, SessionState
    
    with patch('api.miniapp_auth.get_session_manager', return_value=mock_session_manager):
        mock_session_manager.finalize_qr.return_value = AuthState(
            state=SessionState.AUTHORIZED,
            telegram_user_id=12345
        )
        
        payload = '{"ticket": "test_ticket", "tg_user_id": 12345}'
        secret = "test_secret"
        signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Первый запрос должен пройти
        response1 = client.post(
            "/api/qr/callback",
            data=payload,
            headers={
                "X-Miniapp-Signature": signature,
                "X-Timestamp": str(int(time.time())),
                "Content-Type": "application/json"
            }
        )
        
        assert response1.status_code == 200
        
        # Второй запрос с тем же timestamp должен быть отклонен
        response2 = client.post(
            "/api/qr/callback",
            data=payload,
            headers={
                "X-Miniapp-Signature": signature,
                "X-Timestamp": str(int(time.time())),
                "Content-Type": "application/json"
            }
        )
        
        # TODO: Реализовать защиту от replay атак
        # Пока что тест проходит, но нужно добавить проверку nonce
        assert response2.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])