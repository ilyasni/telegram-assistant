import pytest

from api.tasks import scheduler_tasks


@pytest.fixture(autouse=True)
def reset_feature_flags(monkeypatch):
    """Сбрасывает флаги rollout между тестами."""
    monkeypatch.setattr(scheduler_tasks.settings, "digest_agent_enabled", False)
    monkeypatch.setattr(scheduler_tasks.settings, "digest_agent_canary_tenants", [])
    yield


def test_group_digest_disabled_by_default():
    assert scheduler_tasks._is_group_digest_enabled_for_tenant("11111111-1111-1111-1111-111111111111") is False


def test_group_digest_enabled_globally(monkeypatch):
    monkeypatch.setattr(scheduler_tasks.settings, "digest_agent_enabled", True)
    assert scheduler_tasks._is_group_digest_enabled_for_tenant("any-tenant") is True


def test_group_digest_enabled_for_canary_tenant(monkeypatch):
    tenant_uuid = "1D7A788C-5FC3-4A04-9D92-9D76C52D8110"
    monkeypatch.setattr(scheduler_tasks.settings, "digest_agent_canary_tenants", [tenant_uuid])
    assert scheduler_tasks._is_group_digest_enabled_for_tenant(tenant_uuid.lower()) is True
    assert scheduler_tasks._is_group_digest_enabled_for_tenant("3f6c0b3a-5f3a-4f93-a1e4-0fd3a3d2b6b5") is False

