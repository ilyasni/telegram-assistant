--
-- PostgreSQL database dump
--

-- Dumped from database version 15.1 (Ubuntu 15.1-1.pgdg20.04+1)
-- Dumped by pg_dump version 15.7 (Ubuntu 15.7-1.pgdg20.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: _shadow; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA _shadow;


ALTER SCHEMA _shadow OWNER TO postgres;

--
-- Name: SCHEMA _shadow; Type: COMMENT; Schema: -; Owner: postgres
--

COMMENT ON SCHEMA _shadow IS 'Артефакты миграции на глобальные каналы/посты';


--
-- Name: graphql_public; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA graphql_public;


ALTER SCHEMA graphql_public OWNER TO postgres;

--
-- Name: storage; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA storage;


ALTER SCHEMA storage OWNER TO postgres;

--
-- Name: telegram_bot; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA telegram_bot;


ALTER SCHEMA telegram_bot OWNER TO postgres;

--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN 
  NEW.updated_at = NOW(); 
  RETURN NEW; 
END; $$;


ALTER FUNCTION public.set_updated_at() OWNER TO postgres;

--
-- Name: sync_post_has_media(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.sync_post_has_media() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  UPDATE posts p
     SET has_media = EXISTS(SELECT 1 FROM post_media pm WHERE pm.post_id = p.id)
   WHERE p.id = COALESCE(NEW.post_id, OLD.post_id);
  RETURN NULL;
END; $$;


ALTER FUNCTION public.sync_post_has_media() OWNER TO postgres;

--
-- Name: update_post_metrics(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.update_post_metrics() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Обновляем счётчики на основе связанных таблиц
    UPDATE posts SET
        reactions_count = (
            SELECT COUNT(DISTINCT CONCAT(reaction_type, ':', reaction_value))
            FROM post_reactions 
            WHERE post_id = NEW.post_id
        ),
        forwards_count = (
            SELECT COUNT(*) 
            FROM post_forwards 
            WHERE post_id = NEW.post_id
        ),
        replies_count = (
            SELECT COUNT(*) 
            FROM post_replies 
            WHERE post_id = NEW.post_id
        ),
        last_metrics_update = NOW()
    WHERE id = NEW.post_id;
    
    RETURN NEW;
END; $$;


ALTER FUNCTION public.update_post_metrics() OWNER TO postgres;

--
-- Name: update_telegram_sessions_updated_at(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.update_telegram_sessions_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_telegram_sessions_updated_at() OWNER TO postgres;

--
-- Name: update_users_telegram_auth_updated_at(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.update_users_telegram_auth_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.telegram_auth_updated_at = NOW();
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_users_telegram_auth_updated_at() OWNER TO postgres;

--
-- Name: update_yyyymm(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.update_yyyymm() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NEW.posted_at IS NOT NULL THEN
    NEW.yyyymm = (EXTRACT(YEAR FROM NEW.posted_at)::INT * 100) + EXTRACT(MONTH FROM NEW.posted_at)::INT;
  END IF;
  RETURN NEW;
END; $$;


ALTER FUNCTION public.update_yyyymm() OWNER TO postgres;

--
-- Name: upsert_telegram_session(character varying, character varying, text, character varying, character varying, text, text); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying DEFAULT 'pending'::character varying, p_auth_error text DEFAULT NULL::text, p_error_details text DEFAULT NULL::text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    session_id UUID;
BEGIN
    -- Пытаемся обновить существующую запись
    UPDATE telegram_sessions 
    SET session_string_enc = p_session_string_enc,
        key_id = p_key_id,
        status = p_status,
        auth_error = p_auth_error,
        error_details = p_error_details,
        updated_at = now()
    WHERE user_id = p_user_id
    RETURNING id INTO session_id;
    
    -- Если не нашли, создаем новую
    IF session_id IS NULL THEN
        INSERT INTO telegram_sessions (
            tenant_id, user_id, session_string_enc, key_id, status,
            auth_error, error_details
        ) VALUES (
            p_tenant_id, p_user_id, p_session_string_enc, p_key_id, p_status,
            p_auth_error, p_error_details
        ) RETURNING id INTO session_id;
    END IF;
    
    RETURN session_id;
END;
$$;


ALTER FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text) OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: channel_mapping; Type: TABLE; Schema: _shadow; Owner: postgres
--

CREATE TABLE _shadow.channel_mapping (
    old_channel_id uuid,
    new_channel_id uuid,
    tg_channel_id bigint
);


ALTER TABLE _shadow.channel_mapping OWNER TO postgres;

--
-- Name: post_mapping; Type: TABLE; Schema: _shadow; Owner: postgres
--

CREATE TABLE _shadow.post_mapping (
    old_post_id uuid,
    new_post_id uuid
);


ALTER TABLE _shadow.post_mapping OWNER TO postgres;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- Name: channels; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.channels (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tg_channel_id bigint,
    username character varying(255),
    title character varying(500) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    last_message_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb,
    CONSTRAINT channels_telegram_id_check CHECK ((tg_channel_id < 0))
);


ALTER TABLE public.channels OWNER TO postgres;

--
-- Name: TABLE channels; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.channels IS 'Глобальные каналы (без tenant_id), доступ через user_channel';


--
-- Name: encryption_keys; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.encryption_keys (
    key_id character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    retired_at timestamp with time zone
);


ALTER TABLE public.encryption_keys OWNER TO postgres;

--
-- Name: TABLE encryption_keys; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.encryption_keys IS 'Ключи шифрования для Telegram StringSession (поддержка ротации)';


--
-- Name: group_mentions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.group_mentions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    group_message_id uuid NOT NULL,
    mentioned_user_id uuid NOT NULL,
    mentioned_user_tg_id bigint NOT NULL,
    context_snippet text,
    is_processed boolean DEFAULT false NOT NULL,
    processed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.group_mentions OWNER TO postgres;

--
-- Name: TABLE group_mentions; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.group_mentions IS 'Упоминания пользователей в группах';


--
-- Name: group_messages; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.group_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    group_id uuid NOT NULL,
    tg_message_id bigint NOT NULL,
    sender_tg_id bigint,
    sender_username character varying(255),
    content text,
    posted_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.group_messages OWNER TO postgres;

--
-- Name: TABLE group_messages; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.group_messages IS 'Сообщения из групповых чатов';


--
-- Name: groups; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    tg_chat_id bigint NOT NULL,
    title character varying(500) NOT NULL,
    username character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    last_checked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb
);


ALTER TABLE public.groups OWNER TO postgres;

--
-- Name: TABLE groups; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.groups IS 'Групповые чаты для мониторинга упоминаний';


--
-- Name: indexing_status; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.indexing_status (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    post_id uuid NOT NULL,
    embedding_status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    graph_status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    processing_started_at timestamp with time zone,
    processing_completed_at timestamp with time zone,
    error_message text,
    retry_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT indexing_status_embedding_status_check CHECK (((embedding_status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT indexing_status_graph_status_check CHECK (((graph_status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying])::text[])))
);


ALTER TABLE public.indexing_status OWNER TO postgres;

--
-- Name: invite_codes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.invite_codes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    code character varying(64) NOT NULL,
    tenant_id uuid NOT NULL,
    role character varying(16) DEFAULT 'user'::character varying NOT NULL,
    uses_limit integer DEFAULT 1 NOT NULL,
    uses_count integer DEFAULT 0 NOT NULL,
    expires_at timestamp with time zone,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    last_used_at timestamp with time zone,
    last_used_by uuid,
    CONSTRAINT invite_codes_role_chk CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'user'::character varying])::text[]))),
    CONSTRAINT invite_codes_uses_chk CHECK (((uses_limit >= 0) AND (uses_count >= 0)))
);


ALTER TABLE public.invite_codes OWNER TO postgres;

--
-- Name: outbox_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.outbox_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_type character varying(100) NOT NULL,
    payload jsonb NOT NULL,
    aggregate_id uuid NOT NULL,
    content_hash character varying(64) NOT NULL,
    idempotency_key character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    schema_version character varying(10) DEFAULT 'v1'::character varying NOT NULL,
    trace_id character varying(100),
    processed_at timestamp with time zone
);


ALTER TABLE public.outbox_events OWNER TO postgres;

--
-- Name: post_enrichment; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.post_enrichment (
    post_id uuid NOT NULL,
    tags jsonb DEFAULT '[]'::jsonb,
    vision_labels jsonb DEFAULT '[]'::jsonb,
    ocr_text text,
    crawl_md text,
    enrichment_provider character varying(50),
    enriched_at timestamp with time zone DEFAULT now() NOT NULL,
    enrichment_latency_ms integer,
    metadata jsonb DEFAULT '{}'::jsonb,
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.post_enrichment OWNER TO postgres;

--
-- Name: TABLE post_enrichment; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_enrichment IS 'Обогащённые данные постов: теги, OCR, vision, crawl результаты';


--
-- Name: post_forwards; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.post_forwards (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    post_id uuid NOT NULL,
    from_chat_id bigint,
    from_message_id bigint,
    from_chat_title text,
    from_chat_username text,
    forwarded_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.post_forwards OWNER TO postgres;

--
-- Name: TABLE post_forwards; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_forwards IS 'Репосты постов в другие чаты/каналы';


--
-- Name: post_media; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.post_media (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    post_id uuid NOT NULL,
    media_type character varying(50) NOT NULL,
    media_url text NOT NULL,
    thumbnail_url text,
    file_size_bytes bigint,
    width integer,
    height integer,
    duration_seconds integer,
    tg_file_id text,
    tg_file_unique_id text,
    sha256 bytea,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_media_type CHECK (((media_type)::text = ANY ((ARRAY['photo'::character varying, 'video'::character varying, 'document'::character varying])::text[])))
);


ALTER TABLE public.post_media OWNER TO postgres;

--
-- Name: TABLE post_media; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_media IS 'Медиа-файлы постов с Telegram-специфичными идентификаторами';


--
-- Name: post_reactions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.post_reactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    post_id uuid NOT NULL,
    reaction_type character varying(50) NOT NULL,
    reaction_value text NOT NULL,
    user_tg_id bigint,
    is_big boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.post_reactions OWNER TO postgres;

--
-- Name: TABLE post_reactions; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_reactions IS 'Реакции на посты (эмодзи, кастомные эмодзи, платные)';


--
-- Name: post_replies; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.post_replies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    post_id uuid NOT NULL,
    reply_to_post_id uuid,
    reply_message_id bigint NOT NULL,
    reply_chat_id bigint NOT NULL,
    reply_author_tg_id bigint,
    reply_author_username text,
    reply_content text,
    reply_posted_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.post_replies OWNER TO postgres;

--
-- Name: TABLE post_replies; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_replies IS 'Комментарии/ответы на посты';


--
-- Name: posts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.posts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    channel_id uuid NOT NULL,
    tg_message_id bigint NOT NULL,
    content text,
    media_urls jsonb DEFAULT '[]'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_processed boolean DEFAULT false NOT NULL,
    posted_at timestamp with time zone,
    url text,
    has_media boolean DEFAULT false NOT NULL,
    yyyymm integer,
    views_count integer DEFAULT 0,
    forwards_count integer DEFAULT 0,
    reactions_count integer DEFAULT 0,
    replies_count integer DEFAULT 0,
    is_pinned boolean DEFAULT false,
    is_edited boolean DEFAULT false,
    edited_at timestamp with time zone,
    post_author text,
    reply_to_message_id bigint,
    reply_to_chat_id bigint,
    via_bot_id bigint,
    via_business_bot_id bigint,
    is_silent boolean DEFAULT false,
    is_legacy boolean DEFAULT false,
    noforwards boolean DEFAULT false,
    invert_media boolean DEFAULT false,
    last_metrics_update timestamp with time zone DEFAULT now(),
    CONSTRAINT posts_telegram_message_id_check CHECK ((tg_message_id > 0))
);

ALTER TABLE ONLY public.posts FORCE ROW LEVEL SECURITY;


ALTER TABLE public.posts OWNER TO postgres;

--
-- Name: TABLE posts; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.posts IS 'Глобальные посты (без tenant_id), доступ через user_channel + RLS';


--
-- Name: COLUMN posts.views_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.views_count IS 'Количество просмотров поста';


--
-- Name: COLUMN posts.forwards_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.forwards_count IS 'Количество репостов поста';


--
-- Name: COLUMN posts.reactions_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.reactions_count IS 'Количество уникальных реакций';


--
-- Name: COLUMN posts.replies_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.replies_count IS 'Количество комментариев';


--
-- Name: COLUMN posts.is_pinned; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.is_pinned IS 'Закреплён ли пост в канале';


--
-- Name: COLUMN posts.is_edited; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.is_edited IS 'Был ли пост отредактирован';


--
-- Name: COLUMN posts.post_author; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.post_author IS 'Автор поста (если доступен)';


--
-- Name: COLUMN posts.last_metrics_update; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.last_metrics_update IS 'Время последнего обновления метрик';


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.schema_migrations (
    version character varying(14) NOT NULL
);


ALTER TABLE public.schema_migrations OWNER TO postgres;

--
-- Name: telegram_auth_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.telegram_auth_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    event character varying(64) NOT NULL,
    reason character varying(255),
    ip character varying(64),
    user_agent character varying(512),
    at timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb DEFAULT '{}'::jsonb
);


ALTER TABLE public.telegram_auth_events OWNER TO postgres;

--
-- Name: TABLE telegram_auth_events; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.telegram_auth_events IS 'События авторизации Telegram (упрощенная версия)';


--
-- Name: telegram_auth_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.telegram_auth_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    event character varying(64) NOT NULL,
    reason character varying(255),
    error_code character varying(64),
    ip character varying(64),
    user_agent character varying(512),
    latency_ms integer,
    at timestamp with time zone DEFAULT now() NOT NULL,
    meta jsonb DEFAULT '{}'::jsonb
);


ALTER TABLE public.telegram_auth_logs OWNER TO postgres;

--
-- Name: TABLE telegram_auth_logs; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.telegram_auth_logs IS 'Аудит событий авторизации Telegram (QR/miniapp)';


--
-- Name: telegram_sessions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.telegram_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id character varying(255) NOT NULL,
    user_id character varying(255),
    session_string_enc text NOT NULL,
    key_id character varying(64) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    auth_error text,
    error_details text
);


ALTER TABLE public.telegram_sessions OWNER TO postgres;

--
-- Name: TABLE telegram_sessions; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.telegram_sessions IS 'Зашифрованные Telethon StringSession на арендатора/пользователя';


--
-- Name: COLUMN telegram_sessions.session_string_enc; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.telegram_sessions.session_string_enc IS 'Зашифрованная StringSession от Telethon';


--
-- Name: COLUMN telegram_sessions.key_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.telegram_sessions.key_id IS 'ID ключа шифрования для расшифровки session_string_enc';


--
-- Name: COLUMN telegram_sessions.status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.telegram_sessions.status IS 'Статус сессии: pending|authorized|revoked|expired|failed';


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tenants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb,
    CONSTRAINT tenants_name_check CHECK ((length((name)::text) >= 1))
);


ALTER TABLE public.tenants OWNER TO postgres;

--
-- Name: user_channel; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.user_channel (
    user_id uuid NOT NULL,
    channel_id uuid NOT NULL,
    subscribed_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb
);


ALTER TABLE public.user_channel OWNER TO postgres;

--
-- Name: TABLE user_channel; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.user_channel IS 'Many-to-many связь пользователей и каналов для подписок';


--
-- Name: user_group; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.user_group (
    user_id uuid NOT NULL,
    group_id uuid NOT NULL,
    monitor_mentions boolean DEFAULT true NOT NULL,
    subscribed_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    settings jsonb DEFAULT '{}'::jsonb
);


ALTER TABLE public.user_group OWNER TO postgres;

--
-- Name: TABLE user_group; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.user_group IS 'Подписки пользователей на группы';


--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    telegram_id bigint NOT NULL,
    username character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_active_at timestamp with time zone,
    settings jsonb DEFAULT '{}'::jsonb,
    telegram_session_enc text,
    telegram_session_key_id character varying(64),
    telegram_auth_status character varying(20) DEFAULT 'pending'::character varying,
    telegram_auth_created_at timestamp with time zone,
    telegram_auth_updated_at timestamp with time zone,
    telegram_auth_error text,
    first_name character varying(255),
    last_name character varying(255),
    role character varying(16) DEFAULT 'user'::character varying NOT NULL,
    tier character varying(20) DEFAULT 'free'::character varying,
    CONSTRAINT users_role_chk CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'user'::character varying])::text[]))),
    CONSTRAINT users_telegram_id_check CHECK ((telegram_id > 0))
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: COLUMN users.username; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.username IS 'Username пользователя в Telegram (@username)';


--
-- Name: COLUMN users.telegram_session_enc; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.telegram_session_enc IS 'Зашифрованная StringSession от Telethon';


--
-- Name: COLUMN users.telegram_session_key_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.telegram_session_key_id IS 'ID ключа шифрования для расшифровки session_string_enc';


--
-- Name: COLUMN users.telegram_auth_status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.telegram_auth_status IS 'Статус авторизации: pending|authorized|revoked|expired|failed';


--
-- Name: COLUMN users.first_name; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.first_name IS 'Имя пользователя из Telegram';


--
-- Name: COLUMN users.last_name; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.last_name IS 'Фамилия пользователя из Telegram';


--
-- Name: channels; Type: VIEW; Schema: telegram_bot; Owner: postgres
--

CREATE VIEW telegram_bot.channels AS
 SELECT channels.id,
    channels.tg_channel_id AS telegram_id,
    channels.username,
    channels.title,
    channels.is_active,
    channels.last_message_at,
    channels.created_at,
    channels.settings
   FROM public.channels;


ALTER TABLE telegram_bot.channels OWNER TO postgres;

--
-- Name: indexing_status; Type: VIEW; Schema: telegram_bot; Owner: postgres
--

CREATE VIEW telegram_bot.indexing_status AS
 SELECT indexing_status.id,
    indexing_status.post_id,
    indexing_status.embedding_status,
    indexing_status.graph_status,
    indexing_status.processing_started_at,
    indexing_status.processing_completed_at,
    indexing_status.error_message,
    indexing_status.retry_count
   FROM public.indexing_status;


ALTER TABLE telegram_bot.indexing_status OWNER TO postgres;

--
-- Name: posts; Type: VIEW; Schema: telegram_bot; Owner: postgres
--

CREATE VIEW telegram_bot.posts AS
 SELECT posts.id,
    posts.channel_id,
    posts.tg_message_id AS telegram_message_id,
    posts.content,
    posts.media_urls,
    posts.posted_at,
    posts.url,
    posts.has_media,
    posts.created_at,
    posts.is_processed
   FROM public.posts;


ALTER TABLE telegram_bot.posts OWNER TO postgres;

--
-- Name: tenants; Type: VIEW; Schema: telegram_bot; Owner: postgres
--

