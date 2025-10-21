-- Инициализация схемы для Telegram Assistant
-- Создание базовых таблиц с мульти-тенантностью

-- Таблица арендаторов
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settings JSONB DEFAULT '{}',
    
    CONSTRAINT tenants_name_check CHECK (length(name) >= 1)
);

-- Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ,
    settings JSONB DEFAULT '{}',
    
    CONSTRAINT users_telegram_id_check CHECK (telegram_id > 0)
);

-- Таблица каналов
CREATE TABLE IF NOT EXISTS channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    telegram_id BIGINT NOT NULL,
    username VARCHAR(255),
    title VARCHAR(500) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settings JSONB DEFAULT '{}',
    
    UNIQUE(tenant_id, telegram_id),
    CONSTRAINT channels_telegram_id_check CHECK (telegram_id < 0)
);

-- Таблица постов
CREATE TABLE IF NOT EXISTS posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    telegram_message_id BIGINT NOT NULL,
    content TEXT,
    media_urls JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_processed BOOLEAN NOT NULL DEFAULT false,
    
    UNIQUE(tenant_id, channel_id, telegram_message_id),
    CONSTRAINT posts_telegram_message_id_check CHECK (telegram_message_id > 0)
);

-- Таблица статуса индексации
CREATE TABLE IF NOT EXISTS indexing_status (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    embedding_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    graph_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    processing_started_at TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    
    CONSTRAINT indexing_status_embedding_status_check 
        CHECK (embedding_status IN ('pending', 'processing', 'completed', 'failed')),
    CONSTRAINT indexing_status_graph_status_check 
        CHECK (graph_status IN ('pending', 'processing', 'completed', 'failed'))
);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);

CREATE INDEX IF NOT EXISTS idx_channels_tenant_id ON channels(tenant_id);
CREATE INDEX IF NOT EXISTS idx_channels_telegram_id ON channels(telegram_id);
CREATE INDEX IF NOT EXISTS idx_channels_is_active ON channels(is_active);
CREATE INDEX IF NOT EXISTS idx_channels_last_message_at ON channels(last_message_at);

CREATE INDEX IF NOT EXISTS idx_posts_tenant_id ON posts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_posts_channel_id ON posts(channel_id);
CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_posts_is_processed ON posts(is_processed);

CREATE INDEX IF NOT EXISTS idx_indexing_status_post_id ON indexing_status(post_id);
CREATE INDEX IF NOT EXISTS idx_indexing_status_embedding_status ON indexing_status(embedding_status);
CREATE INDEX IF NOT EXISTS idx_indexing_status_graph_status ON indexing_status(graph_status);

-- RLS политики (Row Level Security) - отключены для простоты в dev режиме
-- В production нужно будет создать роли и настроить политики
-- ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE users ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE channels ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE posts ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE indexing_status ENABLE ROW LEVEL SECURITY;
