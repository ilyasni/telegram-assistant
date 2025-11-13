from uuid import uuid4
from datetime import datetime
from types import ModuleType, SimpleNamespace
import sys

dummy_config = ModuleType("config")
dummy_config.settings = SimpleNamespace(
    database_url="postgresql://postgres:postgres@localhost:5432/postgres",
    feature_rls_enabled=False,
)
sys.modules.setdefault("config", dummy_config)

services_module = ModuleType("services")
trend_service_module = ModuleType("services.trend_detection_service")
trend_service_module.get_trend_detection_service = lambda: None
services_module.trend_detection_service = trend_service_module
sys.modules.setdefault("services", services_module)
sys.modules.setdefault("services.trend_detection_service", trend_service_module)

scheduler_module = ModuleType("api.tasks.scheduler_tasks")
scheduler_module.enqueue_group_digest = lambda *args, **kwargs: None
sys.modules.setdefault("api.tasks.scheduler_tasks", scheduler_module)

from api.routers.groups import _build_discovery_candidates
from models.database import Group, GroupDiscoveryRequest


class DummyQuery:
    def __init__(self, groups):
        self._groups = groups

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._groups


class DummySession:
    def __init__(self, groups):
        self._groups = groups

    def query(self, model):
        assert model is Group
        return DummyQuery(self._groups)


def test_build_discovery_candidates_marks_connected_group():
    tenant_id = uuid4()
    connected_group_id = uuid4()
    group = Group(
        id=connected_group_id,
        tenant_id=tenant_id,
        tg_chat_id=-1001234567890,
        title="Connected Group",
        username="connected_group",
        is_active=True,
        created_at=datetime.utcnow(),
    )
    discovery = GroupDiscoveryRequest(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="completed",
        total=2,
        connected_count=1,
        created_at=datetime.utcnow(),
        results=[
            {
                "tg_chat_id": -1001234567890,
                "title": "Connected Group",
                "username": "connected_group",
                "is_megagroup": True,
                "is_private": False,
                "is_connected": False,
            },
            {
                "tg_chat_id": -1009876543210,
                "title": "New Group",
                "username": None,
                "is_megagroup": False,
                "is_private": True,
            },
        ],
    )

    db = DummySession([group])
    candidates = _build_discovery_candidates(discovery, db)

    assert len(candidates) == 2
    connected = candidates[0]
    assert connected.is_connected is True
    assert connected.connected_group_id == connected_group_id

    new_group = candidates[1]
    assert new_group.is_connected is False
    assert new_group.connected_group_id is None