CREATE VIEW telegram_bot.tenants AS
 SELECT tenants.id,
    tenants.name,
    tenants.created_at,
    tenants.settings
   FROM public.tenants;


ALTER TABLE telegram_bot.tenants OWNER TO postgres;

--
-- Name: users; Type: VIEW; Schema: telegram_bot; Owner: postgres
--

CREATE VIEW telegram_bot.users AS
 SELECT users.id,
    users.tenant_id,
    users.telegram_id,
    users.username,
    users.created_at,
    users.last_active_at,
    users.settings
   FROM public.users;


ALTER TABLE telegram_bot.users OWNER TO postgres;

--
-- Data for Name: channel_mapping; Type: TABLE DATA; Schema: _shadow; Owner: postgres
--

COPY _shadow.channel_mapping (old_channel_id, new_channel_id, tg_channel_id) FROM stdin;
33333333-3333-3333-3333-333333333333	33333333-3333-3333-3333-333333333333	-1001234567890
\.


--
-- Data for Name: post_mapping; Type: TABLE DATA; Schema: _shadow; Owner: postgres
--

COPY _shadow.post_mapping (old_post_id, new_post_id) FROM stdin;
\.


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.alembic_version (version_num) FROM stdin;
b44bd6941d11
\.


--
-- Data for Name: channels; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.channels (id, tg_channel_id, username, title, is_active, last_message_at, created_at, settings) FROM stdin;
11c77f6b-2a54-4139-a20b-43d8a7950f34	\N	@AGI_and_RL	@AGI_and_RL	t	\N	2025-10-24 16:52:16.731331+00	{}
630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	\N	@business_ru	@business_ru	t	\N	2025-10-24 17:14:55.377865+00	{}
7f194a2a-5206-4348-b42d-1b3976ec7d43	\N	@okolo_art	@okolo_art	t	\N	2025-10-24 17:15:32.398271+00	{}
\.


--
-- Data for Name: encryption_keys; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.encryption_keys (key_id, created_at, retired_at) FROM stdin;
default_key_1761146055.690612	2025-10-22 15:14:15.690612+00	\N
\.


--
-- Data for Name: group_mentions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.group_mentions (id, group_message_id, mentioned_user_id, mentioned_user_tg_id, context_snippet, is_processed, processed_at, created_at) FROM stdin;
\.


--
-- Data for Name: group_messages; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.group_messages (id, group_id, tg_message_id, sender_tg_id, sender_username, content, posted_at, created_at) FROM stdin;
\.


--
-- Data for Name: groups; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.groups (id, tenant_id, tg_chat_id, title, username, is_active, last_checked_at, created_at, settings) FROM stdin;
\.


--
-- Data for Name: indexing_status; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.indexing_status (id, post_id, embedding_status, graph_status, processing_started_at, processing_completed_at, error_message, retry_count) FROM stdin;
\.


--
-- Data for Name: invite_codes; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.invite_codes (id, code, tenant_id, role, uses_limit, uses_count, expires_at, active, created_at, created_by, last_used_at, last_used_by) FROM stdin;
\.


--
-- Data for Name: outbox_events; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.outbox_events (id, event_type, payload, aggregate_id, content_hash, idempotency_key, created_at, schema_version, trace_id, processed_at) FROM stdin;
\.


--
-- Data for Name: post_enrichment; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_enrichment (post_id, tags, vision_labels, ocr_text, crawl_md, enrichment_provider, enriched_at, enrichment_latency_ms, metadata, updated_at) FROM stdin;
\.


--
-- Data for Name: post_forwards; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_forwards (id, post_id, from_chat_id, from_message_id, from_chat_title, from_chat_username, forwarded_at, created_at) FROM stdin;
\.


--
-- Data for Name: post_media; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_media (id, post_id, media_type, media_url, thumbnail_url, file_size_bytes, width, height, duration_seconds, tg_file_id, tg_file_unique_id, sha256, created_at) FROM stdin;
\.


