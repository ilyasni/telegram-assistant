-- Roles and Invite Codes

-- 1) Add role to users (admin|user)
ALTER TABLE users
ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'user',
ADD CONSTRAINT users_role_chk CHECK (role IN ('admin','user'));

-- 2) Invite codes table
CREATE TABLE IF NOT EXISTS invite_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(64) UNIQUE NOT NULL,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL DEFAULT 'user',
    uses_limit INT NOT NULL DEFAULT 1,
    uses_count INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by UUID REFERENCES users(id),
    last_used_at TIMESTAMPTZ,
    last_used_by UUID REFERENCES users(id),
    CONSTRAINT invite_codes_role_chk CHECK (role IN ('admin','user')),
    CONSTRAINT invite_codes_uses_chk CHECK (uses_limit >= 0 AND uses_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_invite_codes_tenant ON invite_codes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_invite_codes_active ON invite_codes(active);


