-- Seed initial data (development only)
-- Safe inserts with ON CONFLICT do nothing

INSERT INTO tenants (id, name, settings)
VALUES ('550e8400-e29b-41d4-a716-446655440000', 'Test Tenant', '{"plan": "development", "features": ["telegram_parsing", "rag"]}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, tenant_id, telegram_id, username, settings)
VALUES ('550e8400-e29b-41d4-a716-446655440001', '550e8400-e29b-41d4-a716-446655440000', 123456789, 'testuser', '{"notifications": true, "digest_frequency": "daily"}')
ON CONFLICT (id) DO NOTHING;

INSERT INTO channels (id, tenant_id, telegram_id, username, title, settings)
VALUES ('550e8400-e29b-41d4-a716-446655440002', '550e8400-e29b-41d4-a716-446655440000', -1001234567890, 'testchannel', 'Test Channel', '{"auto_parse": true, "digest_enabled": true}')
ON CONFLICT (id) DO NOTHING;