--
-- Data for Name: post_reactions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_reactions (id, post_id, reaction_type, reaction_value, user_tg_id, is_big, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: post_replies; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_replies (id, post_id, reply_to_post_id, reply_message_id, reply_chat_id, reply_author_tg_id, reply_author_username, reply_content, reply_posted_at, created_at) FROM stdin;
\.


--
-- Data for Name: posts; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.posts (id, channel_id, tg_message_id, content, media_urls, created_at, is_processed, posted_at, url, has_media, yyyymm, views_count, forwards_count, reactions_count, replies_count, is_pinned, is_edited, edited_at, post_author, reply_to_message_id, reply_to_chat_id, via_bot_id, via_business_bot_id, is_silent, is_legacy, noforwards, invert_media, last_metrics_update) FROM stdin;
0357b00a-3872-4d54-b95c-ef203f8c56d3	11c77f6b-2a54-4139-a20b-43d8a7950f34	1216	Помним ли мы Genie - ворлд моделс от дипмаинда?\nСтатья выходила в феврале 2024\n\n**Genie: Generative Interactive Environments**\nhttps://arxiv.org/abs/2402.15391\nhttps://www.alphaxiv.org/ru/overview/2402.15391v1\n\n(в августе вот уже 3ю версию анонсили https://deepmind.google/discover/blog/genie-3-a-new-frontier-for-world-models/)\n\nофициального имплемента не выкладывали,\nно есть неофициальный \nhttps://github.com/myscience/open-genie\n\nТак вот - на этот раз чел сделал так сказать минималистичную учебную реализацию, так что мы можем сами поизучать и чего-нибудь потренить\n\nhttps://github.com/AlmondGod/tinyworlds\n\nВсем кому тема интересна считаю обязательно стоит покопаться\n\nставим автору звездочки, изучаем и делаем свои ворлмоделсы (для RLя конечно же 🎩)	["photo:5411248194542763534"]	2025-10-05 15:09:11+00	f	2025-10-05 15:09:11+00	\N	t	202510	2225	52	0	0	f	t	2025-10-05 15:28:57+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.490698+00
8abdbef5-0244-46eb-9237-c8dced704757	11c77f6b-2a54-4139-a20b-43d8a7950f34	1213	**Vistral-24B-Instruct**\n\n**Vistral** - это наша новая флагманская унимодальная LLM представляющая из себя улучшенную версию **Mistral-Small-3.2-24B-Instruct-2506** командой VikhrModels, адаптированную преимущественно для русского и английского языков. Удалён визуальный энкодер, убрана мультимодальность. Сохранена стандартная архитектура **MistralForCausalLM** без изменений в базовой структуре модели.\n\n🔗 **Карточка модели**: https://huggingface.co/Vikhrmodels/Vistral-24B-Instruct\n🔗 **GGUF (скоро)**: https://huggingface.co/Vikhrmodels/Vistral-24B-Instruct-GGUF\n⚖️ **Лицензия**: apache-2.0\n\n**Сайт**: https://vikhr.org\n**Донаты**: [Здесь](https://www.tbank.ru/cf/3W1Ko1rj8ah)\n\n👥 **Авторы**: @LakoMoorDev @nlpwanderer	["photo:5388788620846040247"]	2025-09-29 09:05:33+00	f	2025-09-29 09:05:33+00	\N	t	202509	1945	19	0	0	f	t	2025-09-29 09:44:13+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.498828+00
7e48d2a7-3563-4a5b-a2ae-66fd8094346c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1206	Vikhr Borealis - первая русскоязычная открытая audio llm\n\nМы долго и не очень успешно развивали свой tts - Salt, от него исторически осталось довольно много данных и наработок, мы решили - чо бы не сварить asr + llm как модно?\n\nНу и сварили. Архитектурно - whisper + qwen, учили на 7к часов аудио только адаптер+llm, сейчас работает только в ASR режиме, позже возможно довезем инструктивный режим. Так же выйдет бенчмарк для русского asr, он пока в доработке. \nБлог так же выйдет, там будут небольшие аблейшены по данным\n\nМодель в данный момент бьет whisperы на русском и на части бенчей лучше чем gigam. \n\n[Модель](https://huggingface.co/Vikhrmodels/Borealis)\n[Сolab поиграться](https://colab.research.google.com/drive/1ac7apyGO24iAYMwg3DLcqLZRjo-w4QWf?usp=sharing)	["photo:5341377730995944248"]	2025-09-12 12:09:11+00	f	2025-09-12 12:09:11+00	\N	t	202509	2274	24	0	0	f	t	2025-09-12 12:09:57+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.51774+00
79972a07-941a-4c9a-9ef9-e4ee4a84b88e	11c77f6b-2a54-4139-a20b-43d8a7950f34	1199	**Наш русскоязычный датасет для TTS опубликован!**\n\nСегодня выкладываем открытые корпуса на 4000+ часов речи, а еще синтезатор речи ESpeech-TTS-1\n\nНаш датасет содержит больше 4000 часов русской речи. Статистика по корпусам:\n\n**Многоголосые:**\n[ESpeech-podcasts](https://huggingface.co/datasets/ESpeech/ESpeech-podcasts) - 3200 часов\n[ESpeech-webinars](https://huggingface.co/datasets/ESpeech/ESpeech-webinars2) - 850 часов\n\n**Одноголосые:**\n[ESpeech-igm](https://huggingface.co/datasets/ESpeech/ESpeech-igm) - 220 часов\n[ESpeech-buldjat ](https://huggingface.co/datasets/ESpeech/ESpeech-buldjat)- 54 часа\n[ESpeech-upvote](https://huggingface.co/datasets/ESpeech/ESpeech-upvote) - 296 часов\n[ESpeech-tuchniyzhab](https://huggingface.co/datasets/ESpeech/ESpeech-tuchniyzhab) - 306 часов\n\nДанные лежат вот тут: https://huggingface.co/ESpeech\n\nТехрепорт датасета доступен тут: https://github.com/Den4ikAI/ESpeech/blob/main/ESpeech_techreport.pdf\n\n\nТакже, мы решили провести некоторые эксперименты с TTS. Получилось обучить F5-TTS на 10000 часов речи и сделать одну из лучших по нашим замерам моделей в опенсурсе для русского языка. \n\n**Какие модели доступны?**\n[ESpeech-TTS-1 [RL] V1 ](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V1)- Первая версия модели с RL\n[ESpeech-TTS-1 [RL] V2 ](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V2)- Вторая версия модели с RL\n[ESpeech-TTS-1 PODCASTER [SFT]](https://huggingface.co/ESpeech/ESpeech-TTS-1_podcaster) - Модель обученная только на подкастах, лучше генерирует спонтанную речь\n[ESpeech-TTS-1 [SFT] 95K ](https://huggingface.co/ESpeech/ESpeech-TTS-1_SFT-95K)- чекпоинт с 95000 шагов (на нем основана RL V1)\n[ESpeech-TTS-1 [SFT] 265K](https://huggingface.co/ESpeech/ESpeech-TTS-1_SFT-256K) - чекпоинт с 265000 шагов (на нем основана RL V2)\n\nЛайкайте модель которая больше понравится чтобы мы понимали есть ли смысл запускать RL.\n\n**Послушать модели без скачивания можно вот здесь:**\n\n[https://huggingface.co/spaces/Den4ikAI/ESpeech-TTS\n](https://huggingface.co/spaces/Den4ikAI/ESpeech-TTS)\nСовместно с @speech_recognition_ru ещё сделали **лидерборд русского ТТС**, где можно глянуть метрики:\n\n[https://huggingface.co/spaces/ESpeech/open_tts_leaderboard_ru](https://huggingface.co/spaces/ESpeech/open_tts_leaderboard_ru)\nЗадать вопросы по поводу данных и модели можно в наших телеграм каналах:\n[https://t.me/den4ikresearch](https://t.me/den4ikresearch)\n[https://t.me/voice_stuff_chat](https://t.me/voice_stuff_chat)\n\nВы можете мне задонатить, чтобы у меня были ресурсы делать более крутые модели и датасеты:\n\nUSDT (TRC20): TEpEM4VVmGmqKHn4Xz1FxM7qZiXjWtUEUB\nBTC: bc1qw5lq7fc455e47hggax6zp8txw4ru7yvsxvawv3\nhttps://www.tbank.ru/cf/7WKnNMqWtOx	["video:5289698025449492088", "document:5289698025449492088"]	2025-08-26 10:41:53+00	f	2025-08-26 10:41:53+00	\N	t	202508	2371	44	0	0	f	t	2025-10-02 19:50:35+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.532678+00
698e850b-f5af-477a-9480-217b28d5b940	11c77f6b-2a54-4139-a20b-43d8a7950f34	1191	Сегодня мы выложили улучшенную версию RefalMachine/RuadaptQwen3-4B-Instruct 🎉\n\nМодель стала лучше по всем фронтам: \n1️⃣ На бенчмарке по каждой категории рост, в частности, на математике.\n2️⃣ Стабильность модели повысилась (меньше циклов).\n3️⃣ На арене также наблюдается рост (при снижении средней длины ответа!). \n\nТекущая версия (v2) на данный момент вероятно SoTA для русского языка среди всех тюнов и/или адаптаций 4B модели (на основании нашего бенчмарка). От исходной версии присутствуют небольшие отставания, однако на арене RuadaptQwen3-4B-Instruct стабильно обходит Qwen3-4B, а скорость генерации русскоязычного текста существенно лучше. Бенч можно посмотреть по ссылке (там без арены) https://huggingface.co/datasets/RefalMachine/llmtf_open_benchmark\n\nУлучшения связаны с более качественным post-training, включая использование нового SFT датасета (T-Wix), а также добавление preference-tune шага.\n\nВеса в основном репозитории и GGUF также обновлены:\nhttps://huggingface.co/RefalMachine/RuadaptQwen3-4B-Instruct\nhttps://huggingface.co/RefalMachine/RuadaptQwen3-4B-Instruct-GGUF	["photo:5192681092715508345"]	2025-07-24 16:37:27+00	f	2025-07-24 16:37:27+00	\N	t	202507	2942	33	0	0	f	t	2025-07-24 16:37:59+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.552125+00
3b5295c7-40b0-4bbd-98d0-028a6321bf3c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1175	Пара приятных и полезных находок\n\nВведение в диффузионки\n\n**Step-by-Step Diffusion: An Elementary Tutorial**\nhttps://arxiv.org/abs/2406.08929\n\nИ обзор методов скрытых рассуждений в ллмках (т.е. когда ллмы "рассуждают" не текстом в лицо, а во внутренних представлениях модельки)\nВ целом какие подходы бывают, как тренируют и про интерпретируемость\n\n**A Survey on Latent Reasoning**\nhttps://arxiv.org/abs/2507.06203\nhttps://www.alphaxiv.org/ru/overview/2507.06203v1\n\nhttps://github.com/multimodal-art-projection/LatentCoT-Horizon	["photo:6016102412999310160"]	2025-07-10 15:18:41+00	f	2025-07-10 15:18:41+00	https://arxiv.org/abs/2406.08929	t	202507	2381	103	0	0	f	t	2025-07-10 16:29:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.588549+00
01467111-5fc4-4442-b204-2f7ab230e98f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24657	**Все хотят приобрести студию Warner Bros, чтобы получить права на библиотеку с Гарри Поттером, DC и сериалами HBO, — Bloomberg.\n**\nОпубликован полный список претендентов на покупку студии:\n- Apple,\n-Amazon,\n- Netflix,\n- NBCUniversal,\n- Paramount.\n\nНа данный момент фаворитом считается Paramount, которую возглавляет Дэвид Эллисон — сын одного из самых богатых людей планеты, Ларри Эллисона. Эллисон пообещал гендиректору Warner Bros Дэвиду Заславу, что в случае продажи тот сохранит свою должность.\n\nЕсли победит Apple, то, вероятно, в «Гарри Поттере» появится реклама iPhone.	["photo:5462903925125544784"]	2025-10-23 17:33:20+00	f	2025-10-23 17:33:20+00	\N	t	202510	5806	27	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.884664+00
d90f9f64-6783-4918-aace-338bc384a606	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24650	Сотрудник Apple по имени Сэм Санг (Sam Sung), прославившийся благодаря своей визитке с надписью «Sam Sung — Apple», сменил фамилию после интернет-хайпа.\n\nРаботая в Apple Store в Ванкувере, он стал вирусной сенсацией — ведь сочетание его имени с брендом конкурента выглядело как шутка судьбы. Через 12 лет он рассказал, что тогда специально не увольнялся, чтобы не подогревать внимание.\n\nТеперь он носит фамилию Струан и признаётся: та история до сих пор всплывает в разговорах, где бы он ни работал.	["photo:5463286014006129840"]	2025-10-23 12:35:50+00	f	2025-10-23 12:35:50+00	\N	t	202510	5947	38	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.901703+00
e452227e-78e4-43c5-8edf-26c0c86751f2	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24645	Власти согласились не вводить НДС для сделок с российским ПО из реестра Минцифры. Меру планируют исключить из налогового законопроекта ко второму чтению.\n\nО сохранении льготы просили участники ИТ-отрасли. Они предупреждали, что изменения приведут к сокращению выручки, оттоку кадров и закрытиям.	["photo:5460972663016126831"]	2025-10-23 08:47:06+00	f	2025-10-23 08:47:06+00	\N	t	202510	6039	52	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.912448+00
9f8690fa-66f8-40d5-8b7c-297a66827dc4	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24641	**📈**** Полный список налоговых поправок. Сегодня Госдума приняла их в первом чтении.\n**\n— НДС поднимают с 20 до 22%.\n\n— Порог УСН для бизнеса уменьшают с 60 млн доходов в год до 10 млн. Бизнес, который зарабатывает больше 10 млн в год, должен будет платить НДС.\n\n— Ставка акцизов будет проиндексирована на уровень инфляции в 2026 и 2027 годах.\n\n— Акцизы на алкоголь и сигареты поднимут выше уровня инфляции.\n\n— Льготы НДФЛ от продажи имущества отменяют. Теперь от НДФЛ освобождают при владении имуществом минимум от трёх лет.\n\n— Тариф взносов для IT-компаний увеличивается с 7,6% до 15%.\n\n— Налоговики получат право осматривать территорию и помещения компаний под налоговым мониторингом.\n\n— Поправки существенно расширяют рамки мониторинга. Теперь для участия в нем будет достаточно соответствовать одному из критериев — по выручке, активам или сумме уплаченных налогов, тогда как сейчас необходимо выполнение всех трех.\n\n— Упрощаются условия получения отсрочек, рассрочек и инвестиционных налоговых кредитов. Пакет документов сократят, а срок инвестиционного налогового кредита увеличивается с 5 до 10 лет.\n\n— Поправки к законопроекту принимаются до 10 ноября.\n\nПоправки должны вступить в силу с 1 января 2026 года.	[]	2025-10-23 05:45:29+00	f	2025-10-23 05:45:29+00	\N	f	202510	6850	226	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.921445+00
25785edc-bc0a-43f9-9346-19af6ea30afb	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24632	Google потерял около **$100 миллиардов капитализации** после анонса нового проекта OpenAI — браузера **Atlas**. Акции компании упали на **4%**.\n\nAtlas — это «умный браузер» со встроенным ChatGPT, который может **искать информацию, писать тексты, делать саммари и заполнять таблицы**. Его ключевая идея — **один запрос, один точный ответ**, без рекламы и бесконечных переходов между сайтами.\n\nСейчас Atlas доступен на **macOS**, а вскоре появится на **iOS, Android и Windows**. В планах — **интеграция с почтой и Google Sheets**, что делает его прямым конкурентом сервисам Google.	["photo:5461000103562179235"]	2025-10-22 11:01:12+00	f	2025-10-22 11:01:12+00	\N	t	202510	6570	74	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.94+00
d9196d0e-78a2-45a4-b55d-4da970cd9ab0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24623	**8-летний нью-йоркский предприниматель Линус Пипмейер покоряет рынок вендинга.\n**\nМальчик создает значки собственного дизайна — с панорамами города и уличными мотивами. Он сам придумывает рисунки, оцифровывает, печатает, собирает и загружает их в гача-автомат, который установил в центре Нью-Йорка.\n\nМаркетинг у юного бизнесмена не хуже взрослого: он расклеивает объявления, рисует стрелки мелом и рассказывает прохожим, где стоит его автомат. У Линуса уже появились фанаты, следящие за новыми «коллекциями».\n\nНедавно он открыл сайт и начал продавать мерч с собственными принтами.	["photo:5458809292284099790"]	2025-10-22 05:15:23+00	f	2025-10-22 05:15:23+00	\N	t	202510	6392	83	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.958815+00
2b8ba8da-333e-43ec-8edc-1dc24f3f2be8	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24618	В Японии запустили приложение для состоятельных мужчин, которое помогает им искать спутниц.\nРазработчики обещают полную проверку анкет: девушки проходят верификацию личности и внешности, а мужчины могут зарегистрироваться только после подтверждения дохода — не менее 5,3 миллиона рублей в год.\n\nСоздатели сервиса называют его «элитной альтернативой Tinder» и уверяют, что цель проекта — «соединять успешных мужчин и женщин, ценящих статус и честность».	["video:5456582617572867621", "document:5456582617572867621"]	2025-10-21 14:31:45+00	f	2025-10-21 14:31:45+00	\N	t	202510	6811	106	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.970249+00
3e64acd9-1377-4a59-b488-b76ac99355c3	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24613	В России криптовалюту могут приравнять к совместно нажитому имуществу. Согласно законопроекту депутата Игоря Антропенко, цифровые активы, купленные в браке, будут считаться общей собственностью супругов. Исключение составят монеты, приобретённые до свадьбы или полученные безвозмездно. Инициатива направлена на защиту прав при разводах, поскольку криптовалюта всё чаще используется как инструмент инвестиций и накоплений.	[]	2025-10-21 11:31:11+00	f	2025-10-21 11:31:11+00	\N	f	202510	6278	39	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.98071+00
b39f0551-00d4-4f39-b233-4f1d76496021	7f194a2a-5206-4348-b42d-1b3976ec7d43	1219	📰  **Лувр ограбили: из главного музея Франции сегодня похитили 9 драгоценностей**\n\nИсчезли предметы из коллекции вещей, принадлежавших __Наполеону Бонопарту,__ его супруге __Жозефине__ и другим монархам. Речь, в частности, идет об ожерелье, броши и диадеме.\n\nГрабители проникли в музей утром. Трое или четверо злоумышленников в масках подъехали к музею **на скутерах** со стороны Сены, где ведутся ремонтные работы. Используя автолестницу, они поднялись до окон на втором этаже и **разбили** их, проникнув в галерею. Затем грабители с помощью небольших **бензопил** вскрыли витрины с драгоценностями. В общей сложности ограбление длилось всего** 7 минут.**\n\nСогласно последним данным, один из похищенных предметов был** найден неподалеку** от Лувра. Речь идет о короне императрицы Евгении де Монтихо, супруги Наполеона III. Из-за ограбления музей был закрыт для посещения на весь день. Никто из-за этого происшествия не пострадал, хотя сообщалось о панике внутри музея в момент ограбления.\n\n🎭  [**__@artnews_tg__**](https://t.me/+x7FBBnWsOFhjYzdi)** **— __новости искусства. самое важное и интересное. подписывайтесь.__\n\n[источник](https://www.lemonde.fr/societe/article/2025/10/19/le-musee-du-louvre-victime-d-un-braquage-et-ferme-pour-la-journee-annonce-la-ministre-de-la-culture_6648121_3224.html)	["photo:5449708359663091491"]	2025-10-19 17:09:57+00	f	2025-10-19 17:09:57+00	\N	t	202510	30	0	0	6	f	t	2025-10-19 17:10:01+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.411676+00
6f06618f-92d2-4fa2-b3cf-8bc6fa2636b4	7f194a2a-5206-4348-b42d-1b3976ec7d43	1216	Знаете, самая моя слабая тема в искусстве это символизм. \n\nНу никак мне не удаётся разглядеть образ Христа в запеченой рыбе на натюрмортах художников. Даже учитывая, что я помню про ИХТИС и ИНРИ, всё равно для меня это сопоставимо с конспирологией. Не доросла, видимо. \n\nПравда, есть одно исключение — искусство Китая. \n\nВ Поднебесной религия особенно не прижилась. Даже буддизм был принесён извне. Главные темы в китайском искусстве — природа и взаимодействие с ней человека. Поскольку общество Китая оставалось аграрным вплоть до двадцатого века.\n\nСчиталось, что искусство некое зеркало природы, способное либо опустошить, либо обновить художника духовно. Отсюда вдохновляющие и благородные тематики. Или социально нравоучительные функции, например, в портретах и фресках. Когда изображались мудрые императоры и их злые противоположности. \n\nИскусство Китая никогда не стремилось к фотографической точности и реализму. Изобразить внутреннюю сущность объекта было важнее.\n\nСимволизм там вполне понятный, я бы даже сказала, конкретный: \n\nБамбук олицетворяет дух (гнётся, но не ломается), дракон — символ императора, журавль — долголетия, пара уток — верность в браке, орхидея — символ чистоты и верности, а сосна символизирует стойкость и т. д. \n\nРассцвет китайского искусства пришёлся на период с 618 — 906 годы н. э. \nВо времена правления династии Тан. \nЕсли будете гуглить, ищите "Империя Тан". \n\nТам император Тай-цзун расширил империю вглубь Центральной Азии и до Кореи. Потом передал наследие сыну, и тот тоже постарался. Искусство и технологии развивались шустро. Люди жили в мире и гармонии. \n\nНо не все. \n\nПоэтому династию потом свергли. А вот достижения остались. Потому то мы и можем теперь разбирать на символы изображения на китайском фарфоре и наслаждаться атмосферой древних китайских пейзажей.\n\n#искусство	["photo:5449772560834231955"]	2025-10-18 11:15:46+00	f	2025-10-18 11:15:46+00	\N	t	202510	51	1	0	0	f	t	2025-10-18 11:21:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.418127+00
5e9091d2-1f60-4ab0-aa52-6689a74b0a63	11c77f6b-2a54-4139-a20b-43d8a7950f34	1223		[]	2025-10-21 18:14:29+00	f	2025-10-21 18:14:29+00	\N	f	202510	1226	2	0	1	f	t	2025-10-21 18:14:32+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:20.965984+00
cb42b3d6-fcde-4973-9c87-5909b971687b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1222	https://openai.com/index/introducing-chatgpt-atlas/\n\n(он так и не написал чатгпт нормально)	["photo:5458755781286560587"]	2025-10-21 18:13:27+00	f	2025-10-21 18:13:27+00	\N	t	202510	1246	8	0	1	f	t	2025-10-21 19:02:52+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.475455+00
db0e00be-e162-4ff8-9085-8ccdda2ee455	11c77f6b-2a54-4139-a20b-43d8a7950f34	1221	заходите смотреть как ллмки делают деньги в реалтайме\nhttps://nof1.ai/\n\nмоделькам дали всем один промт и по 10к$ и отпустили трейдить, ну и вот\n\nувидел в https://t.me/j_links/8169	["photo:5449393139128335028"]	2025-10-18 11:11:22+00	f	2025-10-18 11:11:22+00	\N	t	202510	5315	337	0	13	f	t	2025-10-18 11:12:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.477908+00
405ae69b-897d-40c2-8833-cb6e5f866fdd	11c77f6b-2a54-4139-a20b-43d8a7950f34	1220	https://www.arxiv.org/abs/2509.19162\n\nМы выложили на архив третью статью проекта CayleyPy. \n(Первая статья была принята на самую топовую конференцию [NeaurIPS как spotlight](https://t.me/sberlogabig/596) - то есть в топ3%.)\n\nА также представляем первый релиз нашей библиотеки - CayleyPy - для работы методами МЛ/RL с графами размера гугл: https://github.com/cayleypy/cayleypy (Кидайте звезды  ⭐⭐  на наш гитхаб - они нам очень помогут !) Библиотека также ставится через pypi: https://pypi.org/project/cayleypy/ . \n\nСама статья с упором на математику - предложено около 200 новых математических гипотез полученных с помощью вычислительных экспериментов с нашей библиотекой, которая позволяет делать расчеты - которые за пределами существовавших ранее систем компьютерной алгебры.  Если у Вас есть знакомые математики занимающиеся теорий групп или графов - свяжите их с нами - @alexander_v_c  . [Slides at Oberwolfach](https://docs.google.com/presentation/d/1wI4XY9s-Y6L5qfpCMpFb1wMeon-7c8u0BMt1QZAjxd8/edit?usp=sharing).\n\nА также мы рады всем добровольцам - кто знает Питон или математику и имеет несколько свободных часов  - будем рады всем участникам - пинганите @alexander_v_c\n\nЧтобы бенчмаркать методы и одновременно двигать математику и биоинформатику - мы создали более 10 челленжей на Каггл.\nВсем кому интересен Каггл  - тоже присоединяйтесь\nhttps://www.kaggle.com/competitions/cayleypy-christophers-jewel\nhttps://www.kaggle.com/competitions/cayleypy-glushkov\nhttps://www.kaggle.com/competitions/CayleyPy-pancake\nhttps://www.kaggle.com/competitions/cayleypy-transposons	["photo:5388615215836429818"]	2025-10-15 18:36:31+00	f	2025-10-15 18:36:31+00	\N	t	202510	1972	25	0	0	f	t	2025-10-15 18:37:22+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.480343+00
99999c80-03f3-4958-8d92-1893cf3bffba	11c77f6b-2a54-4139-a20b-43d8a7950f34	1219	Опа, квен3вл 4б (и там ещё 8б)\nДо этого были только 30б и 235б - жирновато\n\nА тут и в домашний комп влезет\n\nhttps://huggingface.co/Qwen/Qwen3-VL-4B-Instruct	["photo:6013898222898101179"]	2025-10-14 18:37:51+00	f	2025-10-14 18:37:51+00	https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct	t	202510	2125	70	0	4	f	t	2025-10-15 05:43:30+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.483366+00
e9592709-064d-4839-883e-85f66000290a	11c77f6b-2a54-4139-a20b-43d8a7950f34	1218	А еще погенерил прикольную (на мой вкус) документацию с подробностями и базой, реально может стать понятнее если прям ничего не знаете.\nзацените	["document:5435888434348592775"]	2025-10-14 12:26:06+00	f	2025-10-14 12:26:06+00	\N	t	202510	1823	12	0	0	f	t	2025-10-15 05:43:24+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.485897+00
bd1115f9-d7d8-401c-8791-d71f816c34c8	11c77f6b-2a54-4139-a20b-43d8a7950f34	1217	Я считаю что нам всем нужен симулятор атомного реактора. Вот (ну конечно пока в начально виде)\n\nhttps://github.com/researchim-ai/atomic-sim\n\nЯ например ничего про них не знаю. Ллмки кое-чего знают и помогают.\n\nВ целом это такой заход в симы и енвайроменты для промышленности\nОпенсурс конечно же\n\nТо есть делаем симчик, потом в gym и генерим данные чтобы потом затачивать ллмки/рл в какой-то области\n\nСейчас реактор - потому что интересно и я особо такого не видел\n\n(хотя охота конечно вообще в целом станцию сделать, но пока далековато до этого)\n\nВ ресечим кстати делаются разные интересные проектики (в том числе и для прома еще один, про него расскажу чутка позже) https://t.me/researchim\nНу и стараюсь собирать статьи	["photo:5438285894139383451"]	2025-10-14 12:25:10+00	f	2025-10-14 12:25:10+00	\N	t	202510	1873	27	0	5	f	t	2025-10-15 05:43:19+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.488114+00
c385d8eb-4ec0-4003-b42e-d5c571cb8080	11c77f6b-2a54-4139-a20b-43d8a7950f34	1174	Ну шьто, как вам грок 4 кто уже трогал?	[]	2025-07-10 12:47:41+00	f	2025-07-10 12:47:41+00	\N	f	202507	1777	2	0	3	f	t	2025-07-10 13:19:03+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.590837+00
b7945933-58d8-4e2a-98da-803f7d08bf9f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1173		["photo:5440724477785796064"]	2025-07-10 12:47:03+00	f	2025-07-10 12:47:03+00	\N	t	202507	2089	10	0	0	f	t	2025-07-10 12:47:06+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.592762+00
bed45bd0-1205-47f3-8201-35611b253f10	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24658	Трамп помиловал основателя Binance Чанпэн Чжао.	["photo:5462903925125544944"]	2025-10-23 18:01:46+00	f	2025-10-23 18:01:46+00	\N	t	202510	5669	25	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.8826+00
1d6a676c-4478-4b1c-85e2-ff317a90d01e	11c77f6b-2a54-4139-a20b-43d8a7950f34	1215	Щас смотрю, нравится\nhttps://www.youtube.com/watch?v=nzsRVwgx2vo	["photo:5835715328460569245"]	2025-10-02 05:06:28+00	f	2025-10-02 05:06:28+00	https://www.youtube.com/watch?v=nzsRVwgx2vo	t	202510	2404	33	0	0	f	t	2025-10-02 05:06:51+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.494336+00
43a1b69c-38b3-4fc8-9504-96ceaf760a89	11c77f6b-2a54-4139-a20b-43d8a7950f34	1214	Скоро покупочки в чатегпт\nhttps://openai.com/index/buy-it-in-chatgpt/	["photo:5975254976206846895"]	2025-09-30 05:25:27+00	f	2025-09-30 05:25:27+00	https://openai.com/index/buy-it-in-chatgpt/	t	202509	2345	10	0	3	f	t	2025-09-30 05:27:09+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.496445+00
b01f3a6b-7971-43aa-8c55-04e70353895b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1212	unsloth завезли ноутбук с рлем для gpt-oss моделек\nВ примере учат ллмку рлем писать более оптимизированные CUDA-кернелы\n\nhttps://docs.unsloth.ai/new/gpt-oss-reinforcement-learning	["photo:5971183501829060902"]	2025-09-28 02:19:50+00	f	2025-09-28 02:19:50+00	https://docs.unsloth.ai/new/gpt-oss-reinforcement-learning	t	202509	2148	85	0	0	f	t	2025-09-28 02:48:43+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.501232+00
d951b65c-2f2f-498b-aa4e-771fe39b84f4	11c77f6b-2a54-4139-a20b-43d8a7950f34	1211	RL должен быть в школьной программе	[]	2025-09-25 08:27:10+00	f	2025-09-25 08:27:10+00	\N	f	202509	2370	14	0	11	f	t	2025-09-25 08:27:20+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.503466+00
270db607-b928-43a7-a6bb-5e87052f9be9	11c77f6b-2a54-4139-a20b-43d8a7950f34	1210	Тут выходила работа от стенфордских\n\nАгент делает других агентов прямо из научных статей\n\nПишет код для MCP сервера, всякие тулы, сам тестит\n\nПоказывают на примере AlphaGenome и говрят что в результате полученный агент 100% на примерах из статьи выбивает\nАгент реализовал все нужное за 3 часа\n\nПо-моему ну прям хорошо\n\n**Paper2Agent: Reimagining Research Papers As Interactive and Reliable AI Agents**\nhttps://arxiv.org/abs/2509.06917\nhttps://www.alphaxiv.org/ru/overview/2509.06917v1\n\nhttps://github.com/jmiao24/Paper2Agent	["photo:5379793176916784027"]	2025-09-25 03:15:55+00	f	2025-09-25 03:15:55+00	\N	t	202509	3172	139	0	0	f	t	2025-10-02 19:48:32+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.505544+00
2cfdde07-51e5-4012-a945-593fdc2ffe14	11c77f6b-2a54-4139-a20b-43d8a7950f34	1209	**Poker Battle**. Прогресс за третью, четвёртую и пятую недели.\n\n__Надеюсь ни у кого не осталось сомнений, что я не буду регулярно писать в канал ))__\n\nПо ощущениям проект готов процентов на 80%. Значит, остались последние 80%.\n\nЧто готово:\n* LLM-игроки\n* Заметки игроков друг на друга\n* Лог событий за столом\n* Статистика сессии\n* Поддержка нескольких столов\n\nОсталось сделать всё сложить в красивый интерфейс для просмотра.\n\nТем не менее я определился с датой старта — **27 октября**. Оставшийся месяц я потрачу на доделки, тесты и промо.\n\nСегодня сделал лендинг: https://www.pokerbattle.ai/\n\nЕсли у вас есть контакты людей из AI или покер related компаний, которые могли бы стать спонсорами, делитесь :)	["photo:5950600944804805246"]	2025-09-24 15:57:32+00	f	2025-09-24 15:57:32+00	https://www.pokerbattle.ai/	t	202509	2007	26	0	0	f	t	2025-09-24 16:19:11+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.507803+00
d4063284-2a3b-4e41-8a9b-d9a07a79ef5f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1208	М прикольновое\n\nКвены дропнули Qwen3Guard модельки для анализа промтов и ответов моделек на сейфти\n\n0.6B 4B 8B\n\n```Обнаружение в реальном времени: Qwen3Guard-Stream специально оптимизирован для потоковой передачи данных, обеспечивая эффективную и своевременную модерацию при инкрементальной генерации токенов.\n\nТрёхуровневая классификация серьёзности: обеспечивает детальную оценку рисков, разделяя выходные данные на безопасные, спорные и небезопасные уровни серьёзности, что позволяет адаптировать их к различным сценариям развертывания.\n\nМногоязыковая поддержка: поддерживает 119 языков и диалектов, обеспечивая стабильную работу в глобальных и кросс-языковых приложениях.```\nhttps://qwen.ai/blog?id=f0bbad0677edf58ba93d80a1e12ce458f7a80548&from=research.research-list\n\nhttps://huggingface.co/collections/Qwen/qwen3guard-68d2729abbfae4716f3343a1\n\nhttps://github.com/QwenLM/Qwen3Guard	["photo:5375428790064381009"]	2025-09-24 07:22:56+00	f	2025-09-24 07:22:56+00	\N	t	202509	2568	79	0	0	f	t	2025-09-24 07:28:46+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.510903+00
d3014129-403a-42e8-8e58-242d55c2a1f3	11c77f6b-2a54-4139-a20b-43d8a7950f34	1207	Тут опять учат квадрокоптеры летать рлем\nпричем в нейронке всего 2084 параметров и она норм работает на 10 разных квадрокоптерах\n\nВидосик тут\nhttps://www.reddit.com/r/robotics/comments/1njl25z/raptor_a_foundation_policy_for_quadrotor_control/\n\nRAPTOR: A Foundation Policy for Quadrotor Control\nhttps://arxiv.org/abs/2509.11481\nhttps://www.alphaxiv.org/ru/overview/2509.11481v1	["photo:5936978137470581332"]	2025-09-18 20:46:02+00	f	2025-09-18 20:46:02+00	https://www.reddit.com/r/reinforcementlearning/s/plgpZd7Zm9	t	202509	2899	81	0	1	f	t	2025-09-18 20:50:19+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.515412+00
67e2eba2-2d3c-41f1-ade7-f0f4de3d0dcd	7f194a2a-5206-4348-b42d-1b3976ec7d43	1221		["photo:5456425667039197464"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	29	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.407887+00
496e39fb-3567-4185-bec9-bbd2fb795a2c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1205	Надеюсь ребята RLем буду заниматься	[]	2025-09-08 19:21:04+00	f	2025-09-08 19:21:04+00	\N	f	202509	3067	4	0	1	f	t	2025-09-08 19:21:28+00	\N	1203	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.520796+00
0c51ba82-d4fc-40bd-814d-ebdaca484b7d	11c77f6b-2a54-4139-a20b-43d8a7950f34	1204	Поздравляем) 🥳	[]	2025-09-08 19:19:21+00	f	2025-09-08 19:19:21+00	\N	f	202509	3102	4	0	0	f	t	2025-09-08 19:19:48+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.522987+00
f1d60b79-b25a-426e-a46d-8587c65cecbe	11c77f6b-2a54-4139-a20b-43d8a7950f34	1203	https://spbu.ru/news-events/novosti/studenty-spbgu-stali-pobeditelyami-mezhdunarodnogo-chempionata-po	[]	2025-09-08 19:18:23+00	f	2025-09-08 19:18:23+00	\N	f	202509	3275	6	0	0	f	t	2025-09-08 19:19:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.525221+00
e00ececa-4cd6-4867-8885-479cc26a165b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1201	опять обзор агентного ллмного рля\nценой всего\n\n**The Landscape of Agentic Reinforcement Learning for LLMs: A Survey**\nhttps://arxiv.org/abs/2509.02547\nhttps://www.alphaxiv.org/ru/overview/2509.02547v1\n\nhttps://github.com/xhyumiracle/Awesome-AgenticLLM-RL-Papers	["photo:5321366086794473624"]	2025-09-05 09:27:25+00	f	2025-09-05 09:27:25+00	\N	t	202509	4255	120	0	1	f	t	2025-09-05 09:28:39+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.527868+00
219110b2-9e72-43ff-ac7a-94b088dddc62	11c77f6b-2a54-4139-a20b-43d8a7950f34	1200		["video:5289698025449492073", "document:5289698025449492073"]	2025-08-26 10:41:53+00	f	2025-08-26 10:41:53+00	\N	t	202508	2762	43	0	0	f	t	2025-08-26 10:41:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.530354+00
570b4caf-eb53-4556-ac4a-eb446a52170c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1198	RL пушить надо	[]	2025-08-20 19:56:12+00	f	2025-08-20 19:56:12+00	\N	f	202508	2930	6	0	2	f	t	2025-08-20 19:58:32+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.536337+00
11fced41-c9d9-4729-8246-5530809970b5	11c77f6b-2a54-4139-a20b-43d8a7950f34	1197	Смешное из подкаста с ex-CTO OpenAI, Greg Brockman. Он рассказал про времена, когда компания занималась разработкой ботов для DOTA 2:\n\n— Мы хотели разработать новые RL алгоритмы, потому что всем в тот момент времени было очевидно, что тогдашние методы не масштабировались. Все знали это. Я помню мой коллега сказал: «а почему это так? Кто-то проверял? Мы правда это знаем?». Я тогда ответил, мол, да, это наш бейзлайн, мы должны отмасштабировать текущий метод и отталкиваться от него. Я помню, как приходил в офис каждую неделю: они удваивали количество ядер на сервере, они играли больше игр, рейтинг агента рос и рос. Я говорил, что нужно продолжать, пока мы не упрёмся в стену. А потом уже можно пойти заняться интересными вещами.\n\nИ мы так и не упёрлись в стену...\n\n(прим.: у них по итогу работал тот же метод, PPO, что они придумали ранее. И им же годы спустя дообучали LLM-ки следовать инструкциям. И, вероятно, им же — или его модификацией — учат агентов / рассуждения. GRPO от DeepSeek — это модификация PPO)\n\n[Клип](https://youtube.com/clip/Ugkx665gtfANA0SRKppuNnvscrbgzKQf6cH7?si=jMcp5lHc0aeXRqsj) (не знал эту историю, решил поделиться)	["photo:5837834074482391600"]	2025-08-20 19:55:46+00	f	2025-08-20 19:55:46+00	https://youtube.com/clip/Ugkx665gtfANA0SRKppuNnvscrbgzKQf6cH7?si=jMcp5lHc0aeXRqsj	t	202508	2611	20	0	2	f	t	2025-08-20 20:04:40+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.538684+00
4551b82a-631b-496a-865d-94e942fe2e24	11c77f6b-2a54-4139-a20b-43d8a7950f34	1196	https://gemini.google.com/app\nВпервые запустил кста\n\nPS все уже, не воркает	["photo:6034048143575419678"]	2025-08-15 21:19:22+00	f	2025-08-15 21:19:22+00	https://gemini.google.com/app	t	202508	3041	16	0	0	f	t	2025-08-16 14:23:06+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.541106+00
11ff9a4d-6de2-4ead-b0ef-a886b9b471e5	11c77f6b-2a54-4139-a20b-43d8a7950f34	1195	Может кому интересно про сравнение архитектур gpt-oss с GPT2 и Квенов недавних\nhttps://magazine.sebastianraschka.com/p/from-gpt-2-to-gpt-oss-analyzing-the?utm_campaign=posts-open-in-app	["photo:5817735792200628591"]	2025-08-11 16:42:46+00	f	2025-08-11 16:42:46+00	https://magazine.sebastianraschka.com/p/from-gpt-2-to-gpt-oss-analyzing-the	t	202508	4065	101	0	0	f	t	2025-08-11 16:43:07+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.543332+00
8c133af9-4cf2-487e-9310-1afdb8626deb	11c77f6b-2a54-4139-a20b-43d8a7950f34	1194	я вот только вспомнил\nСейчас же RL конференция проходит!\n\nСтатьи https://rlj.cs.umass.edu/2025/2025issue.html	["photo:5235802688921402340"]	2025-08-07 20:33:37+00	f	2025-08-07 20:33:37+00	\N	t	202508	4009	19	0	0	f	t	2025-08-07 20:35:24+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.545619+00
b64de121-1358-45d7-aa72-1139f6d6398c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1193	**QVikhr-3-8B-Instruction** \n\nПополнение еще одной моделью на базе **Qwen 3**. В **DOoM**, **QVikhr-3-8B-Instruction** получила оценку 0.445, что существенно превосходит результат базовой модели **Qwen3-8B**. Модель подходит для решения задач по математике и физике на русском языке.\n\n🔗 **Карточка модели:** https://huggingface.co/Vikhrmodels/QVikhr-3-8B-Instruction\n🔗 **GGUF (скоро):** https://huggingface.co/Vikhrmodels/QVikhr-3-8B-Instruction-GGUF\n⚖️ **Лицензия: **apache-2.0\n\nСайт: https://vikhr.org\nДонаты: [Здесь](https://www.tbank.ru/cf/3W1Ko1rj8ah)\n\n👥 Авторы: @LakoMoorDev @nlpwanderer	["photo:5231148305877169242"]	2025-08-06 14:19:51+00	f	2025-08-06 14:19:51+00	\N	t	202508	3138	12	0	0	f	t	2025-08-06 14:59:58+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.547897+00
76bb6f80-33f4-4bb1-928d-e21e2abbbf88	11c77f6b-2a54-4139-a20b-43d8a7950f34	1192	Мне кажется, что опенаи скинут опенсурсную модельку сегодня	[]	2025-08-05 18:43:16+00	f	2025-08-05 18:43:16+00	\N	f	202508	2470	4	0	6	f	t	2025-08-05 18:43:30+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.550026+00
808856a7-97f7-43ab-b2de-9f7c0fba740b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1190	шьто ни день то новые соты опенсурсы\n\nОбнова самой большой модельки от квенов\n\nhttps://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507\n\nЩас бы дипсику чонить выложить. Или ОпенАИ	["photo:5474237437581587760"]	2025-07-21 20:51:29+00	f	2025-07-21 20:51:29+00	\N	t	202507	2799	6	0	2	f	t	2025-07-21 20:53:59+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.555039+00
1bf48881-0bae-4a74-8ca7-e14ee0faee6c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1189	Каждая рандомная группа челов автоматически становится новой LLM RL лабой\n\nCUDA-L1: Improving CUDA Optimization via Contrastive Reinforcement Learning\nhttps://arxiv.org/abs/2507.14111\nhttps://www.alphaxiv.org/ru/overview/2507.14111v1	["photo:5472253398914037574"]	2025-07-21 14:07:35+00	f	2025-07-21 14:07:35+00	\N	t	202507	2761	13	0	0	f	t	2025-07-21 14:13:18+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.55716+00
26c40906-8f94-4fbc-afc7-754009231bc1	11c77f6b-2a54-4139-a20b-43d8a7950f34	1188	🚀 Уважаемые коллеги,  кому интересна математика и машинное обучение,  приглашаем Вас принять участие в неформальном научном проекте.\n\nМы разрабатываем новые методы и опен-соурс библиотеку CayleyPy, которая на основе МЛ/РЛ методов позволяет решить математические задачи, которые были  не доступны ранее. Как пример наша система уже по всем параметрам  на порядки превсходит аналогичные методы в системе компьютерной алгебры GAP   (де-факто стандарт)  - использующую алгоритмы доработанные самим Д. Кнутом.\n\nЕсли у Вас желание поучаствовать в проекте,  есть знание Питона и несколько свободных часов в неделю - то присоединяйтесь к нам - при активной работе - Вы будете соавтором научных публикаций. (Напишите @alexander_v_c - к.ф.-м.н. Александр Червов).\n\nКраткая суть задачи может быть описана несколькими способами - нахождение пути на графе размером  10^20-10^200 (из-за размера  обычные методы не применимы - только МЛ/РЛ). Решение пазла типа кубика Рубика, задача сортировки, математически - разложение элемента группы по образующим  - все это в реальности одна и та же  задача. Задача близка к прошедшему конкурсу [Каггл Санта 2023](https://www.kaggle.com/competitions/santa-2023). Более общо - это задача планирования - типичная для реинфорсмент ленинг - спланировать действия так чтобы кумулятивный эффект давал лучший результат - управлением манипулятором робота, системы АльфаГо, АльфаТензор, успех DeepSeek  - это задачи - тесно связанные с тем, что мы делаем.\n\nА зачем это нужно биологам ? А чтобы превращать людей в мышей ))) (А [капусту в репу](https://dl.acm.org/doi/abs/10.1145/300515.300516)).  Так назвал свои [статьи](https://ieeexplore.ieee.org/abstract/document/492588) известный биоинформатик П.Певзнер - оказывается эволюционная дистанция - соответствует дистанции на определенных графах - и наша цель улучшить ее оценку через МЛ/РЛ.   Зачем нужно нужно в сетях  - задержка сигнала (latency) сети определяется диаметром сети - оценка диаметра графов - одна из наших целей.    В теории квантовых вычислений тоже нужны подобные графы и приложения этим не ограничены.   И, кроме того, а знаете чем знаменит Билл Гейтс ?)) Он отлично [сортировал блины](https://en.wikipedia.org/wiki/Pancake_sorting#The_original_pancake_problem) ! Наша задача - побить его - через МЛ/РЛ)))\n\nВ нашем коллективе есть профессора математики, Каггл градмастеры, и легендарные иностранные специалисты - Tomas Rokicki , Herbert Kociemba  - Вам будет у кого поучиться. \n\nПодробнее о проекте вы можете узнать в наших статьях https://arxiv.org/abs/2502.18663 https://arxiv.org/abs/2502.13266 и в нашей группе https://t.me/sberlogasci/1 и  ⭐ СТАВЬТЕ СТАРС ⭐ (звездочки) на наш гитхаб: https://github.com/cayleypy/cayleypy	["photo:5447190971956721008"]	2025-07-18 14:37:08+00	f	2025-07-18 14:37:08+00	\N	t	202507	2263	32	0	0	f	t	2025-07-18 14:38:13+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.559317+00
36f50e2b-715b-41f0-bb8f-f02594414899	11c77f6b-2a54-4139-a20b-43d8a7950f34	1187	Ставим звездочки и участвуем в интересном проекте	[]	2025-07-18 14:37:08+00	f	2025-07-18 14:37:08+00	\N	f	202507	2062	2	0	0	f	t	2025-07-18 14:49:38+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.562221+00
570b04fc-e265-4793-b4f5-54eba04880b6	11c77f6b-2a54-4139-a20b-43d8a7950f34	1186		["photo:5461009956217155849"]	2025-07-17 12:05:54+00	f	2025-07-17 12:05:54+00	\N	t	202507	6341	55	0	0	f	t	2025-07-17 12:05:58+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.564344+00
ce76c62e-dd13-470b-8c34-569f2cb4ffc2	11c77f6b-2a54-4139-a20b-43d8a7950f34	1185	Хм, похоже новая сота опенсурс ллм-прувер\nСтатьи пока нет.\n\nГенерили синтетические доказательства с возрастающей сложностью + самокоррекция на фидбеке от Lean компилера. RL\n\nGoedel-Prover-V2-**8B** моделька пишут что примерно как DeepSeek-Prover-V2-**671B**. \n32B еще лучше\n\nhttps://blog.goedel-prover.com/\n\nhttps://huggingface.co/Goedel-LM/Goedel-Prover-V2-8B\n\nhttps://huggingface.co/Goedel-LM/Goedel-Prover-V2-32B	["photo:5461009956217155848"]	2025-07-17 12:05:54+00	f	2025-07-17 12:05:54+00	\N	t	202507	6541	55	0	5	f	t	2025-07-17 12:12:07+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.566466+00
85aa4570-cb9e-4520-ba8e-be064d060b7f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1184		["photo:5458797274965604308"]	2025-07-16 18:48:38+00	f	2025-07-16 18:48:38+00	\N	t	202507	2501	52	0	0	f	t	2025-07-16 18:48:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.568912+00
4ec78401-078d-46ec-a880-9468ed644d6f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1183		["photo:5458797274965604309"]	2025-07-16 18:48:38+00	f	2025-07-16 18:48:38+00	\N	t	202507	3171	52	0	0	f	t	2025-07-16 18:48:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.571408+00
fcb2ab52-7038-41a7-a019-4c0e14944a47	11c77f6b-2a54-4139-a20b-43d8a7950f34	1182		["photo:5458797274965604306"]	2025-07-16 18:48:38+00	f	2025-07-16 18:48:38+00	\N	t	202507	2543	51	0	0	f	t	2025-07-16 18:48:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.573442+00
f1af80cb-727b-4c0b-b9d1-e109c6bee007	11c77f6b-2a54-4139-a20b-43d8a7950f34	1181	Как же он понял... 👍👍👍\nhttps://x.com/_jasonwei/status/1945294042138599722	["photo:5458797274965604307"]	2025-07-16 18:48:38+00	f	2025-07-16 18:48:38+00	\N	t	202507	2189	50	0	1	f	t	2025-07-16 18:51:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.575711+00
ed00d5ca-920b-4153-8827-3897169b8733	11c77f6b-2a54-4139-a20b-43d8a7950f34	1180	Мб кому интересно\nЧел который уволился из OpenAI 3 недели назад рассказывает о своих впечатлениях.\nРаботал кстати над запуском кодекса\nhttps://calv.info/openai-reflections	[]	2025-07-16 12:55:30+00	f	2025-07-16 12:55:30+00	\N	f	202507	2321	45	0	0	f	t	2025-07-16 13:02:36+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.577816+00
a9aeb5a9-0971-4cd1-92ff-b35854c8e9e7	11c77f6b-2a54-4139-a20b-43d8a7950f34	1179	Gguf с любыми квантами Kimi K2 от анслота на месте. Рекомендуют 256гб оперативы и 16гб врам+ иметь для мелких квантов\n\nunsloth/Kimi-K2-Instruct-GGUF · Hugging Face\nhttps://huggingface.co/unsloth/Kimi-K2-Instruct-GGUF	["photo:6029490358636886491"]	2025-07-15 15:40:00+00	f	2025-07-15 15:40:00+00	https://huggingface.co/unsloth/Kimi-K2-Instruct-GGUF	t	202507	2622	33	0	0	f	t	2025-07-15 15:43:22+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.579894+00
f7080db0-604c-4659-9803-28ed87f08110	11c77f6b-2a54-4139-a20b-43d8a7950f34	1178	Кстати Kimi K2 это раздутый DeepSeek V3/R1. Меньше хедов в мульти-хеде, больше экспертов \n\nhttps://x.com/rasbt/status/1944056316424577525\n\nЕще померили на бенчмарке "эмоционального интеллекта" https://eqbench.com/\nЩас у него больший скор из всех моделек в бенче. \nЕще он лучший в креативном написании текстов	["photo:5449820441129643813"]	2025-07-13 12:57:08+00	f	2025-07-13 12:57:08+00	\N	t	202507	2609	45	0	1	f	t	2025-07-13 12:57:43+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.582074+00
6c33890c-9f7e-4bb3-95c4-776baf342154	11c77f6b-2a54-4139-a20b-43d8a7950f34	1177	Для тех кому куда и гпу прог интересен\n\nчел выложил решения ко всем задачкам из известной книжки Programming Massively Parallel Processors\n\nhttps://github.com/tugot17/pmpp/	["photo:6029249870533081603"]	2025-07-13 07:37:28+00	f	2025-07-13 07:37:28+00	https://github.com/tugot17/pmpp	t	202507	2462	67	0	0	f	t	2025-07-13 07:44:51+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.584218+00
f6f1cd74-0654-4807-a79d-32a46f657a2c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1176	Как насчет опенсурсной агенточной модельки на 1Т параметров? Kimi K2\n\nhttps://moonshotai.github.io/Kimi-K2/\n\nhttps://huggingface.co/moonshotai/Kimi-K2-Instruct\n\nhttps://huggingface.co/moonshotai/Kimi-K2-Base\n\nhttps://github.com/MoonshotAI/Kimi-K2?tab=License-1-ov-file#readme\n\nMOE  с 32б активных параметров. Но все равно 1Т общих оч много\nНо зато опенсурс и поэтому кайфуем. Еще и от челов которые RL над ллмками активно делают\nВсем РЛьным респект всегда	["photo:5442965686210133767"]	2025-07-11 16:18:09+00	f	2025-07-11 16:18:09+00	\N	t	202507	2443	29	0	7	f	t	2025-07-11 16:42:47+00	\N	1160	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.586414+00
cdc1c2a6-c3a5-48ed-b98a-6b3f155ec5dc	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24656	📈 Госдолг США превысил** $38 трлн. **Это исторический рекорд. Трамп неоднократно обещал погасить госдолг, если станет президентом. «Эта страна должна** $35 трлн,** но это может быстро сойти на нет», — говорил он в ходе предвыборной компании. Однако за время его президентства темпы роста госдолга только ускорились.	[]	2025-10-23 15:46:31+00	f	2025-10-23 15:46:31+00	\N	f	202510	5833	30	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.887792+00
71fdf77e-4cd7-4230-adf6-fd3274357e27	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24655	Прокуратура запросила 8 лет колонии для Аяза Шабутдинова по делу о мошенничестве. Блогер признал свою вину.	["photo:5462903925125544752"]	2025-10-23 15:01:33+00	f	2025-10-23 15:01:33+00	\N	t	202510	6264	71	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.890363+00
332e35bd-e36e-4016-9230-7a7f6b04578b	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24654	Санкции США против российских нефтяных компаний вызвали беспокойство в Китае, — Bloomberg	[]	2025-10-23 14:31:38+00	f	2025-10-23 14:31:38+00	\N	f	202510	5627	2	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.8928+00
117e2284-c648-421f-8047-260347138e08	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24653	ЕС также запретил поставлять в Россию трёхколесные велосипеды, самокаты, игрушечные педальные автомобили, коляски для кукол, сами куклы и головоломки.	[]	2025-10-23 14:01:34+00	f	2025-10-23 14:01:34+00	\N	f	202510	5845	54	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.89511+00
416ff5cf-5710-498f-8f57-0b3aeec961e3	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24652	Клиенты российских банков стали чаще попадать в мошенническую базу ЦБ за продажу крипты на p2p-сервисах. Из-за этого банки могут заблокировать их счета. При этом осенью ЦБ пообещал разработать механизм реабилитации таких россиян, но пока этого не произошло и продажа крипты  через эти площадки остается высокорискованной, отмечают участники рынка.	[]	2025-10-23 13:16:25+00	f	2025-10-23 13:16:25+00	\N	f	202510	5907	34	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.897497+00
18695b08-436d-419c-b8ef-ca52773eb5db	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24651		["photo:5463286014006129841"]	2025-10-23 12:35:50+00	f	2025-10-23 12:35:50+00	\N	t	202510	5883	37	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.899573+00
74507603-c276-44e3-a963-51d4b5477541	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24649	**Техника в России может заметно подорожать из-за массового исхода американских IT-гигантов из Китая.\n**Microsoft, Google, Amazon и Apple сворачивают производство в Поднебесной — причиной стали политические пошлины, новые тарифы США и последствия локдаунов.\n\nMicrosoft переносит выпуск Surface, серверов и консолей Xbox, Amazon строит ИИ-кластеры во Вьетнаме и Индии, Google открывает сборочные линии в Таиланде, а Apple переносит производство iPad и HomePod во Вьетнам.\n\nПерестройка логистики и рост себестоимости приведут к подорожанию электроники по всему миру — в России особенно, где импорт и без того осложнён.	[]	2025-10-23 11:46:27+00	f	2025-10-23 11:46:27+00	\N	f	202510	6789	52	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.903698+00
a8731d4e-563e-4e87-b02c-cc2eda464984	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24648	Одна из крупнейших металлургических и горнодобывающих компаний РФ Evraz с активами в России, США, Канаде, Чехии, Италии и Kазахстане попала под санкции ЕС.	[]	2025-10-23 11:01:22+00	f	2025-10-23 11:01:22+00	\N	f	202510	5475	17	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.905836+00
c41d0fb3-3401-4d51-b0b2-8c6e89971f97	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24647	**Последнее обновление в CS2 вызвало обвал рынка скинов и подорвало внутриигровую экономику — за несколько часов игроки потеряли свыше $1 млрд\n**\nТеперь пять предметов тайного качества можно обменять на нож из той же коллекции, что резко снизило его редкость и стоимость. Если раньше за нож приходилось платить $5 000, то теперь его можно получить примерно за $5.\n\nЦены на «красные» пушки выросли в 10–20 раз, а стоимость ножей, наоборот, обрушилась. Ещё недавно скины считались более надёжной инвестицией, чем крипта или фондовый рынок, но обновление показало, насколько хрупок этот рынок.	["photo:5462903925125543956"]	2025-10-23 10:14:01+00	f	2025-10-23 10:14:01+00	\N	t	202510	5973	97	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.908145+00
9a65ff54-449c-461b-9c04-5b3828828d8f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24646	Евросоюз принял 19-й пакет санкций против России. Новые санкции направлены против российских банков, криптобирж, компаний в Индии и Китае. ЕС также ограничит передвижения российских дипломатов, чтобы «противостоять попыткам дестабилизации».	[]	2025-10-23 09:31:25+00	f	2025-10-23 09:31:25+00	\N	f	202510	5573	9	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.910369+00
b41f0246-cb2d-4e3e-ada2-f1e318a2be41	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24644	**За первое полугодие рынок микрозаймов для бизнеса увеличился на 29%**. Объем новых займов составил 72,8 млрд рублей, а общий портфель достиг 111 млрд. Почти половина средств пришлась на продавцов с маркетплейсов, которые оформляют кредиты примерно в два раза чаще других предпринимателей.	[]	2025-10-23 08:02:06+00	f	2025-10-23 08:02:06+00	\N	f	202510	5585	14	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.914415+00
d83ffa99-91c5-44d7-86b3-6fa6ec5ddd27	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24643	📉 Глава Минфина США Джанет Бессент заявила, что в ближайшие дни санкции против России будут существенно ужесточены.	[]	2025-10-23 07:11:21+00	f	2025-10-23 07:11:21+00	\N	f	202510	5550	12	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.916599+00
d8814410-e216-4013-b563-c24828960804	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24642	👑 Минфин приступил к созданию реестра майнеров. По словам Антона Силуанова, в списке уже числится 1 364 человека.	[]	2025-10-23 06:31:41+00	f	2025-10-23 06:31:41+00	\N	f	202510	5621	12	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.919043+00
900275c8-2a01-4d50-b412-a34603fd3701	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24640	Запрещённая в России Meta уволит около 600 сотрудников ИИ-подразделения на фоне миллиардных инвестиций в ИИ-гонку.\n\nПо мнению компании, это позволит избавиться от чрезмерной бюрократии и сделать процесс работы более гибким.	["photo:5461000103562180441"]	2025-10-23 05:02:52+00	f	2025-10-23 05:02:52+00	\N	t	202510	5704	18	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.92371+00
0541f67c-7468-4b30-a46d-4fcb410b9665	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24639		["photo:5458589050656128849"]	2025-10-22 18:04:52+00	f	2025-10-22 18:04:52+00	\N	t	202510	7482	216	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.92569+00
5df0098a-c8b6-4705-8f3a-f5eb03f91f10	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24638		["photo:5458589050656128847"]	2025-10-22 18:04:52+00	f	2025-10-22 18:04:52+00	\N	t	202510	7540	215	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.927568+00
f129538f-3e99-49f9-8aa8-cf5b2f657681	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24637	Девушка ушла из офиса и теперь зарабатывает **200 тысяч рублей в месяц**, строя дома в The Sims.\n\nБывший маркетолог собрала команду виртуальных строителей и продаёт их работы через **Boosty**. Постройки расходятся мгновенно — себестоимость почти нулевая, а прибыль стабильная.\n\n«Никакого начальства, дедлайнов и корпоративов — только симы и кэш», — говорит она.	["video:5458589050199900558", "document:5458589050199900558"]	2025-10-22 18:04:52+00	f	2025-10-22 18:04:52+00	\N	t	202510	7502	216	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.929693+00
6bc9fd60-6593-4ccf-8212-10cbcd0d5908	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24636	Маркетплейсы вытесняют традиционную розницу, предлагая товары по ценам, которые в среднем в три–пять раз ниже, чем в обычных магазинах.	["photo:5461000103562180329"]	2025-10-22 16:51:51+00	f	2025-10-22 16:51:51+00	\N	t	202510	6308	42	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.932071+00
d0e00060-0613-4bf4-be57-76d28376ec3d	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24635	Миллиардер Михаил Гуцериев выиграл в суде ЕС дело об отмене санкций против него за 2024 год. Как сказано в постановлении суда в Люксембурге, отмене подлежит решение от 26 февраля 2024 года. Постановление суда не означает исключение Гуцериева из санкционного списка: после указанной даты санкции ЕС вновь продлевались. Гуцериев оказался в санкционном списке ЕС в июне 2021-го — в Брюсселе обвинили его в поддержке режима Лукашенко.	["photo:5461000103562180325"]	2025-10-22 16:06:15+00	f	2025-10-22 16:06:15+00	\N	t	202510	6171	7	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.934189+00
ebb2891d-0539-4b7d-b33f-d2c07fb83d67	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24634	Nebius Аркадия Воложа и Uber инвестируют до $375 млн в разработчика беспилотных технологий Avride — подразделение бывшей Yandex N.V. Компания запланировала расширить географию присутствия и увеличить парк до 500 беспилотных автомобилей.\n\nПервые автомобили с беспилотными системами Avride должны появиться в американском Далласе до конца 2025 года в рамках сервиса роботакси от Uber. Это будут собранные в США Hyundai Ioniq 5.	["photo:5461000103562179981"]	2025-10-22 13:36:05+00	f	2025-10-22 13:36:05+00	\N	t	202510	6251	14	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.936229+00
ceb9baf8-4fc6-43ad-8d2f-05798d83f766	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24633	В Госдуме опровергли слухи о грядущем запрете Telegram и WhatsApp. По словам депутатов, введённые ограничения **временные** и направлены исключительно на **повышение кибербезопасности и защиту от злоумышленников**.\n\nПолная блокировка мессенджеров **не рассматривается**, заверили в парламенте.	[]	2025-10-22 12:47:00+00	f	2025-10-22 12:47:00+00	\N	f	202510	6210	41	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.938173+00
577cc7ba-3659-4cba-8382-50b84246d8b3	7f194a2a-5206-4348-b42d-1b3976ec7d43	1220	Художник Дэвид Зинн (David Zinn).\n\nХорошего вам настроения! \nКак сегодня у меня 😎	["photo:5456425667039197463"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	30	1	0	0	f	t	2025-10-24 07:45:25+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.409815+00
c4bd43a4-3b85-4fcd-9b47-f6593d7e5ffa	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24631	💰Россия нарастит объём зарубежных кредитов до **1,8 трлн рублей** в ближайшие три года — это на **14% больше**, чем планировалось ранее.\n\nСредства направят на поддержку экономик партнёров, развитие инфраструктуры и закупку российской продукции.\nСреди получателей — **Иран**, которому выделят деньги на строительство железной дороги, **Вьетнам** — на закупку военной техники, и **Египет** — на возведение атомной электростанции.\n\nТаким образом, Москва укрепляет экономические связи и продвигает свои технологии за рубежом.	[]	2025-10-22 10:01:04+00	f	2025-10-22 10:01:04+00	\N	f	202510	5898	25	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.941966+00
05093057-8505-4c80-9a35-05d9cad81656	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24630	Просто и наглядно о росте НДС\n\n__Сохраняем__	["photo:5461000103562178959"]	2025-10-22 09:01:22+00	f	2025-10-22 09:01:22+00	\N	t	202510	6406	204	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.944296+00
e4d35c34-c945-4de7-a974-f78728cf02b8	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24629	АвтоВАЗ массово переводит станочников в уборщики, чтобы сохранить рабочие места, пишет Mash. Теперь квалифицированные специалисты убирают стружку, моют подвалы и красят оборудование. До этого из-за сокращения объёмов производства компания перешла на четырехдневную рабочую неделю без сохранения зарплаты.	[]	2025-10-22 08:01:16+00	f	2025-10-22 08:01:16+00	\N	f	202510	6014	61	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.946483+00
a45bb205-30ee-4c47-839d-709841ab5b0f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24628	Крупные посреднические интернет-площадки будут обязаны следить за соблюдением налогового законодательства своими контрагентами и передавать данные о нарушениях в ФНС — такой проект подготовила налоговая, узнал РБК.\n\nЗа нарушение правил платформам грозят штрафы в 100 тысяч рублей, а продавцам — блокировка их товаров в поиске.	[]	2025-10-22 07:13:01+00	f	2025-10-22 07:13:01+00	\N	f	202510	6293	24	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.948429+00
8044ce8c-b85b-4fbc-a53b-d6cebde44d88	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24627	OpenAI показала ChatGPT Atlas — ИИ-браузер с возможностью открыть диалог с чат-ботом на любой вкладке и режимом агента.\n\nБраузер пока доступен только для macOS. Режим агента в превью-версии на старте получат подписчики ChatGPT Plus и Pro.	["video:5461000103105951089", "document:5461000103105951089"]	2025-10-22 06:27:01+00	f	2025-10-22 06:27:01+00	\N	t	202510	6023	45	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.950467+00
c6aa642e-55f0-4d88-adc2-579fd71b6c03	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24626		["photo:5458809292284099793"]	2025-10-22 05:15:24+00	f	2025-10-22 05:15:24+00	\N	t	202510	6560	81	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.952366+00
94b19828-0461-4c1a-a9f4-984331766cd4	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24625		["photo:5458809292284099792"]	2025-10-22 05:15:23+00	f	2025-10-22 05:15:23+00	\N	t	202510	6594	81	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.954311+00
28b3ac4e-dde9-4ef7-8a28-369cffd7406d	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24624		["photo:5458809292284099791"]	2025-10-22 05:15:23+00	f	2025-10-22 05:15:23+00	\N	t	202510	6401	81	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.95653+00
c746f80d-5d1c-49ff-b386-1434376a2700	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24622	Правительство предупредило о цунами банкротств после снижения порога доходов для уплаты НДС с 60 миллионов до 10. \n\nСогласно статистике, от этого больше всего пострадают ИП с доходом менее 200 тысяч рублей в месяц.	[]	2025-10-22 04:27:48+00	f	2025-10-22 04:27:48+00	\N	f	202510	6567	73	0	0	f	f	\N	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.961273+00
0fcb33fe-8733-419b-8e16-b921125f5afc	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24621	💶 **Глава Tehter Паоло Ардоино сообщил, что число пользователей стейблкоина USDT достигло 500 млн. А общий объём эмиссии, по подсчётам The Block, составил почти $182 млрд. \n**\nВ сентябре 2025 года источники Bloomberg рассказали, что Tether ведёт переговоры с инвесторами о привлечении **$20 млрд **при оценке в** $500 млрд.**	[]	2025-10-21 16:47:11+00	f	2025-10-21 16:47:11+00	\N	f	202510	6573	20	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.963902+00
fe618e8e-d1c4-45fe-a6e2-6e33ed9a2822	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24620	В России легализуют криптовалюту для внешней торговли, — Силуанов. \n\nЗа этими операциями будет повышенный контроль со стороны ведомства и ЦБ, уточнил министр.	[]	2025-10-21 16:06:52+00	f	2025-10-21 16:06:52+00	\N	f	202510	6577	34	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.966023+00
229299ee-e075-42e5-8919-883f1ab4ed93	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24619	**Volkswagen на грани финансового краха\n**\nНемецкий автогигант Volkswagen может столкнуться с серьезными финансовыми проблемами. Компании не хватает 11 миллиардов евро для стабильной работы в следующем году, сообщает BILD.	["photo:5456582618029095208"]	2025-10-21 15:16:37+00	f	2025-10-21 15:16:37+00	\N	t	202510	6811	92	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.968131+00
8004b799-5c5d-47c9-950c-19d24d90de09	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24617	**США и Австралия подписали соглашение о сотрудничестве в сфере редкоземельных металлов на сумму до $8,5 млрд.** Это сотрудничество расширит для Вашингтона доступ к критически важным материалам в условиях контроля за экспортом со стороны Китая, отмечает Bloomberg. На этом фоне сильно выросли акции австралийских компаний, связанных с редкоземельными металлами.	[]	2025-10-21 13:48:01+00	f	2025-10-21 13:48:01+00	\N	f	202510	6311	5	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.972116+00
5bdb67f0-dfba-452b-84b6-522a65c6449f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24616	**Украденные из Лувра драгоценности всплыли на продаже в Москве — за 250 миллионов рублей**. На одной из онлайн-площадок появилось объявление с диадемой, серьгами и ожерельем из сапфиров и бриллиантов. Грабителей до сих пор не нашли, а следствие проверяет, не связаны ли продавцы с кражей.	["photo:5456582618029094736"]	2025-10-21 13:01:32+00	f	2025-10-21 13:01:32+00	\N	t	202510	6814	133	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.974049+00
996582f5-0a1b-4a70-882c-9da977ce1a75	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24615		["photo:5456255285686567846"]	2025-10-21 12:13:01+00	f	2025-10-21 12:13:01+00	\N	t	202510	6558	87	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.976125+00
e576cc7b-6cc8-46c5-9956-311a8d4955d7	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24614	Нелегальный мигрант в Канаде выиграл $5 млн в лотерею, но не смог оформить билет на своё имя и записал его на знакомую девушку. После получения выигрыша она исчезла с деньгами и новым бойфрендом. История быстро разлетелась по соцсетям, а настоящий победитель теперь ждёт решения суда, чтобы попытаться вернуть свой приз.	["photo:5456255285686567845"]	2025-10-21 12:13:01+00	f	2025-10-21 12:13:01+00	\N	t	202510	6696	86	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.978491+00
cd97cba1-3273-498b-91d2-8c31726944dc	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24612	Павел Дуров заявил, что готов выкупить украденные из Лувра драгоценности и передать их в филиал музея в Абу-Даби.\n\n«Никто не ворует из Лувра в Абу-Даби», — написал он в X, добавив, что произошедшее символизирует «упадок Франции и потерю ею культурного величия».	["photo:5456582618029094480"]	2025-10-21 10:46:31+00	f	2025-10-21 10:46:31+00	\N	t	202510	6322	56	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.98283+00
0cf306d4-0af6-4be7-8213-f46d2d48d83a	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24610	📈 В 2026 году кофе в России подорожает примерно на 25% — этому способствуют колебания доллара и ценовые войны на биржах. Однако, по прогнозам аналитиков, спрос не снизится: даже при росте стоимости россияне продолжат покупать напиток по 500 рублей за стакан.	[]	2025-10-21 10:01:16+00	f	2025-10-21 10:01:16+00	\N	f	202510	6116	39	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.984682+00
091a7192-a296-43e0-a796-d50e8b65201c	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24609	**В 2025 финансовом году доходы США от пошлин выросли до рекордных $195 млрд, что в 2,5 раза превышает показатель 2024 финансового года, — Bloomberg.\n**\nПри текущих темпах роста доходы от пошлин могут превысить** $350 млрд** в 2026 финансовом году.	["photo:5456582618029094370"]	2025-10-21 09:17:01+00	f	2025-10-21 09:17:01+00	\N	t	202510	6518	18	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.986813+00
53ba5b41-af7f-479b-bcae-0edced8b16f6	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24608	Павел Дуров прокомментировал ограбление Лувра, заявив, что причиной подобных событий стала политика французских властей. По его словам, она привела к «упадку некогда великой страны» и росту социальной нестабильности.	["photo:5456582618029094291"]	2025-10-21 08:33:56+00	f	2025-10-21 08:33:56+00	\N	t	202510	6546	14	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.988842+00
b357d25f-902c-43c5-ba04-49d2fe276da7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1233		["photo:5458590669858799014"]	2025-10-21 21:24:20+00	f	2025-10-21 21:24:20+00	\N	t	202510	29	1	0	0	f	t	2025-10-21 21:24:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.382186+00
9f813f17-3a79-43c8-9830-5f7be9637f9d	7f194a2a-5206-4348-b42d-1b3976ec7d43	1232		["photo:5458590669858799013"]	2025-10-21 21:24:20+00	f	2025-10-21 21:24:20+00	\N	t	202510	29	1	0	0	f	t	2025-10-21 21:24:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.384379+00
d8a50bd8-d048-4a3b-b64e-f23f64231691	7f194a2a-5206-4348-b42d-1b3976ec7d43	1231		["photo:5458590669858799012"]	2025-10-21 21:24:20+00	f	2025-10-21 21:24:20+00	\N	t	202510	29	1	0	0	f	t	2025-10-21 21:24:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.386797+00
1fbb4f6b-5bfc-4669-8897-481cd329e9b4	7f194a2a-5206-4348-b42d-1b3976ec7d43	1230		["photo:5458590669858799011"]	2025-10-21 21:24:20+00	f	2025-10-21 21:24:20+00	\N	t	202510	30	1	0	0	f	t	2025-10-21 21:31:26+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.389142+00
b39aac65-4589-4a58-9be2-11c7088e2ded	7f194a2a-5206-4348-b42d-1b3976ec7d43	1229		["photo:5456425667039197472"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	30	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.391641+00
1f1f511b-edba-4cff-9f53-ab7e0a8a7887	7f194a2a-5206-4348-b42d-1b3976ec7d43	1228		["photo:5456425667039197471"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	30	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.393972+00
2eefabc1-d6d7-4482-903b-b67e848e54f0	7f194a2a-5206-4348-b42d-1b3976ec7d43	1227		["photo:5456425667039197470"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	31	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.396179+00
015bcec3-acc6-462b-90e8-8556c88d099c	7f194a2a-5206-4348-b42d-1b3976ec7d43	1226		["photo:5456425667039197469"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	30	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.398136+00
001b3d66-57f5-406f-a560-16536984cf71	7f194a2a-5206-4348-b42d-1b3976ec7d43	1225		["photo:5456425667039197468"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	31	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.400058+00
bd850165-ea84-433b-bf48-c4cc7e605953	7f194a2a-5206-4348-b42d-1b3976ec7d43	1224		["photo:5456425667039197467"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	30	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.402023+00
f8501657-d629-4720-ad0e-4335a2b48ab7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1223		["photo:5456425667039197466"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	31	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.403978+00
e21fc184-8599-47c6-b094-dd13cb68773b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1222		["photo:5456425667039197465"]	2025-10-21 08:08:01+00	f	2025-10-21 08:08:01+00	\N	t	202510	30	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.40582+00
8bf6b6c3-5c5e-4c56-af7c-9b307fb40a5c	7f194a2a-5206-4348-b42d-1b3976ec7d43	1218	Предположительно забрали 9 предметов из коллекции драгоценностей Наполеона и императрицы.	[]	2025-10-19 10:25:29+00	f	2025-10-19 10:25:29+00	\N	f	202510	44	1	0	0	f	t	2025-10-19 10:54:27+00	\N	1217	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.414049+00
2db5b6e3-1653-41e3-b285-0693d722560b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1217	Лувр ограбили	[]	2025-10-19 10:07:11+00	f	2025-10-19 10:07:11+00	\N	f	202510	42	1	0	0	f	t	2025-10-24 07:45:35+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.416099+00
6045f651-d186-442c-b50f-5f151f35eafa	7f194a2a-5206-4348-b42d-1b3976ec7d43	1215	Устаю так сильно, что к вечеру еле ноги волочу. А в голове дымка. \n\nТут уж не до постов. И, честно говоря, вообще не до чего. \nГреет мысль, что осталось недолго терпеть)). \n\nА пока смотрим балет Щелкунчик. Поднимаем себе настроение к Новому году. \nГлавное при просмотре не думать о страданиях людей, которые ежегодно стояли в очередях при минусовой температуре или покупали поддельные билеты). \n\nА постановка хорошая, правда.\n\nhttps://youtu.be/TlVz_gqnyTA?si=KPZUHz4xKg-ikLvi	["photo:6031817015435572933"]	2025-10-16 08:54:06+00	f	2025-10-16 08:54:06+00	https://youtu.be/TlVz_gqnyTA?si=KPZUHz4xKg-ikLvi	t	202510	64	1	0	0	f	t	2025-10-16 11:58:19+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.420414+00
e025dbfb-a5b8-400d-a518-cd4dc66ff4d8	7f194a2a-5206-4348-b42d-1b3976ec7d43	1212	Смотрим оперу Турандот. Пока это возможно 🩷\n\nhttps://youtu.be/fnE2N09nuPI?si=Ykhxx99eVEiDI3Ba	["photo:5869617498192720673"]	2025-10-14 20:08:52+00	f	2025-10-14 20:08:52+00	https://youtu.be/fnE2N09nuPI?si=Ykhxx99eVEiDI3Ba	t	202510	59	1	0	0	f	t	2025-10-14 20:39:42+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.422489+00
57bdf053-7983-4e0f-9502-8dac2bb7b76a	7f194a2a-5206-4348-b42d-1b3976ec7d43	1209	Гуф «оперный певец»))\n\nВидео стащила [тут](https://t.me/aistarsss/8130)	["video:5420371456242514923", "document:5420371456242514923"]	2025-10-08 21:07:55+00	f	2025-10-08 21:07:55+00	\N	t	202510	88	2	0	3	f	t	2025-10-09 14:11:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.424621+00
3c3dcb8d-c188-4ff4-b3f4-10f0e6133994	7f194a2a-5206-4348-b42d-1b3976ec7d43	1207		["photo:5413887211427987279"]	2025-10-06 17:44:58+00	f	2025-10-06 17:44:58+00	\N	t	202510	81	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.426651+00
3684f840-9802-474f-b08e-75121449f390	7f194a2a-5206-4348-b42d-1b3976ec7d43	1206		["photo:5413887211427987278"]	2025-10-06 17:44:58+00	f	2025-10-06 17:44:58+00	\N	t	202510	90	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.428786+00
80c32c96-9d55-43d2-8641-a772b32fbfa6	7f194a2a-5206-4348-b42d-1b3976ec7d43	1205		["photo:5413887211427987277"]	2025-10-06 17:44:58+00	f	2025-10-06 17:44:58+00	\N	t	202510	93	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.430785+00
09e3d9a0-06f7-471a-9271-7942a72935ed	7f194a2a-5206-4348-b42d-1b3976ec7d43	1204		["photo:5413887211427987276"]	2025-10-06 17:44:58+00	f	2025-10-06 17:44:58+00	\N	t	202510	92	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.432801+00
12492f4c-0f46-4236-8f7d-9948b5ab82ae	7f194a2a-5206-4348-b42d-1b3976ec7d43	1203		["photo:5413887211427987275"]	2025-10-06 17:44:57+00	f	2025-10-06 17:44:57+00	\N	t	202510	83	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.435074+00
082a9d4f-8e0a-49c7-bd7e-14f216f65af7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1202		["photo:5413887211427987274"]	2025-10-06 17:44:57+00	f	2025-10-06 17:44:57+00	\N	t	202510	81	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.436938+00
8b6d891b-7479-497d-9d7b-00d91ba6583f	7f194a2a-5206-4348-b42d-1b3976ec7d43	1201		["photo:5413887211427987273"]	2025-10-06 17:44:57+00	f	2025-10-06 17:44:57+00	\N	t	202510	80	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.438704+00
9a5a3f88-26c4-485c-b3da-1697b36b77dd	7f194a2a-5206-4348-b42d-1b3976ec7d43	1200		["photo:5413887211427987272"]	2025-10-06 17:44:57+00	f	2025-10-06 17:44:57+00	\N	t	202510	76	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.440844+00
4fff5e62-98cc-4679-9346-3454b3aef0fd	7f194a2a-5206-4348-b42d-1b3976ec7d43	1199		["photo:5413887211427987271"]	2025-10-06 17:44:57+00	f	2025-10-06 17:44:57+00	\N	t	202510	78	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.442922+00
d2de0cf5-1570-4d6b-9bcd-24a6fa1a0aea	7f194a2a-5206-4348-b42d-1b3976ec7d43	1198		["photo:5413887211427987270"]	2025-10-06 17:44:57+00	f	2025-10-06 17:44:57+00	\N	t	202510	72	1	0	0	f	t	2025-10-06 20:27:37+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.444896+00
7025fe0a-611a-4dbc-b56a-d8e2af88103b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1197		["photo:5413887211427987269"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	63	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.447005+00
3ee20fa0-69ce-4c3e-8e2c-1f11d4462305	7f194a2a-5206-4348-b42d-1b3976ec7d43	1196		["photo:5413887211427987268"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	67	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.448705+00
c0d8e436-dbd7-468a-9708-64644f17fc4d	7f194a2a-5206-4348-b42d-1b3976ec7d43	1195		["photo:5413887211427987267"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	66	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.450569+00
e086a2f9-d520-4252-b1aa-a8018c1fac0b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1194		["photo:5413887211427987266"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	55	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.452587+00
a60d0d10-8384-4732-874e-67544bc026d3	7f194a2a-5206-4348-b42d-1b3976ec7d43	1193		["photo:5413887211427987265"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.454443+00
498a9801-d549-4d1e-bc9e-83731f1752e9	7f194a2a-5206-4348-b42d-1b3976ec7d43	1192		["photo:5413887211427987264"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.4569+00
6c25abb6-eea5-40c3-b08a-9fc93b28174b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1191		["photo:5413887211427987263"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.45888+00
b2cd4549-1416-4811-8846-ca21511e35b7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1190		["photo:5413887211427987262"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.460994+00
f935f35d-83db-4e71-9044-fc341b16d5e0	7f194a2a-5206-4348-b42d-1b3976ec7d43	1189		["photo:5413887211427987261"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	53	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.46303+00
c88806f2-81d0-49bc-a300-6158d371e526	7f194a2a-5206-4348-b42d-1b3976ec7d43	1188	Настроение:\nСмотреть натюрморты Николая Мазура. \n\nА остальное сдюжим как нибудь)	["photo:5413887211427987260"]	2025-10-06 17:44:53+00	f	2025-10-06 17:44:53+00	\N	t	202510	53	1	0	0	f	t	2025-10-06 20:27:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.465406+00
65a7354c-e521-4dea-9176-e2ce3b5f8fc9	7f194a2a-5206-4348-b42d-1b3976ec7d43	1187	А когда-то телохранители выглядели так.\n\nНа картинке: Фигура Фаберже, изображающая личного казачьего телохранителя императрицы Александры (1912).	["photo:5408972102394050594"]	2025-10-05 10:26:44+00	f	2025-10-05 10:26:44+00	\N	t	202510	62	1	0	0	f	t	2025-10-06 20:27:53+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.467964+00
d46f8531-3ba2-444b-b741-4c25565d41db	7f194a2a-5206-4348-b42d-1b3976ec7d43	1186	Про Маурицио Каттелана, если кто не помнит, писала тут https://telegra.ph/Mauricio-Kattelan-Fenomen-v-mire-iskusstva-02-14	["photo:5975524579893949283"]	2025-09-29 20:21:31+00	f	2025-09-29 20:21:31+00	https://telegra.ph/Mauricio-Kattelan-Fenomen-v-mire-iskusstva-02-14	t	202509	69	0	0	0	f	t	2025-10-06 20:28:09+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.470382+00
f322adad-b783-4263-abcf-c60df661519d	7f194a2a-5206-4348-b42d-1b3976ec7d43	1185	Отсылка к Дюшану?)	["video:5393269911957044245", "document:5393269911957044245"]	2025-09-29 20:06:27+00	f	2025-09-29 20:06:27+00	\N	t	202509	64	1	0	1	f	t	2025-10-06 20:28:20+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.472947+00
72be8a5e-75be-4efb-805b-1f12fc38ede8	7f194a2a-5206-4348-b42d-1b3976ec7d43	1184	Опять тырю мемы у Админушки. Что поделать, если у него исключительный вкус	["photo:5393269912413272197"]	2025-09-29 20:06:09+00	f	2025-09-29 20:06:09+00	\N	t	202509	63	1	0	1	f	t	2025-10-06 20:28:29+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.47484+00
38fb4a0b-8d1e-4708-b6f3-cd8219cd6969	7f194a2a-5206-4348-b42d-1b3976ec7d43	1183		["video:5993283401623274233", "document:5993283401623274233"]	2025-09-29 10:55:39+00	f	2025-09-29 10:55:39+00	\N	t	202509	50	1	0	0	f	t	2025-09-29 10:55:40+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.476808+00
3bed169c-e381-4430-a059-fb3ecf906d6f	7f194a2a-5206-4348-b42d-1b3976ec7d43	1182	В закрытом чатике канала разгадали, верно. \nТри картинки выше: Танец, лёгкость и пантомиму объединяет слово **балет**. \n\nС лёгкостью балетных танцоров вроде всё понятно. За ней стоят тяжёлые тренировки, стёртные в кровь ноги и постоянное преодоление своих физических возможностей. \n\nС танцем тоже всё ясно. Каждое движение имеет своё название и регламент правильности его выполнения. Связку движений между собой в одну цепочку помогают оттачивать строгие преподаватели. И, конечно же, тренировки с утра и до атрофии мышц. \n\nА пантомима? \n\nСчитается, что без пантомимы балету бы не хватало содержательности. Но балетная пантомима — вещь особая. Несколько сотен человек из зала просто не могут рассмотреть мимику танцующих или неявные жесты. \nПоэтому жесты и позы в балете нарочито объёмные. Не заметить их возможным не представляется. \n\nОбычно пантомимой обозначают сюжетные повороты или пиковые моменты эмоций героев. Так сказать, моменты максимального кипения чувств.\n\nЕсли в девятнадцатом веке пантомима была буквальной. Определённый жест выражал конкретное слово. Например, два поднятых вверх пальца обозначают клятву, а указание на безымянный палец — свадьбу. \n\nТо в двадцатом веке пантомима стала более абстрактной. Жесты могли выражать целые фразы. Например, указательный палец по очереди показывает на глаза. Это означает: "Смотри, я тебе кое-что покажу." Или "Я видел." \n\nНа данный момент пантомима в балете стала почти что "специей". Некоторые режиссёры считают её пережитком прошлого и почти не используют. \n\nНо другие режиссёры увлекаются до такой степени, что превращают постановку в "немое кино." Смотреть на такое сложно. \n\nКак бы там не было, пока пантомима остаётся неотъемлемой частью балета. \n\n— Вращение кистями над головой это приглашение на танец. \n— Движение кулаками к земле или к сердцу — смерть. \n— Прикосновение к сердцу логично говорит о нежных и трепетных чувствах. \n— Касание лба означает видение или сон.\n\n#искусство@okolo_art	[]	2025-09-29 10:55:36+00	f	2025-09-29 10:55:36+00	\N	f	202509	58	1	0	0	f	t	2025-10-06 20:28:38+00	\N	1178	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.478765+00
509e4005-0afa-4586-b830-482d399abfb7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1180		["photo:5379802471226014871"]	2025-09-27 09:51:24+00	f	2025-09-27 09:51:24+00	\N	t	202509	57	1	0	0	f	t	2025-09-27 09:51:27+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.481052+00
b948f580-4833-49ff-9d4f-e3b11700d861	7f194a2a-5206-4348-b42d-1b3976ec7d43	1179		["photo:5379802471226014858"]	2025-09-27 09:51:24+00	f	2025-09-27 09:51:24+00	\N	t	202509	63	1	0	0	f	t	2025-09-27 09:51:27+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.482947+00
703c6d71-f286-457a-b04a-808096b8b117	7f194a2a-5206-4348-b42d-1b3976ec7d43	1178	Играем. \n\nНайдите общее слово, которое объединяет эти три картинки	["photo:5379802471226014859"]	2025-09-27 09:51:24+00	f	2025-09-27 09:51:24+00	\N	t	202509	54	1	0	4	f	t	2025-09-27 10:25:58+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.484797+00
\.


--
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.schema_migrations (version) FROM stdin;
\.


--
-- Data for Name: telegram_auth_events; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.telegram_auth_events (id, user_id, event, reason, ip, user_agent, at, meta) FROM stdin;
5f032d03-7129-42aa-a547-ab74b195ad58	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:32:23.941422+00	{}
57cde1eb-f87f-484a-a70e-8e3d7553c1a0	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:06.686958+00	{}
74203443-e8ca-4a41-ae63-b54df85a9fd1	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:08.698382+00	{}
b1fa347e-0b38-4a6a-860b-1a2c48808d61	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:10.710057+00	{}
ff0de093-c7dc-495d-a56f-e68384324d1c	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:12.721649+00	{}
fcffc05f-153b-49da-9706-a50beb35a33a	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:14.732928+00	{}
53891a2f-29f2-4223-91ff-3480a99c94fe	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:16.743696+00	{}
4e394e9a-c3f5-4711-bfdb-b9457b360c76	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:18.754651+00	{}
78fe960e-0e73-46b7-8276-eae8637b028a	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:20.76542+00	{}
b5f19748-1c9d-4af6-8117-224da078417f	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:22.775959+00	{}
3ae9c77f-6aa2-4d79-b2b0-69f1f62e8628	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:24.786926+00	{}
d93f68eb-6a73-4e93-a5a9-783995c163d5	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:26.798709+00	{}
b428345e-58b7-4c10-99da-7f16fce9a2bf	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:28.81016+00	{}
955310f6-b75e-40f0-8edc-aa7a63fda246	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:30.822532+00	{}
86f6c432-ca9d-4a40-a70e-bff270ecca13	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:32.834547+00	{}
6470d705-9c29-4370-a742-a9dd786163c1	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:34.845758+00	{}
1660d26b-96e6-4b7d-961a-d4b17da408c2	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:36.857411+00	{}
c2301b00-329e-403b-bad0-3d3fbe09b3b2	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:38.871581+00	{}
8f1183eb-099e-4752-a30d-b13bf8467ccc	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:40.883107+00	{}
ec719b53-15d8-4e65-9830-dccf2770ccec	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:42.895166+00	{}
b7c2ba50-a157-4c90-95a7-3b7e18624f07	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:44.905727+00	{}
3a6e1bde-6289-4539-a56f-5aaa212d1f34	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:46.917101+00	{}
c7eba322-c928-48d7-a96d-0816ef2cad6f	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:48.928115+00	{}
5e4dfdc6-6c0e-42ec-95f7-3149dc364beb	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:50.940528+00	{}
5ec8f193-a21a-450b-8b2a-ce2586f297c1	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:52.9516+00	{}
f9f530b6-84c4-4b4a-b2b4-c6a7fa2d3d5c	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:54.960416+00	{}
2a72f2ec-1b94-4abb-a2f6-b4418cb0005e	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:56.970877+00	{}
fca3a244-9f88-49df-a6d0-460e87f2df1a	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:33:58.98168+00	{}
932ff9e2-47e2-43dc-a1d4-5442ab833867	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:00.992502+00	{}
fed3021b-e2c3-4ad2-873c-fdf8763fd0bb	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:03.00502+00	{}
6919d3d6-51e2-4e64-9478-fd3a43b0cda5	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:05.017429+00	{}
c54e99ad-c059-44b2-9f0b-fb479ec7bfcd	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:07.029193+00	{}
12717890-928b-4e45-a445-c6bc57f15d24	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:09.040925+00	{}
20138cc1-d742-453d-b14e-85e2b8171055	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:11.053188+00	{}
686c130e-1dbc-4e95-95f5-7bffe456da4e	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:13.063639+00	{}
499acd53-3e22-4ae9-997c-b21692611021	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:15.074907+00	{}
d8852d02-6f0f-468e-bd77-284e09ac5214	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:17.086777+00	{}
5216515c-730c-44a3-8195-d57c00a8aa30	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:19.098087+00	{}
3eee4879-3c9c-4faf-88ef-2d183610ec2a	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:21.110019+00	{}
e31dc8d8-5b22-4e87-bada-6c8942fc01e5	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:23.121846+00	{}
6323a2da-42a1-40aa-ad69-02e5cdf3ce41	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:25.132827+00	{}
f457a6ce-f748-4e9c-aa65-a0990eeb623f	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:27.142692+00	{}
2940aba0-8d5e-47ca-9981-c3c4f2edcc77	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:29.15365+00	{}
80e8d512-d6ed-4094-a8a1-2c632023bcf4	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:31.164997+00	{}
ae60f3b7-c44a-4fa2-83a5-e8d612a18271	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:33.175493+00	{}
8fda7f93-771c-46a0-8788-91f4fff67567	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:35.186796+00	{}
033c0a41-c1fe-4973-b207-4626a535add7	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:37.198548+00	{}
eeaba37a-d9f0-487f-b5ee-09fc30d035d0	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:39.209559+00	{}
cc59aab0-bb11-4905-bf9a-e367e6b6cba3	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:41.220975+00	{}
683cb2f6-76c7-41c9-9e38-8fd8ea94420f	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:43.232144+00	{}
3cfc446a-62a9-4db4-9ace-2bfaf5d5c0e6	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:45.242019+00	{}
566b2fc6-361d-4f13-980d-72e02d1f84ba	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:47.251599+00	{}
74a8118d-16b2-4499-b988-dadcb26868dd	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:49.262232+00	{}
053690ef-1ff3-421f-a6ce-141004dac2b0	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:51.273172+00	{}
afd5de77-ae13-406a-a6fc-c8e049c2bc9e	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:53.284351+00	{}
f46ada8f-0510-4aa6-b064-54fc8b3e43ca	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:55.29602+00	{}
55f3f2a8-a2d4-4f91-9be7-927a75628823	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:57.307133+00	{}
b8d69ce1-a82c-488e-94a9-16a8b050e304	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:34:59.317771+00	{}
222a848b-163e-4dcb-ae3e-efb87be661a9	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:01.329263+00	{}
072524d7-2aa2-48b4-8330-5ef8f096c350	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:03.341439+00	{}
be2b195a-4f5b-41a0-89c1-7958d21cfd89	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:05.350544+00	{}
056c59f9-a6f2-4119-b5ff-37b9277acc36	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:07.366515+00	{}
8930fa56-55af-4064-a667-72e3b2453136	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:09.378214+00	{}
58a2186a-70d7-454c-9f25-6b878936dc2f	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:11.391019+00	{}
e60b7a2c-6eba-4fdf-a1ec-538a7f12491a	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:13.405168+00	{}
c6f39cc6-403a-401c-81b2-9fa67788d48c	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:15.415954+00	{}
1e2d5ac9-2801-4f11-969f-aa5a38e616a2	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:17.427235+00	{}
b88ffdc5-1f2d-48f8-8c0b-0467c9665a13	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:19.438122+00	{}
a625722c-4aa9-4958-94cc-25bd306539d2	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:24.531382+00	{}
487ce426-fa23-4e66-befd-2c65aa146873	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:35:26.549388+00	{}
bf733642-f2b1-4e0d-8d99-6c915b022b22	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 15:42:18.936679+00	{}
7b0444d6-7b0f-4229-b3eb-25e66f33d0f0	cc1e70c9-9058-4fd0-9b52-94012623f0e0	qr_authorized	telegram_user_id=139883458	\N	\N	2025-10-22 15:58:17.646323+00	{}
393faf2e-2190-468c-b37d-e9a5a67f89da	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-22 18:52:22.353256+00	{}
516dc648-733a-4649-a0c3-a5b3568863e2	cc1e70c9-9058-4fd0-9b52-94012623f0e0	session_status_updated	session_invalidated_by_telegram	\N	\N	2025-10-24 19:09:47.360785+00	{}
c2117762-2610-429e-9057-81e8cf96993f	cc1e70c9-9058-4fd0-9b52-94012623f0e0	qr_authorized	telegram_user_id=139883458	\N	\N	2025-10-24 19:20:51.8344+00	{}
\.


--
-- Data for Name: telegram_auth_logs; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.telegram_auth_logs (id, session_id, event, reason, error_code, ip, user_agent, latency_ms, at, meta) FROM stdin;
\.


--
-- Data for Name: telegram_sessions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.telegram_sessions (id, tenant_id, user_id, session_string_enc, key_id, status, created_at, updated_at, auth_error, error_details) FROM stdin;
013b2e7b-77ba-48e6-8e8c-c408a97e1b67	test-tenant	139883458	QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB	test-key-long	authorized	2025-10-24 18:47:32.71607+00	2025-10-24 19:28:02.772044+00	\N	\N
\.


--
-- Data for Name: tenants; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.tenants (id, name, created_at, settings) FROM stdin;
22222222-2222-2222-2222-222222222222	Test Tenant	2025-10-21 22:49:37.271912+00	{}
e70c43b0-e11d-45a8-8e51-f0ead91fb126	Tenant 139883458	2025-10-22 17:21:01.161759+00	{}
\.


--
-- Data for Name: user_channel; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.user_channel (user_id, channel_id, subscribed_at, is_active, settings) FROM stdin;
cc1e70c9-9058-4fd0-9b52-94012623f0e0	11c77f6b-2a54-4139-a20b-43d8a7950f34	2025-10-24 17:07:49.723702+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	2025-10-24 17:14:55.381414+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	7f194a2a-5206-4348-b42d-1b3976ec7d43	2025-10-24 17:15:32.402264+00	t	{}
\.


--
-- Data for Name: user_group; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.user_group (user_id, group_id, monitor_mentions, subscribed_at, is_active, settings) FROM stdin;
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.users (id, tenant_id, telegram_id, username, created_at, last_active_at, settings, telegram_session_enc, telegram_session_key_id, telegram_auth_status, telegram_auth_created_at, telegram_auth_updated_at, telegram_auth_error, first_name, last_name, role, tier) FROM stdin;
cc1e70c9-9058-4fd0-9b52-94012623f0e0	e70c43b0-e11d-45a8-8e51-f0ead91fb126	139883458	ilyasni	2025-10-22 11:35:51.066755+00	\N	{}	gAAAAABo-9GZ27jYivFHrW9WOwXKssV9K5DyFz6BuFWkNZJ8m_VTE0xyQxmt-vVW2G6Nz0pjLR-gL1L8Pt3pN69Kj6LBlgNK_zyR_SFQgpMoZNqFvBK9aEH93gDL9W8sJmIYTsP_UV-yQCp_iAsqgLa_EGrw-3ogzwCLHGg1T4zG59crWQqB7z6-2hSrctI2ohQuqc1ULF1Ukj85jIeRfufVXq9bPVAK0--Th2g2aDdTKlsD0L00cef6JchCZCQxtiwSEZRXDCIlEZwpnpGJGQEPNR1-kXueVMUjyJCjjzeaoh7XO4PGblIYVqg042Ygq3DDp_ju9y2ByRl3V4LlLp6kxYrsj9gG3dm9_1zyLkigy8Qtnx5fsu3y24lz8izc-GxTxkBfvH-ik6eyLFwmOw910gSfmQfgUl7oypXGLYQuAabiRxe7yooFT8Tn3QZE0HmOp_T8YhUbsj5VD_c_TpqLQxLsJ9lUc7T_cYUEfRfEWObuMv2mN5hKNSDUc-ndSeW8dYyMZy1-hEz6k4pk7bqPYMomxl8Prza2nX9EUrXpen0mSp1scB0=	default_key_1761146055.690612	authorized	2025-10-24 19:20:51.8344+00	2025-10-24 19:20:51.8344+00	\N	Ilya	Kozlov	admin	premium
\.


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: channels channels_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT channels_pkey PRIMARY KEY (id);


--
-- Name: encryption_keys encryption_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.encryption_keys
    ADD CONSTRAINT encryption_keys_pkey PRIMARY KEY (key_id);


--
-- Name: group_mentions group_mentions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_mentions
    ADD CONSTRAINT group_mentions_pkey PRIMARY KEY (id);


--
-- Name: group_messages group_messages_group_id_tg_message_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_group_id_tg_message_id_key UNIQUE (group_id, tg_message_id);


--
-- Name: group_messages group_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_pkey PRIMARY KEY (id);


--
-- Name: groups groups_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_pkey PRIMARY KEY (id);


--
-- Name: groups groups_tenant_id_tg_chat_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_tenant_id_tg_chat_id_key UNIQUE (tenant_id, tg_chat_id);


--
-- Name: indexing_status indexing_status_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.indexing_status
    ADD CONSTRAINT indexing_status_pkey PRIMARY KEY (id);


--
-- Name: invite_codes invite_codes_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_code_key UNIQUE (code);


--
-- Name: invite_codes invite_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_pkey PRIMARY KEY (id);


--
-- Name: outbox_events outbox_events_aggregate_id_event_type_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_aggregate_id_event_type_content_hash_key UNIQUE (aggregate_id, event_type, content_hash);


--
-- Name: outbox_events outbox_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (id);


--
-- Name: post_enrichment post_enrichment_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_enrichment
    ADD CONSTRAINT post_enrichment_pkey PRIMARY KEY (post_id);


--
-- Name: post_forwards post_forwards_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_forwards
    ADD CONSTRAINT post_forwards_pkey PRIMARY KEY (id);


--
-- Name: post_media post_media_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_media
    ADD CONSTRAINT post_media_pkey PRIMARY KEY (id);


--
-- Name: post_reactions post_reactions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_reactions
    ADD CONSTRAINT post_reactions_pkey PRIMARY KEY (id);


--
-- Name: post_replies post_replies_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_replies
    ADD CONSTRAINT post_replies_pkey PRIMARY KEY (id);


--
-- Name: posts posts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: telegram_auth_events telegram_auth_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_auth_events
    ADD CONSTRAINT telegram_auth_events_pkey PRIMARY KEY (id);


--
-- Name: telegram_auth_logs telegram_auth_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_auth_logs
    ADD CONSTRAINT telegram_auth_logs_pkey PRIMARY KEY (id);


--
-- Name: telegram_sessions telegram_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_sessions
    ADD CONSTRAINT telegram_sessions_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: user_channel user_channel_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_channel
    ADD CONSTRAINT user_channel_pkey PRIMARY KEY (user_id, channel_id);


--
-- Name: user_group user_group_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_group
    ADD CONSTRAINT user_group_pkey PRIMARY KEY (user_id, group_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_telegram_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_telegram_id_key UNIQUE (telegram_id);


--
-- Name: idx_channel_mapping_new; Type: INDEX; Schema: _shadow; Owner: postgres
--

CREATE INDEX idx_channel_mapping_new ON _shadow.channel_mapping USING btree (new_channel_id);


--
-- Name: idx_channel_mapping_old; Type: INDEX; Schema: _shadow; Owner: postgres
--

CREATE INDEX idx_channel_mapping_old ON _shadow.channel_mapping USING btree (old_channel_id);


--
-- Name: idx_post_mapping_old; Type: INDEX; Schema: _shadow; Owner: postgres
--

CREATE INDEX idx_post_mapping_old ON _shadow.post_mapping USING btree (old_post_id);


--
-- Name: idx_channels_is_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_channels_is_active ON public.channels USING btree (is_active);


--
-- Name: idx_channels_last_message_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_channels_last_message_at ON public.channels USING btree (last_message_at);


--
-- Name: idx_indexing_status_embedding_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_indexing_status_embedding_status ON public.indexing_status USING btree (embedding_status);


--
-- Name: idx_indexing_status_graph_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_indexing_status_graph_status ON public.indexing_status USING btree (graph_status);


--
-- Name: idx_indexing_status_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_indexing_status_post_id ON public.indexing_status USING btree (post_id);


--
-- Name: idx_invite_codes_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_invite_codes_active ON public.invite_codes USING btree (active);


--
-- Name: idx_invite_codes_tenant; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_invite_codes_tenant ON public.invite_codes USING btree (tenant_id);


--
-- Name: idx_posts_channel_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_posts_channel_id ON public.posts USING btree (channel_id);


--
-- Name: idx_posts_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_posts_created_at ON public.posts USING btree (created_at);


--
-- Name: idx_posts_is_processed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_posts_is_processed ON public.posts USING btree (is_processed);


--
-- Name: idx_users_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_users_created_at ON public.users USING btree (created_at);


--
-- Name: idx_users_telegram_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_users_telegram_id ON public.users USING btree (telegram_id);


--
-- Name: idx_users_tenant_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_users_tenant_id ON public.users USING btree (tenant_id);


--
-- Name: ix_group_mentions_message; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_mentions_message ON public.group_mentions USING btree (group_message_id);


--
-- Name: ix_group_mentions_processed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_mentions_processed ON public.group_mentions USING btree (is_processed) WHERE (is_processed = false);


--
-- Name: ix_group_mentions_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_mentions_user ON public.group_mentions USING btree (mentioned_user_tg_id);


--
-- Name: ix_group_messages_posted; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_messages_posted ON public.group_messages USING btree (group_id, posted_at DESC);


--
-- Name: ix_group_messages_sender; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_messages_sender ON public.group_messages USING btree (sender_tg_id);


--
-- Name: ix_groups_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_groups_active ON public.groups USING btree (is_active) WHERE (is_active = true);


--
-- Name: ix_groups_tenant; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_groups_tenant ON public.groups USING btree (tenant_id);


--
-- Name: ix_indexing_status_embed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_indexing_status_embed ON public.indexing_status USING btree (embedding_status);


--
-- Name: ix_indexing_status_graph; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_indexing_status_graph ON public.indexing_status USING btree (graph_status);


--
-- Name: ix_post_enrichment_enriched_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_enrichment_enriched_at ON public.post_enrichment USING btree (enriched_at DESC);


--
-- Name: ix_post_enrichment_tags_gin; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_enrichment_tags_gin ON public.post_enrichment USING gin (tags);


--
-- Name: ix_post_enrichment_vision_gin; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_enrichment_vision_gin ON public.post_enrichment USING gin (vision_labels);


--
-- Name: ix_post_forwards_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_forwards_created ON public.post_forwards USING btree (created_at DESC);


--
-- Name: ix_post_forwards_from_chat; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_forwards_from_chat ON public.post_forwards USING btree (post_id);


--
-- Name: ix_post_forwards_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_forwards_post_id ON public.post_forwards USING btree (post_id);


--
-- Name: ix_post_media_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_media_post_id ON public.post_media USING btree (post_id);


--
-- Name: ix_post_media_sha256; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_media_sha256 ON public.post_media USING btree (sha256) WHERE (sha256 IS NOT NULL);


--
-- Name: ix_post_media_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_media_type ON public.post_media USING btree (media_type);


--
-- Name: ix_post_reactions_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_created ON public.post_reactions USING btree (created_at DESC);


--
-- Name: ix_post_reactions_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_post_id ON public.post_reactions USING btree (post_id);


--
-- Name: ix_post_reactions_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_type ON public.post_reactions USING btree (reaction_type);


--
-- Name: ix_post_reactions_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_user ON public.post_reactions USING btree (user_tg_id) WHERE (user_tg_id IS NOT NULL);


--
-- Name: ix_post_reactions_value; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_value ON public.post_reactions USING btree (reaction_value);


--
-- Name: ix_post_replies_author; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_author ON public.post_replies USING btree (reply_author_tg_id) WHERE (reply_author_tg_id IS NOT NULL);


--
-- Name: ix_post_replies_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_post_id ON public.post_replies USING btree (post_id);


--
-- Name: ix_post_replies_posted; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_posted ON public.post_replies USING btree (reply_posted_at DESC);


--
-- Name: ix_post_replies_reply_to; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_reply_to ON public.post_replies USING btree (reply_to_post_id);


--
-- Name: ix_posts_author; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_author ON public.posts USING btree (post_author) WHERE (post_author IS NOT NULL);


--
-- Name: ix_posts_channel_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_channel_id ON public.posts USING btree (channel_id);


--
-- Name: ix_posts_channel_posted; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_channel_posted ON public.posts USING btree (channel_id, posted_at DESC);


--
-- Name: ix_posts_edited; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_edited ON public.posts USING btree (is_edited, edited_at DESC) WHERE (is_edited = true);


--
-- Name: ix_posts_forwards_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_forwards_count ON public.posts USING btree (forwards_count DESC) WHERE (forwards_count > 0);


--
-- Name: ix_posts_metrics_update; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_metrics_update ON public.posts USING btree (last_metrics_update DESC);


--
-- Name: ix_posts_pinned; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_pinned ON public.posts USING btree (is_pinned) WHERE (is_pinned = true);


--
-- Name: ix_posts_posted_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_posted_at ON public.posts USING btree (posted_at DESC);


--
-- Name: ix_posts_reactions_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_reactions_count ON public.posts USING btree (reactions_count DESC) WHERE (reactions_count > 0);


--
-- Name: ix_posts_replies_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_replies_count ON public.posts USING btree (replies_count DESC) WHERE (replies_count > 0);


--
-- Name: ix_posts_views_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_views_count ON public.posts USING btree (views_count DESC) WHERE (views_count > 0);


--
-- Name: ix_posts_yyyymm; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_yyyymm ON public.posts USING btree (yyyymm);


--
-- Name: ix_telegram_auth_events_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_events_at ON public.telegram_auth_events USING btree (at);


--
-- Name: ix_telegram_auth_events_event; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_events_event ON public.telegram_auth_events USING btree (event);


--
-- Name: ix_telegram_auth_events_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_events_user ON public.telegram_auth_events USING btree (user_id);


--
-- Name: ix_telegram_auth_logs_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_logs_at ON public.telegram_auth_logs USING btree (at);


--
-- Name: ix_telegram_auth_logs_event; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_logs_event ON public.telegram_auth_logs USING btree (event);


--
-- Name: ix_telegram_auth_logs_session; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_logs_session ON public.telegram_auth_logs USING btree (session_id);


--
-- Name: ix_telegram_sessions_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_created ON public.telegram_sessions USING btree (created_at);


--
-- Name: ix_telegram_sessions_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_status ON public.telegram_sessions USING btree (status);


--
-- Name: ix_telegram_sessions_tenant; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_tenant ON public.telegram_sessions USING btree (tenant_id);


--
-- Name: ix_telegram_sessions_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_user ON public.telegram_sessions USING btree (user_id);


--
-- Name: ix_user_channel_channel; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_channel_channel ON public.user_channel USING btree (channel_id) WHERE (is_active = true);


--
-- Name: ix_user_channel_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_channel_user ON public.user_channel USING btree (user_id) WHERE (is_active = true);


--
-- Name: ix_user_group_group; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_group_group ON public.user_group USING btree (group_id) WHERE (is_active = true);


--
-- Name: ix_user_group_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_group_user ON public.user_group USING btree (user_id) WHERE (is_active = true);


--
-- Name: ix_users_first_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_first_name ON public.users USING btree (first_name);


--
-- Name: ix_users_last_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_last_name ON public.users USING btree (last_name);


--
-- Name: ix_users_telegram_auth_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_telegram_auth_created ON public.users USING btree (telegram_auth_created_at);


--
-- Name: ix_users_telegram_auth_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_telegram_auth_status ON public.users USING btree (telegram_auth_status);


--
-- Name: ux_channels_tg_global; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_channels_tg_global ON public.channels USING btree (tg_channel_id);


--
-- Name: ux_group_messages; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_group_messages ON public.group_messages USING btree (group_id, tg_message_id);


--
-- Name: ux_post_media_dedup; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_post_media_dedup ON public.post_media USING btree (post_id, COALESCE(tg_file_unique_id, tg_file_id));


--
-- Name: ux_post_reactions_unique; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_post_reactions_unique ON public.post_reactions USING btree (post_id, reaction_type, reaction_value, user_tg_id);


--
-- Name: ux_posts_chan_msg; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_posts_chan_msg ON public.posts USING btree (channel_id, tg_message_id);


--
-- Name: post_enrichment trg_pe_updated; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_pe_updated BEFORE UPDATE ON public.post_enrichment FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: post_forwards trg_post_forwards_metrics; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_forwards_metrics AFTER INSERT OR DELETE OR UPDATE ON public.post_forwards FOR EACH ROW EXECUTE FUNCTION public.update_post_metrics();


--
-- Name: post_media trg_post_media_sync_ad; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_media_sync_ad AFTER DELETE ON public.post_media FOR EACH ROW EXECUTE FUNCTION public.sync_post_has_media();


--
-- Name: post_media trg_post_media_sync_ai; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_media_sync_ai AFTER INSERT ON public.post_media FOR EACH ROW EXECUTE FUNCTION public.sync_post_has_media();


--
-- Name: post_media trg_post_media_sync_au; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_media_sync_au AFTER UPDATE ON public.post_media FOR EACH ROW WHEN ((old.post_id IS DISTINCT FROM new.post_id)) EXECUTE FUNCTION public.sync_post_has_media();


--
-- Name: post_reactions trg_post_reactions_metrics; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_reactions_metrics AFTER INSERT OR DELETE OR UPDATE ON public.post_reactions FOR EACH ROW EXECUTE FUNCTION public.update_post_metrics();


--
-- Name: post_replies trg_post_replies_metrics; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_replies_metrics AFTER INSERT OR DELETE OR UPDATE ON public.post_replies FOR EACH ROW EXECUTE FUNCTION public.update_post_metrics();


--
-- Name: posts trg_posts_update_yyyymm; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_posts_update_yyyymm BEFORE INSERT OR UPDATE ON public.posts FOR EACH ROW EXECUTE FUNCTION public.update_yyyymm();


--
-- Name: telegram_sessions trigger_telegram_sessions_updated_at; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trigger_telegram_sessions_updated_at BEFORE UPDATE ON public.telegram_sessions FOR EACH ROW EXECUTE FUNCTION public.update_telegram_sessions_updated_at();


--
-- Name: users trigger_users_telegram_auth_updated_at; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trigger_users_telegram_auth_updated_at BEFORE UPDATE ON public.users FOR EACH ROW WHEN (((old.telegram_session_enc IS DISTINCT FROM new.telegram_session_enc) OR ((old.telegram_auth_status)::text IS DISTINCT FROM (new.telegram_auth_status)::text))) EXECUTE FUNCTION public.update_users_telegram_auth_updated_at();


--
-- Name: group_mentions group_mentions_group_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_mentions
    ADD CONSTRAINT group_mentions_group_message_id_fkey FOREIGN KEY (group_message_id) REFERENCES public.group_messages(id) ON DELETE CASCADE;


--
-- Name: group_mentions group_mentions_mentioned_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_mentions
    ADD CONSTRAINT group_mentions_mentioned_user_id_fkey FOREIGN KEY (mentioned_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: group_messages group_messages_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;


--
-- Name: groups groups_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: indexing_status indexing_status_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.indexing_status
    ADD CONSTRAINT indexing_status_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: invite_codes invite_codes_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: invite_codes invite_codes_last_used_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_last_used_by_fkey FOREIGN KEY (last_used_by) REFERENCES public.users(id);


--
-- Name: invite_codes invite_codes_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: post_enrichment post_enrichment_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_enrichment
    ADD CONSTRAINT post_enrichment_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: post_forwards post_forwards_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_forwards
    ADD CONSTRAINT post_forwards_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: post_media post_media_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_media
    ADD CONSTRAINT post_media_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: post_reactions post_reactions_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_reactions
    ADD CONSTRAINT post_reactions_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: post_replies post_replies_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_replies
    ADD CONSTRAINT post_replies_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: post_replies post_replies_reply_to_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_replies
    ADD CONSTRAINT post_replies_reply_to_post_id_fkey FOREIGN KEY (reply_to_post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- Name: posts posts_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id) ON DELETE CASCADE;


--
-- Name: telegram_auth_events telegram_auth_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_auth_events
    ADD CONSTRAINT telegram_auth_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: user_channel user_channel_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_channel
    ADD CONSTRAINT user_channel_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id) ON DELETE CASCADE;


--
-- Name: user_channel user_channel_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_channel
    ADD CONSTRAINT user_channel_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_group user_group_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_group
    ADD CONSTRAINT user_group_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;


--
-- Name: user_group user_group_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_group
    ADD CONSTRAINT user_group_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: post_enrichment enrichment_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY enrichment_by_subscription ON public.post_enrichment FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_enrichment.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- Name: post_enrichment enrichment_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY enrichment_worker_bypass ON public.post_enrichment TO worker_role USING (true) WITH CHECK (true);


--
-- Name: group_mentions group_mentions_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY group_mentions_worker_bypass ON public.group_mentions TO worker_role USING (true) WITH CHECK (true);


--
-- Name: group_messages group_messages_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY group_messages_worker_bypass ON public.group_messages TO worker_role USING (true) WITH CHECK (true);


--
-- Name: groups groups_by_user; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY groups_by_user ON public.groups FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM (public.users u
     JOIN public.user_group ug ON ((ug.user_id = u.id)))
  WHERE ((u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint) AND (u.tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid) AND (ug.group_id = groups.id) AND (ug.is_active = true)))));


--
-- Name: groups groups_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY groups_worker_bypass ON public.groups TO worker_role USING (true) WITH CHECK (true);


--
-- Name: post_forwards post_forwards_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_forwards_by_subscription ON public.post_forwards FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_forwards.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- Name: post_forwards post_forwards_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_forwards_worker_bypass ON public.post_forwards TO worker_role USING (true) WITH CHECK (true);


--
-- Name: post_media post_media_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_media_by_subscription ON public.post_media FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_media.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- Name: post_media post_media_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_media_worker_bypass ON public.post_media TO worker_role USING (true) WITH CHECK (true);


--
-- Name: post_reactions post_reactions_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_reactions_by_subscription ON public.post_reactions FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_reactions.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- Name: post_reactions post_reactions_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_reactions_worker_bypass ON public.post_reactions TO worker_role USING (true) WITH CHECK (true);


--
-- Name: post_replies post_replies_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_replies_by_subscription ON public.post_replies FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_replies.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- Name: post_replies post_replies_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_replies_worker_bypass ON public.post_replies TO worker_role USING (true) WITH CHECK (true);


--
-- Name: posts posts_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY posts_by_subscription ON public.posts FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM (public.user_channel uc
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((uc.channel_id = posts.channel_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- Name: posts posts_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY posts_worker_bypass ON public.posts TO worker_role USING (true) WITH CHECK (true);


--
-- Name: user_channel uc_write_own; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY uc_write_own ON public.user_channel TO authenticated USING ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = user_channel.user_id) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint) AND (u.tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = user_channel.user_id) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint) AND (u.tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))));


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: pg_database_owner
--

GRANT USAGE ON SCHEMA public TO anon;
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT USAGE ON SCHEMA public TO service_role;


--
-- Name: SCHEMA telegram_bot; Type: ACL; Schema: -; Owner: postgres
--

GRANT USAGE ON SCHEMA telegram_bot TO anon;
GRANT USAGE ON SCHEMA telegram_bot TO authenticated;
GRANT USAGE ON SCHEMA telegram_bot TO service_role;


--
-- Name: FUNCTION set_updated_at(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.set_updated_at() TO anon;
GRANT ALL ON FUNCTION public.set_updated_at() TO authenticated;
GRANT ALL ON FUNCTION public.set_updated_at() TO service_role;


--
-- Name: FUNCTION sync_post_has_media(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.sync_post_has_media() TO anon;
GRANT ALL ON FUNCTION public.sync_post_has_media() TO authenticated;
GRANT ALL ON FUNCTION public.sync_post_has_media() TO service_role;


--
-- Name: FUNCTION update_post_metrics(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_post_metrics() TO anon;
GRANT ALL ON FUNCTION public.update_post_metrics() TO authenticated;
GRANT ALL ON FUNCTION public.update_post_metrics() TO service_role;


--
-- Name: FUNCTION update_telegram_sessions_updated_at(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_telegram_sessions_updated_at() TO anon;
GRANT ALL ON FUNCTION public.update_telegram_sessions_updated_at() TO authenticated;
GRANT ALL ON FUNCTION public.update_telegram_sessions_updated_at() TO service_role;


--
-- Name: FUNCTION update_users_telegram_auth_updated_at(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_users_telegram_auth_updated_at() TO anon;
GRANT ALL ON FUNCTION public.update_users_telegram_auth_updated_at() TO authenticated;
GRANT ALL ON FUNCTION public.update_users_telegram_auth_updated_at() TO service_role;


--
-- Name: FUNCTION update_yyyymm(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_yyyymm() TO anon;
GRANT ALL ON FUNCTION public.update_yyyymm() TO authenticated;
GRANT ALL ON FUNCTION public.update_yyyymm() TO service_role;


--
-- Name: FUNCTION upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text) TO anon;
GRANT ALL ON FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text) TO authenticated;
GRANT ALL ON FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text) TO service_role;


--
-- Name: TABLE alembic_version; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.alembic_version TO anon;
GRANT ALL ON TABLE public.alembic_version TO authenticated;
GRANT ALL ON TABLE public.alembic_version TO service_role;


--
-- Name: TABLE channels; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.channels TO anon;
GRANT ALL ON TABLE public.channels TO authenticated;
GRANT ALL ON TABLE public.channels TO service_role;


--
-- Name: TABLE encryption_keys; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.encryption_keys TO anon;
GRANT ALL ON TABLE public.encryption_keys TO authenticated;
GRANT ALL ON TABLE public.encryption_keys TO service_role;


--
-- Name: TABLE group_mentions; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.group_mentions TO anon;
GRANT ALL ON TABLE public.group_mentions TO authenticated;
GRANT ALL ON TABLE public.group_mentions TO service_role;


--
-- Name: TABLE group_messages; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.group_messages TO anon;
GRANT ALL ON TABLE public.group_messages TO authenticated;
GRANT ALL ON TABLE public.group_messages TO service_role;


--
-- Name: TABLE groups; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.groups TO anon;
GRANT ALL ON TABLE public.groups TO authenticated;
GRANT ALL ON TABLE public.groups TO service_role;


--
-- Name: TABLE indexing_status; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.indexing_status TO anon;
GRANT ALL ON TABLE public.indexing_status TO authenticated;
GRANT ALL ON TABLE public.indexing_status TO service_role;


--
-- Name: TABLE invite_codes; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.invite_codes TO anon;
GRANT ALL ON TABLE public.invite_codes TO authenticated;
GRANT ALL ON TABLE public.invite_codes TO service_role;


--
-- Name: TABLE outbox_events; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.outbox_events TO anon;
GRANT ALL ON TABLE public.outbox_events TO authenticated;
GRANT ALL ON TABLE public.outbox_events TO service_role;


--
-- Name: TABLE post_enrichment; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_enrichment TO anon;
GRANT ALL ON TABLE public.post_enrichment TO authenticated;
GRANT ALL ON TABLE public.post_enrichment TO service_role;


--
-- Name: TABLE post_forwards; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_forwards TO anon;
GRANT ALL ON TABLE public.post_forwards TO authenticated;
GRANT ALL ON TABLE public.post_forwards TO service_role;


--
-- Name: TABLE post_media; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_media TO anon;
GRANT ALL ON TABLE public.post_media TO authenticated;
GRANT ALL ON TABLE public.post_media TO service_role;


--
-- Name: TABLE post_reactions; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_reactions TO anon;
GRANT ALL ON TABLE public.post_reactions TO authenticated;
GRANT ALL ON TABLE public.post_reactions TO service_role;


--
-- Name: TABLE post_replies; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_replies TO anon;
GRANT ALL ON TABLE public.post_replies TO authenticated;
GRANT ALL ON TABLE public.post_replies TO service_role;


--
-- Name: TABLE posts; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.posts TO anon;
GRANT ALL ON TABLE public.posts TO authenticated;
GRANT ALL ON TABLE public.posts TO service_role;


--
-- Name: TABLE schema_migrations; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.schema_migrations TO anon;
GRANT ALL ON TABLE public.schema_migrations TO authenticated;
GRANT ALL ON TABLE public.schema_migrations TO service_role;


--
-- Name: TABLE telegram_auth_events; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.telegram_auth_events TO anon;
GRANT ALL ON TABLE public.telegram_auth_events TO authenticated;
GRANT ALL ON TABLE public.telegram_auth_events TO service_role;


--
-- Name: TABLE telegram_auth_logs; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.telegram_auth_logs TO anon;
GRANT ALL ON TABLE public.telegram_auth_logs TO authenticated;
GRANT ALL ON TABLE public.telegram_auth_logs TO service_role;


--
-- Name: TABLE telegram_sessions; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.telegram_sessions TO anon;
GRANT ALL ON TABLE public.telegram_sessions TO authenticated;
GRANT ALL ON TABLE public.telegram_sessions TO service_role;


--
-- Name: TABLE tenants; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.tenants TO anon;
GRANT ALL ON TABLE public.tenants TO authenticated;
GRANT ALL ON TABLE public.tenants TO service_role;


--
-- Name: TABLE user_channel; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.user_channel TO anon;
GRANT ALL ON TABLE public.user_channel TO authenticated;
GRANT ALL ON TABLE public.user_channel TO service_role;


--
-- Name: TABLE user_group; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.user_group TO anon;
GRANT ALL ON TABLE public.user_group TO authenticated;
GRANT ALL ON TABLE public.user_group TO service_role;


--
-- Name: TABLE users; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.users TO anon;
GRANT ALL ON TABLE public.users TO authenticated;
GRANT ALL ON TABLE public.users TO service_role;


--
-- Name: TABLE indexing_status; Type: ACL; Schema: telegram_bot; Owner: postgres
--

GRANT SELECT ON TABLE telegram_bot.indexing_status TO anon;
GRANT SELECT ON TABLE telegram_bot.indexing_status TO authenticated;
GRANT SELECT ON TABLE telegram_bot.indexing_status TO service_role;


--
-- Name: TABLE tenants; Type: ACL; Schema: telegram_bot; Owner: postgres
--

GRANT SELECT ON TABLE telegram_bot.tenants TO anon;
GRANT SELECT ON TABLE telegram_bot.tenants TO authenticated;
GRANT SELECT ON TABLE telegram_bot.tenants TO service_role;


--
-- Name: TABLE users; Type: ACL; Schema: telegram_bot; Owner: postgres
--

GRANT SELECT ON TABLE telegram_bot.users TO anon;
GRANT SELECT ON TABLE telegram_bot.users TO authenticated;
GRANT SELECT ON TABLE telegram_bot.users TO service_role;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES  TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES  TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES  TO service_role;


--
-- Name: DEFAULT PRIVILEGES FOR FUNCTIONS; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS  TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS  TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS  TO service_role;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES  TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES  TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES  TO service_role;


--
-- PostgreSQL database dump complete
--

