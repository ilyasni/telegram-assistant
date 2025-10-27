--
-- PostgreSQL database dump
--

-- Dumped from database version 15.1 (Ubuntu 15.1-1.pgdg20.04+1)
-- Dumped by pg_dump version 15.7 (Ubuntu 15.7-1.pgdg20.04+1)

-- Started on 2025-10-26 18:32:36 UTC

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

DROP DATABASE IF EXISTS postgres;
--
-- TOC entry 3875 (class 1262 OID 5)
-- Name: postgres; Type: DATABASE; Schema: -; Owner: postgres
--

CREATE DATABASE postgres WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE postgres OWNER TO postgres;

\connect postgres

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
-- TOC entry 3876 (class 0 OID 0)
-- Dependencies: 3875
-- Name: DATABASE postgres; Type: COMMENT; Schema: -; Owner: postgres
--

COMMENT ON DATABASE postgres IS 'default administrative connection database';


--
-- TOC entry 13 (class 2615 OID 25001)
-- Name: _shadow; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA _shadow;


ALTER SCHEMA _shadow OWNER TO postgres;

--
-- TOC entry 3877 (class 0 OID 0)
-- Dependencies: 13
-- Name: SCHEMA _shadow; Type: COMMENT; Schema: -; Owner: postgres
--

COMMENT ON SCHEMA _shadow IS 'Артефакты миграции на глобальные каналы/посты';


--
-- TOC entry 7 (class 2615 OID 24590)
-- Name: graphql_public; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA graphql_public;


ALTER SCHEMA graphql_public OWNER TO postgres;

--
-- TOC entry 6 (class 2615 OID 24589)
-- Name: storage; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA storage;


ALTER SCHEMA storage OWNER TO postgres;

--
-- TOC entry 8 (class 2615 OID 24591)
-- Name: telegram_bot; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA telegram_bot;


ALTER SCHEMA telegram_bot OWNER TO postgres;

--
-- TOC entry 268 (class 1255 OID 25329)
-- Name: generate_telegram_post_url(text, bigint); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.generate_telegram_post_url(p_channel_username text, p_message_id bigint) RETURNS text
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Handle NULL inputs
    IF p_channel_username IS NULL OR p_message_id IS NULL THEN
        RETURN NULL;
    END IF;
    
    -- Handle empty username (private channels)
    IF TRIM(p_channel_username) = '' THEN
        RETURN NULL;
    END IF;
    
    -- Generate URL for public channels only
    -- Private channels require internal_id mapping (not in scope)
    RETURN CONCAT('https://t.me/', p_channel_username, '/', p_message_id);
END;
$$;


ALTER FUNCTION public.generate_telegram_post_url(p_channel_username text, p_message_id bigint) OWNER TO postgres;

--
-- TOC entry 267 (class 1255 OID 24975)
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
-- TOC entry 266 (class 1255 OID 24971)
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
-- TOC entry 271 (class 1255 OID 25112)
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
-- TOC entry 270 (class 1255 OID 25331)
-- Name: update_telegram_post_url(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.update_telegram_post_url() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.telegram_post_url = generate_telegram_post_url(
        (SELECT username FROM channels WHERE id = NEW.channel_id),
        NEW.telegram_message_id
    );
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_telegram_post_url() OWNER TO postgres;

--
-- TOC entry 264 (class 1255 OID 24760)
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
-- TOC entry 265 (class 1255 OID 24765)
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
-- TOC entry 269 (class 1255 OID 24995)
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
-- TOC entry 283 (class 1255 OID 25318)
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
-- TOC entry 254 (class 1259 OID 25004)
-- Name: channel_mapping; Type: TABLE; Schema: _shadow; Owner: postgres
--

CREATE TABLE _shadow.channel_mapping (
    old_channel_id uuid,
    new_channel_id uuid,
    tg_channel_id bigint
);


ALTER TABLE _shadow.channel_mapping OWNER TO postgres;

--
-- TOC entry 255 (class 1259 OID 25009)
-- Name: post_mapping; Type: TABLE; Schema: _shadow; Owner: postgres
--

CREATE TABLE _shadow.post_mapping (
    old_post_id uuid,
    new_post_id uuid
);


ALTER TABLE _shadow.post_mapping OWNER TO postgres;

--
-- TOC entry 241 (class 1259 OID 24637)
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO postgres;

--
-- TOC entry 234 (class 1259 OID 16413)
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
-- TOC entry 3890 (class 0 OID 0)
-- Dependencies: 234
-- Name: TABLE channels; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.channels IS 'Глобальные каналы (без tenant_id), доступ через user_channel';


--
-- TOC entry 242 (class 1259 OID 24726)
-- Name: encryption_keys; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.encryption_keys (
    key_id character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    retired_at timestamp with time zone
);


ALTER TABLE public.encryption_keys OWNER TO postgres;

--
-- TOC entry 3892 (class 0 OID 0)
-- Dependencies: 242
-- Name: TABLE encryption_keys; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.encryption_keys IS 'Ключи шифрования для Telegram StringSession (поддержка ротации)';


--
-- TOC entry 253 (class 1259 OID 24929)
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
-- TOC entry 3894 (class 0 OID 0)
-- Dependencies: 253
-- Name: TABLE group_mentions; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.group_mentions IS 'Упоминания пользователей в группах';


--
-- TOC entry 252 (class 1259 OID 24913)
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
-- TOC entry 3896 (class 0 OID 0)
-- Dependencies: 252
-- Name: TABLE group_messages; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.group_messages IS 'Сообщения из групповых чатов';


--
-- TOC entry 250 (class 1259 OID 24874)
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
-- TOC entry 3898 (class 0 OID 0)
-- Dependencies: 250
-- Name: TABLE groups; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.groups IS 'Групповые чаты для мониторинга упоминаний';


--
-- TOC entry 236 (class 1259 OID 16456)
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
    vector_id character varying(255),
    CONSTRAINT indexing_status_embedding_status_check CHECK (((embedding_status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT indexing_status_graph_status_check CHECK (((graph_status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying])::text[])))
);


ALTER TABLE public.indexing_status OWNER TO postgres;

--
-- TOC entry 246 (class 1259 OID 24789)
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
-- TOC entry 261 (class 1259 OID 25306)
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
-- TOC entry 248 (class 1259 OID 24842)
-- Name: post_enrichment; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.post_enrichment (
    post_id uuid NOT NULL,
    vision_labels jsonb DEFAULT '[]'::jsonb,
    ocr_text text,
    crawl_md text,
    enrichment_provider character varying(50),
    enriched_at timestamp with time zone DEFAULT now() NOT NULL,
    enrichment_latency_ms integer,
    metadata jsonb DEFAULT '{}'::jsonb,
    updated_at timestamp with time zone DEFAULT now(),
    kind text DEFAULT 'tags'::text NOT NULL,
    tags text[]
);


ALTER TABLE public.post_enrichment OWNER TO postgres;

--
-- TOC entry 3903 (class 0 OID 0)
-- Dependencies: 248
-- Name: TABLE post_enrichment; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_enrichment IS 'Обогащённые данные постов: теги, OCR, vision, crawl результаты';


--
-- TOC entry 259 (class 1259 OID 25071)
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
-- TOC entry 3905 (class 0 OID 0)
-- Dependencies: 259
-- Name: TABLE post_forwards; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_forwards IS 'Репосты постов в другие чаты/каналы';


--
-- TOC entry 249 (class 1259 OID 24859)
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
-- TOC entry 3907 (class 0 OID 0)
-- Dependencies: 249
-- Name: TABLE post_media; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_media IS 'Медиа-файлы постов с Telegram-специфичными идентификаторами';


--
-- TOC entry 258 (class 1259 OID 25049)
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
-- TOC entry 3909 (class 0 OID 0)
-- Dependencies: 258
-- Name: TABLE post_reactions; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_reactions IS 'Реакции на посты (эмодзи, кастомные эмодзи, платные)';


--
-- TOC entry 260 (class 1259 OID 25089)
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
-- TOC entry 3911 (class 0 OID 0)
-- Dependencies: 260
-- Name: TABLE post_replies; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.post_replies IS 'Комментарии/ответы на посты';


--
-- TOC entry 235 (class 1259 OID 16432)
-- Name: posts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.posts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    channel_id uuid NOT NULL,
    telegram_message_id bigint NOT NULL,
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
    telegram_post_url text,
    CONSTRAINT posts_telegram_message_id_check CHECK ((telegram_message_id > 0))
);

ALTER TABLE ONLY public.posts FORCE ROW LEVEL SECURITY;


ALTER TABLE public.posts OWNER TO postgres;

--
-- TOC entry 3913 (class 0 OID 0)
-- Dependencies: 235
-- Name: TABLE posts; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.posts IS 'Глобальные посты (без tenant_id), доступ через user_channel + RLS';


--
-- TOC entry 3914 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.telegram_message_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.telegram_message_id IS 'Telegram message ID (bigint)';


--
-- TOC entry 3915 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.views_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.views_count IS 'Количество просмотров поста';


--
-- TOC entry 3916 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.forwards_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.forwards_count IS 'Количество репостов поста';


--
-- TOC entry 3917 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.reactions_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.reactions_count IS 'Количество уникальных реакций';


--
-- TOC entry 3918 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.replies_count; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.replies_count IS 'Количество комментариев';


--
-- TOC entry 3919 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.is_pinned; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.is_pinned IS 'Закреплён ли пост в канале';


--
-- TOC entry 3920 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.is_edited; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.is_edited IS 'Был ли пост отредактирован';


--
-- TOC entry 3921 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.post_author; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.post_author IS 'Автор поста (если доступен)';


--
-- TOC entry 3922 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.last_metrics_update; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.last_metrics_update IS 'Время последнего обновления метрик';


--
-- TOC entry 3923 (class 0 OID 0)
-- Dependencies: 235
-- Name: COLUMN posts.telegram_post_url; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.posts.telegram_post_url IS 'Direct link to Telegram post (https://t.me/username/message_id)';


--
-- TOC entry 263 (class 1259 OID 25338)
-- Name: posts_legacy; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.posts_legacy AS
 SELECT posts.id,
    posts.channel_id,
    posts.telegram_message_id AS tg_message_id,
    posts.content,
    posts.media_urls,
    posts.created_at,
    posts.is_processed,
    posts.posted_at,
    posts.url,
    posts.has_media,
    posts.yyyymm,
    posts.views_count,
    posts.forwards_count,
    posts.reactions_count,
    posts.replies_count,
    posts.is_pinned,
    posts.is_edited,
    posts.edited_at,
    posts.post_author,
    posts.reply_to_message_id,
    posts.reply_to_chat_id,
    posts.via_bot_id,
    posts.via_business_bot_id,
    posts.is_silent,
    posts.is_legacy,
    posts.noforwards,
    posts.invert_media,
    posts.last_metrics_update,
    posts.telegram_post_url
   FROM public.posts;


ALTER TABLE public.posts_legacy OWNER TO postgres;

--
-- TOC entry 262 (class 1259 OID 25333)
-- Name: posts_with_telegram_links; Type: VIEW; Schema: public; Owner: postgres
--

CREATE VIEW public.posts_with_telegram_links AS
 SELECT p.id,
    p.channel_id,
    p.telegram_message_id,
    p.content,
    p.media_urls,
    p.created_at,
    p.is_processed,
    p.posted_at,
    p.url,
    p.has_media,
    p.yyyymm,
    p.views_count,
    p.forwards_count,
    p.reactions_count,
    p.replies_count,
    p.is_pinned,
    p.is_edited,
    p.edited_at,
    p.post_author,
    p.reply_to_message_id,
    p.reply_to_chat_id,
    p.via_bot_id,
    p.via_business_bot_id,
    p.is_silent,
    p.is_legacy,
    p.noforwards,
    p.invert_media,
    p.last_metrics_update,
    p.telegram_post_url,
    c.username AS channel_username,
    c.title AS channel_title,
    public.generate_telegram_post_url((c.username)::text, p.telegram_message_id) AS computed_telegram_url
   FROM (public.posts p
     JOIN public.channels c ON ((p.channel_id = c.id)));


ALTER TABLE public.posts_with_telegram_links OWNER TO postgres;

--
-- TOC entry 237 (class 1259 OID 24577)
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.schema_migrations (
    version character varying(14) NOT NULL
);


ALTER TABLE public.schema_migrations OWNER TO postgres;

--
-- TOC entry 245 (class 1259 OID 24767)
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
-- TOC entry 3928 (class 0 OID 0)
-- Dependencies: 245
-- Name: TABLE telegram_auth_events; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.telegram_auth_events IS 'События авторизации Telegram (упрощенная версия)';


--
-- TOC entry 244 (class 1259 OID 24747)
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
-- TOC entry 3930 (class 0 OID 0)
-- Dependencies: 244
-- Name: TABLE telegram_auth_logs; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.telegram_auth_logs IS 'Аудит событий авторизации Telegram (QR/miniapp)';


--
-- TOC entry 243 (class 1259 OID 24732)
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
-- TOC entry 3932 (class 0 OID 0)
-- Dependencies: 243
-- Name: TABLE telegram_sessions; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.telegram_sessions IS 'Зашифрованные Telethon StringSession на арендатора/пользователя';


--
-- TOC entry 3933 (class 0 OID 0)
-- Dependencies: 243
-- Name: COLUMN telegram_sessions.session_string_enc; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.telegram_sessions.session_string_enc IS 'Зашифрованная StringSession от Telethon';


--
-- TOC entry 3934 (class 0 OID 0)
-- Dependencies: 243
-- Name: COLUMN telegram_sessions.key_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.telegram_sessions.key_id IS 'ID ключа шифрования для расшифровки session_string_enc';


--
-- TOC entry 3935 (class 0 OID 0)
-- Dependencies: 243
-- Name: COLUMN telegram_sessions.status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.telegram_sessions.status IS 'Статус сессии: pending|authorized|revoked|expired|failed';


--
-- TOC entry 232 (class 1259 OID 16384)
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
-- TOC entry 247 (class 1259 OID 24822)
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
-- TOC entry 3938 (class 0 OID 0)
-- Dependencies: 247
-- Name: TABLE user_channel; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.user_channel IS 'Many-to-many связь пользователей и каналов для подписок';


--
-- TOC entry 251 (class 1259 OID 24892)
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
-- TOC entry 3940 (class 0 OID 0)
-- Dependencies: 251
-- Name: TABLE user_group; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON TABLE public.user_group IS 'Подписки пользователей на группы';


--
-- TOC entry 233 (class 1259 OID 16395)
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
-- TOC entry 3942 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN users.username; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.username IS 'Username пользователя в Telegram (@username)';


--
-- TOC entry 3943 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN users.telegram_session_enc; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.telegram_session_enc IS 'Зашифрованная StringSession от Telethon';


--
-- TOC entry 3944 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN users.telegram_session_key_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.telegram_session_key_id IS 'ID ключа шифрования для расшифровки session_string_enc';


--
-- TOC entry 3945 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN users.telegram_auth_status; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.telegram_auth_status IS 'Статус авторизации: pending|authorized|revoked|expired|failed';


--
-- TOC entry 3946 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN users.first_name; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.first_name IS 'Имя пользователя из Telegram';


--
-- TOC entry 3947 (class 0 OID 0)
-- Dependencies: 233
-- Name: COLUMN users.last_name; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.users.last_name IS 'Фамилия пользователя из Telegram';


--
-- TOC entry 256 (class 1259 OID 25022)
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
-- TOC entry 240 (class 1259 OID 24608)
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
-- TOC entry 257 (class 1259 OID 25026)
-- Name: posts; Type: VIEW; Schema: telegram_bot; Owner: postgres
--

CREATE VIEW telegram_bot.posts AS
 SELECT posts.id,
    posts.channel_id,
    posts.telegram_message_id,
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
-- TOC entry 238 (class 1259 OID 24592)
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
-- TOC entry 239 (class 1259 OID 24596)
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
-- TOC entry 3864 (class 0 OID 25004)
-- Dependencies: 254
-- Data for Name: channel_mapping; Type: TABLE DATA; Schema: _shadow; Owner: postgres
--

COPY _shadow.channel_mapping (old_channel_id, new_channel_id, tg_channel_id) FROM stdin;
33333333-3333-3333-3333-333333333333	33333333-3333-3333-3333-333333333333	-1001234567890
\.


--
-- TOC entry 3865 (class 0 OID 25009)
-- Dependencies: 255
-- Data for Name: post_mapping; Type: TABLE DATA; Schema: _shadow; Owner: postgres
--

COPY _shadow.post_mapping (old_post_id, new_post_id) FROM stdin;
\.


--
-- TOC entry 3851 (class 0 OID 24637)
-- Dependencies: 241
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.alembic_version (version_num) FROM stdin;
b44bd6941d11
\.


--
-- TOC entry 3847 (class 0 OID 16413)
-- Dependencies: 234
-- Data for Name: channels; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.channels (id, tg_channel_id, username, title, is_active, last_message_at, created_at, settings) FROM stdin;
11c77f6b-2a54-4139-a20b-43d8a7950f34	\N	@AGI_and_RL	@AGI_and_RL	t	\N	2025-10-24 16:52:16.731331+00	{}
630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	\N	@business_ru	@business_ru	t	\N	2025-10-24 17:14:55.377865+00	{}
7f194a2a-5206-4348-b42d-1b3976ec7d43	\N	@okolo_art	@okolo_art	t	\N	2025-10-24 17:15:32.398271+00	{}
8b917088-703b-4c7e-978a-f27f7f2af34e	\N	@ilyabirman_channel	@ilyabirman_channel	t	\N	2025-10-26 13:33:56.509455+00	{}
ca114e40-5c61-409d-8e27-433d7675cf22	\N	@breakingtrends	@breakingtrends	t	\N	2025-10-26 13:34:04.704339+00	{}
66a20b7c-23f9-4c8f-899a-8b9f570a39db	\N	@ruspm	@ruspm	t	\N	2025-10-26 13:34:11.827693+00	{}
042611a1-78e1-45d8-8994-0853d6ebd2f3	\N	@awdee	@awdee	t	\N	2025-10-26 13:34:22.32808+00	{}
c47eb63a-c9ad-4c90-820b-9f1156c9c2a8	\N	@bbe_school	@bbe_school	t	\N	2025-10-26 13:34:30.936388+00	{}
2a832a81-87e4-442d-ab1e-0f502029b733	\N	@neuro_code	@neuro_code	t	\N	2025-10-26 13:34:36.537379+00	{}
8e2cdd08-8ea0-4fdf-9bfb-04d3f132b5b7	\N	@b_goncharenko	@b_goncharenko	t	\N	2025-10-26 13:34:42.375238+00	{}
81324c8d-75a1-4ea7-9093-38e19d51c261	\N	@techno_yandex	@techno_yandex	t	\N	2025-10-26 13:35:01.791153+00	{}
95c7210e-619d-403b-9044-54377b18ce8c	\N	@tehnomaniak07	@tehnomaniak07	t	\N	2025-10-26 13:35:10.579054+00	{}
d5a9a167-f073-401a-a725-69e68cbfa463	\N	@ozondesign	@ozondesign	t	\N	2025-10-26 13:35:20.777385+00	{}
fdb3a110-d9ed-4a84-a901-2258cb6f94fa	\N	@jun_hi	@jun_hi	t	\N	2025-10-26 13:35:27.665064+00	{}
817dc325-c569-4c68-aa1d-58f2f276619c	\N	@How2AI	@How2AI	t	\N	2025-10-26 13:35:34.007171+00	{}
5223b6df-58f9-4758-b7a9-b49bb87b72a0	\N	@new_yorko_times	@new_yorko_times	t	\N	2025-10-26 13:35:39.866111+00	{}
3abb63cd-e950-42ae-8711-ce5cd4ee5f19	\N	@editboat	@editboat	t	\N	2025-10-26 13:35:45.997171+00	{}
93ba043b-23b7-4549-963c-2df2d2a1974b	\N	@proudobstvo	@proudobstvo	t	\N	2025-10-26 13:35:51.596034+00	{}
884a410c-118b-434c-bbfd-22251bbab33a	\N	@designsniper	@designsniper	t	\N	2025-10-26 13:36:00.032059+00	{}
f58d7b92-c4a3-4ead-9e51-d12bb3527755	\N	@rybolos_channel	@rybolos_channel	t	\N	2025-10-26 13:36:05.780286+00	{}
6dd6c4d1-7bdd-4b99-8584-732168095141	\N	@uxnotes	@uxnotes	t	\N	2025-10-26 13:36:11.724491+00	{}
99913aea-4b0d-4d1a-85ab-2f7d4f1f673b	\N	@aiwizards	@aiwizards	t	\N	2025-10-26 13:36:17.477894+00	{}
e6abff7e-a499-49c4-8402-c67aff9e1ed8	\N	@uxhorn	@uxhorn	t	\N	2025-10-26 13:36:35.582044+00	{}
2bda56ff-6bfd-49a0-b70f-a1336befa800	\N	@ai_newz	@ai_newz	t	\N	2025-10-26 13:36:42.949266+00	{}
8726a621-ef23-4ee1-9fe2-d91c36d4bfb4	\N	@llm_under_hood	@llm_under_hood	t	\N	2025-10-26 13:36:50.62312+00	{}
b3557bfd-ef2d-40af-b4d5-7e56ce1b9b4a	\N	@uxidesign	@uxidesign	t	\N	2025-10-26 13:36:56.066475+00	{}
f65dc96b-509b-4b3b-b10c-4e3aca335927	\N	@betamoscow	@betamoscow	t	\N	2025-10-26 13:37:02.198229+00	{}
b279b366-77ce-44f7-90e8-95792b8fb0ec	\N	@desprod	@desprod	t	\N	2025-10-26 13:37:07.593073+00	{}
b6e39529-0f33-4839-8849-d4c2f51aa086	\N	@pdigest	@pdigest	t	\N	2025-10-26 13:37:13.69216+00	{}
b4ac44ac-e0c6-4481-b9ca-10f12752fe25	\N	@monkeyinlaw	@monkeyinlaw	t	\N	2025-10-26 13:37:19.391134+00	{}
ce64a6a4-082e-4865-a0e6-dc38ee810c25	\N	@ponchiknews	@ponchiknews	t	\N	2025-10-26 13:37:26.03868+00	{}
d10a6208-fc12-4953-9beb-15e080ae31a0	\N	@hardclient	@hardclient	t	\N	2025-10-26 13:37:31.482982+00	{}
59e1cd47-5dcb-45fa-b0bf-d4f482fa8d78	\N	@dsoloveev	@dsoloveev	t	\N	2025-10-26 13:37:37.303599+00	{}
9f9651e7-5c0b-451a-91ab-df79dda08059	\N	@postpostresearch	@postpostresearch	t	\N	2025-10-26 13:37:43.004158+00	{}
c5fbe0ca-91bc-4589-b999-798f2003c285	\N	@slashdesigner	@slashdesigner	t	\N	2025-10-26 13:37:48.398593+00	{}
720da7af-a794-4c93-851b-cc376f20b6c8	\N	@mosinkru	@mosinkru	t	\N	2025-10-26 13:37:57.072999+00	{}
bbc2b864-d004-4e08-b08b-35ef06094344	\N	@Who_X	@Who_X	t	\N	2025-10-26 13:38:04.026697+00	{}
e9c05cee-e202-4c84-9f99-64c16f88fdbb	\N	@fffworks	@fffworks	t	\N	2025-10-26 13:38:09.138951+00	{}
\.


--
-- TOC entry 3852 (class 0 OID 24726)
-- Dependencies: 242
-- Data for Name: encryption_keys; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.encryption_keys (key_id, created_at, retired_at) FROM stdin;
default_key_1761146055.690612	2025-10-22 15:14:15.690612+00	\N
\.


--
-- TOC entry 3863 (class 0 OID 24929)
-- Dependencies: 253
-- Data for Name: group_mentions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.group_mentions (id, group_message_id, mentioned_user_id, mentioned_user_tg_id, context_snippet, is_processed, processed_at, created_at) FROM stdin;
\.


--
-- TOC entry 3862 (class 0 OID 24913)
-- Dependencies: 252
-- Data for Name: group_messages; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.group_messages (id, group_id, tg_message_id, sender_tg_id, sender_username, content, posted_at, created_at) FROM stdin;
\.


--
-- TOC entry 3860 (class 0 OID 24874)
-- Dependencies: 250
-- Data for Name: groups; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.groups (id, tenant_id, tg_chat_id, title, username, is_active, last_checked_at, created_at, settings) FROM stdin;
\.


--
-- TOC entry 3849 (class 0 OID 16456)
-- Dependencies: 236
-- Data for Name: indexing_status; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.indexing_status (id, post_id, embedding_status, graph_status, processing_started_at, processing_completed_at, error_message, retry_count, vector_id) FROM stdin;
5c367ff9-57c2-4684-8aa4-3a9b86e13f06	5e9091d2-1f60-4ab0-aa52-6689a74b0a63	completed	completed	2025-10-24 21:11:09.963335+00	2025-10-24 23:41:05.546442+00	\N	0	65d02ab4-ba49-4492-a7ff-90c2f3f8dcaf
a038e6b1-80d9-4c9d-bf87-53c8dc60278b	c385d8eb-4ec0-4003-b42e-d5c571cb8080	completed	completed	2025-10-24 21:11:11.322819+00	2025-10-25 00:11:54.410671+00	\N	0	3e8cdd3d-317f-4e37-8afa-8ad074480209
de6f351f-dc1a-4a38-a5b4-67f8258815f7	3b5295c7-40b0-4bbd-98d0-028a6321bf3c	completed	completed	2025-10-24 21:11:11.295785+00	2025-10-25 00:16:55.527274+00	\N	0	d2e81b93-9703-4716-9a86-05487aad6ea0
1efc86a9-5080-406d-b414-7bd98c68b16d	b7945933-58d8-4e2a-98da-803f7d08bf9f	completed	completed	2025-10-24 21:11:11.350849+00	2025-10-25 00:20:11.103378+00	\N	0	4dd46d9d-349f-4b06-8918-14250e07c16b
3deb12bc-38f3-4bac-b51e-e52b3c029b62	f7080db0-604c-4659-9803-28ed87f08110	completed	completed	2025-10-24 21:11:11.206127+00	2025-10-25 00:22:59.478891+00	\N	0	903544b4-f01d-4b22-83ef-e083c5db7305
f658af9f-cdcf-409a-94d0-ba5ccd65ef3c	a9aeb5a9-0971-4cd1-92ff-b35854c8e9e7	completed	completed	2025-10-24 21:11:11.168985+00	2025-10-25 00:22:59.791691+00	\N	0	cdcf8299-c8f2-465a-a8b8-c20321ff5c21
68373335-8d87-4370-a7e0-bd9f715399f1	ed00d5ca-920b-4153-8827-3897169b8733	completed	completed	2025-10-24 21:11:11.139567+00	2025-10-25 00:23:00.095097+00	\N	0	23ec53c6-67be-41be-bcdc-4c14d53d2bc4
8646cb06-33cd-44ad-8937-01b916091984	f1af80cb-727b-4c0b-b9d1-e109c6bee007	completed	completed	2025-10-24 21:11:11.110178+00	2025-10-25 00:23:00.411089+00	\N	0	bf105106-fc6f-4f6b-bd51-9786e228b842
99075461-f6f6-4974-93fc-d8735594bee6	ce76c62e-dd13-470b-8c34-569f2cb4ffc2	completed	completed	2025-10-24 21:11:10.995101+00	2025-10-25 00:23:00.876506+00	\N	0	dfda776e-4d7f-49af-8286-e2b0aebfe747
33ced12c-edae-4106-8e83-417a86cc709b	26c40906-8f94-4fbc-afc7-754009231bc1	completed	completed	2025-10-24 21:11:10.891266+00	2025-10-25 00:23:01.421249+00	\N	0	3fbb09b2-1763-43b5-b579-0adfc5e1c33a
cc178a8e-1c3c-430a-93a0-a88a3fb61d7b	1bf48881-0bae-4a74-8ca7-e14ee0faee6c	completed	completed	2025-10-24 21:11:10.865192+00	2025-10-25 00:23:01.789127+00	\N	0	97a40385-c043-401e-9abf-399781296624
5da1bff5-9c58-4ceb-ad5f-0002a834f013	8abdbef5-0244-46eb-9237-c8dced704757	completed	completed	2025-10-24 21:10:06.77201+00	2025-10-24 21:59:15.342295+00	\N	0	6bbe5d61-2a97-4321-8395-f49e1a1deadb
4a8419ac-9702-40c0-9b46-0a7ae6d27fd7	808856a7-97f7-43ab-b2de-9f7c0fba740b	completed	completed	2025-10-24 21:11:10.839413+00	2025-10-25 00:23:02.091369+00	\N	0	f0a7960c-339c-4014-9264-860fa8e2878d
20dbf3a8-a5eb-4935-9fac-2a1f9538200d	698e850b-f5af-477a-9480-217b28d5b940	completed	completed	2025-10-24 21:11:10.811258+00	2025-10-25 00:23:02.535946+00	\N	0	cb54168e-a626-4777-833b-5381aaf42992
20d70f48-7549-4b39-b383-ce3e5d315c4f	b64de121-1358-45d7-aa72-1139f6d6398c	completed	completed	2025-10-24 21:11:10.760492+00	2025-10-25 00:23:02.969739+00	\N	0	725c3c9d-0da4-4e90-b887-05d52b667033
52ccf08e-a401-419b-bbbf-5780a3ad9057	8c133af9-4cf2-487e-9310-1afdb8626deb	completed	completed	2025-10-24 21:11:10.734589+00	2025-10-25 00:23:03.26264+00	\N	0	0acb8cf6-4714-4042-a7aa-312534249e65
b0aaf9bf-f5ad-4f3b-a148-fc8e454e54b1	11ff9a4d-6de2-4ead-b0ef-a886b9b471e5	completed	completed	2025-10-24 21:11:10.708366+00	2025-10-25 00:23:03.570077+00	\N	0	fd316a12-ba30-4315-b77a-dd452f3b2d1e
5557bb06-8667-417a-a055-a9808648ad77	11fced41-c9d9-4729-8246-5530809970b5	completed	completed	2025-10-24 21:11:10.655904+00	2025-10-25 00:23:04.242998+00	\N	0	2867f9e1-bdcc-4a87-bbcf-d22dd5939089
13f98ecd-8540-4450-8ebc-05b6213df076	79972a07-941a-4c9a-9ef9-e4ee4a84b88e	completed	completed	2025-10-24 21:11:10.605314+00	2025-10-25 00:23:04.629992+00	\N	0	df257029-9348-4b41-b788-ffb2416fe923
335d3f5e-4db4-4765-b58c-7846d5276e12	e00ececa-4cd6-4867-8885-479cc26a165b	completed	completed	2025-10-24 21:11:10.553627+00	2025-10-25 00:23:04.960184+00	\N	0	e3ada17d-04e6-4cf4-9dde-8fb3131d18cb
d761e8ba-1d70-4bd0-9fe7-50684cbc2729	7e48d2a7-3563-4a5b-a2ae-66fd8094346c	completed	completed	2025-10-24 21:11:10.449+00	2025-10-25 00:23:05.383136+00	\N	0	84006e22-0a24-4c0b-b639-51a0f53f959d
9b3b0395-9ca2-4758-8a48-bc27102ab86b	d3014129-403a-42e8-8e58-242d55c2a1f3	completed	completed	2025-10-24 21:11:10.421307+00	2025-10-25 00:23:05.691474+00	\N	0	22c31dba-2f3b-49f3-8581-8b60b084bd53
d36c2711-e3b0-4319-a702-2421e277337d	d4063284-2a3b-4e41-8a9b-d9a07a79ef5f	completed	completed	2025-10-24 21:11:10.392508+00	2025-10-25 00:23:06.007365+00	\N	0	1372bbaa-55ed-4ca5-97e1-995ff313dcb4
3802eb4f-207d-4b3d-afc5-7889b431e287	2cfdde07-51e5-4012-a945-593fdc2ffe14	completed	completed	2025-10-24 21:11:10.366674+00	2025-10-25 00:23:06.331004+00	\N	0	0591de5a-e82b-428c-9f8b-e4903bc60a50
506ac3e7-87b7-43a5-aa70-76a3e752beee	270db607-b928-43a7-a6bb-5e87052f9be9	completed	completed	2025-10-24 21:11:10.338557+00	2025-10-25 00:23:06.666105+00	\N	0	30908aaf-3986-48b1-ab61-5320ad788327
303d5602-5c55-4d7c-8033-2e9f5677d407	4ec78401-078d-46ec-a880-9468ed644d6f	completed	completed	2025-10-24 21:11:11.051378+00	2025-10-24 23:36:56.430411+00	\N	0	5925b627-07c3-4634-aa11-ce7b36190409
5c770a21-55b7-49c0-8b8a-a58cd9a3b01a	b01f3a6b-7971-43aa-8c55-04e70353895b	completed	completed	2025-10-24 21:11:10.277563+00	2025-10-25 00:23:07.439556+00	\N	0	112dcbde-45db-455f-824a-77ca4722571f
4d1fa5f1-d13f-41ec-9674-0c63c6ebb72c	fcb2ab52-7038-41a7-a019-4c0e14944a47	completed	completed	2025-10-24 21:11:11.081426+00	2025-10-24 23:36:59.108466+00	\N	0	e0b5b60d-f776-4120-bf6a-7c1fd849bff4
3674b0eb-71d9-4c29-b08a-5aee09e53109	43a1b69c-38b3-4fc8-9504-96ceaf760a89	completed	completed	2025-10-24 21:11:10.197397+00	2025-10-25 00:23:08.107386+00	\N	0	e5a9644f-7ee4-417b-807f-6823d724b22e
7eb08647-9243-45a4-8b86-f5cf3894898e	1d6a676c-4478-4b1c-85e2-ff317a90d01e	completed	completed	2025-10-24 21:11:10.171785+00	2025-10-25 00:23:08.239534+00	\N	0	1f4d3dc1-cff0-416b-a6c6-8b96980c3e3e
4fe1c0e1-2828-47d6-96d4-8cae6b70ce50	85aa4570-cb9e-4520-ba8e-be064d060b7f	completed	completed	2025-10-24 21:11:11.022295+00	2025-10-24 23:37:04.577749+00	\N	0	eb25a33a-e30f-4550-b830-b05867a2f172
c84dd738-41e7-417b-908d-503498161d14	0357b00a-3872-4d54-b95c-ef203f8c56d3	completed	completed	2025-10-24 21:11:10.145658+00	2025-10-25 00:23:09.278775+00	\N	0	9bac7e73-bedd-46f7-95cf-68658bc9397e
33759d3d-6e23-45ee-859d-27652930b80c	e9592709-064d-4839-883e-85f66000290a	completed	completed	2025-10-24 21:11:10.093612+00	2025-10-25 00:23:10.713132+00	\N	0	2d408f68-ba95-47b6-9439-91647ad8b5c5
a171f96d-2eac-408c-ae73-76e6964af917	570b04fc-e265-4793-b4f5-54eba04880b6	completed	completed	2025-10-24 21:11:10.942018+00	2025-10-24 23:37:10.26782+00	\N	0	a51c31f6-180e-4716-a38f-81ebb2efc34c
6bda1644-7e6a-481d-b842-e17b1257a37b	99999c80-03f3-4958-8d92-1893cf3bffba	completed	completed	2025-10-24 21:11:10.067496+00	2025-10-25 00:23:11.031834+00	\N	0	b5d3e4fd-2e36-4235-b02d-cf6462b6c63f
3600798c-2750-47fa-a8d3-b724f2801a10	405ae69b-897d-40c2-8833-cb6e5f866fdd	completed	completed	2025-10-24 21:11:10.041511+00	2025-10-25 00:23:12.118322+00	\N	0	c9ae11a4-f57f-48d1-b877-b34199632249
3f3ee67e-0c1e-4ef6-a79c-d233761bd377	36f50e2b-715b-41f0-bb8f-f02594414899	completed	completed	2025-10-24 21:11:10.916459+00	2025-10-24 23:37:12.78815+00	\N	0	c996a25d-cae4-4d61-af18-a070708bc31a
ed259c46-955e-4a83-8d64-c48078f31f11	db0e00be-e162-4ff8-9085-8ccdda2ee455	completed	completed	2025-10-24 21:11:10.016084+00	2025-10-25 00:23:12.854628+00	\N	0	2fba341e-cf72-4220-90d0-04dd145c2d90
ad2e399d-c018-4138-8b22-07f75bb322d4	76bb6f80-33f4-4bb1-928d-e21e2abbbf88	completed	completed	2025-10-24 21:11:10.78588+00	2025-10-24 23:37:25.683731+00	\N	0	0aeeb8f9-d542-4822-bd26-adb262d871b9
71e25089-0483-4846-a840-adf65a09bb8c	cb42b3d6-fcde-4973-9c87-5909b971687b	completed	completed	2025-10-24 21:11:09.990357+00	2025-10-25 00:23:16.541228+00	\N	0	a22ef412-3a80-46a1-8cf5-78055e018b83
ed14674a-0b40-4ab1-bb33-c5c8902cefe8	570b4caf-eb53-4556-ac4a-eb446a52170c	completed	completed	2025-10-24 21:11:10.630473+00	2025-10-24 23:37:41.364378+00	\N	0	3b3c6b9e-2232-425b-bdc3-c18273caf381
2a04765b-6b4c-4e80-8de8-3803de9a7cb3	219110b2-9e72-43ff-ac7a-94b088dddc62	completed	completed	2025-10-24 21:11:10.579772+00	2025-10-24 23:37:44.361893+00	\N	0	33751189-45a3-40ba-9ab3-693ca4b7672f
bcc9fdb8-2d5d-4e7c-8aea-6d20929ee313	f1d60b79-b25a-426e-a46d-8587c65cecbe	completed	completed	2025-10-24 21:11:10.528569+00	2025-10-24 23:37:52.080475+00	\N	0	13f4bf74-b036-49ee-bbf0-5fe4a1085365
06da8fd6-06b0-466f-9628-2df2c64b0d74	0c51ba82-d4fc-40bd-814d-ebdaca484b7d	completed	completed	2025-10-24 21:11:10.502559+00	2025-10-24 23:37:54.696589+00	\N	0	573d7a66-3614-4440-9a57-c014ddea6199
1b582a00-36ab-4f06-bcf3-a44404d4edf4	496e39fb-3567-4185-bec9-bbd2fb795a2c	completed	completed	2025-10-24 21:11:10.476209+00	2025-10-24 23:37:57.274689+00	\N	0	ddac1d3f-bb94-43a8-bcf9-53947a9c8a39
40442f0e-4292-41f7-9bac-21beb7fe6ae5	d951b65c-2f2f-498b-aa4e-771fe39b84f4	completed	completed	2025-10-24 21:11:10.310479+00	2025-10-24 23:38:11.388788+00	\N	0	4264c092-afe3-4b95-90dc-78e455130e6c
79e2ee77-fcb2-40c8-b584-c0c924ba620e	e576cc7b-6cc8-46c5-9956-311a8d4955d7	completed	completed	2025-10-24 21:11:12.61222+00	2025-10-24 23:40:41.273497+00	\N	0	0a5575ca-4468-4782-9bf1-f08bdaf89df8
5da6cdd8-1a8b-4740-800f-187aa4ab7721	53ba5b41-af7f-479b-bcae-0edced8b16f6	completed	completed	2025-10-24 21:11:12.741995+00	2025-10-24 23:40:28.336186+00	\N	0	91b4dd04-3b4b-4e43-af93-66f827741e17
16c73ccc-ca60-447c-a3a4-a1dfe762b949	996582f5-0a1b-4a70-882c-9da977ce1a75	completed	completed	2025-10-24 21:11:12.586876+00	2025-10-24 23:40:44.077664+00	\N	0	028fb40c-fafa-40d7-be36-5ecffed1eaa0
fabf76c6-913b-4062-96db-963a5f028c27	3e64acd9-1377-4a59-b488-b76ac99355c3	completed	completed	2025-10-24 21:11:12.638122+00	2025-10-24 23:40:38.599867+00	\N	0	2a38a079-f1d9-4ac3-8ba4-ae3228ec2635
ad488620-6054-4d0a-801b-c2a135505e9b	5bdb67f0-dfba-452b-84b6-522a65c6449f	completed	completed	2025-10-24 21:11:12.561236+00	2025-10-24 23:40:46.702688+00	\N	0	b393527f-0b11-4a98-b09c-fc7cb1df603d
6fe7558f-c213-4c32-b0dd-7852e7a8d417	8004b799-5c5d-47c9-950c-19d24d90de09	completed	completed	2025-10-24 21:11:12.535014+00	2025-10-24 23:40:49.347105+00	\N	0	d13bc65d-d32c-4d9b-bbe5-f28c191e5b2c
49ccc9c5-296d-4e8a-8bc3-c7fe40a6848d	94b19828-0461-4c1a-a9f4-984331766cd4	completed	completed	2025-10-24 21:11:12.328707+00	2025-10-24 23:41:23.691134+00	\N	0	847fba4e-0b1d-4f8c-8a4f-c30424411f82
f6eae2c6-93a6-48c8-8e06-a102154767bf	28b3ac4e-dde9-4ef7-8a28-369cffd7406d	completed	completed	2025-10-24 21:11:12.354094+00	2025-10-24 23:41:26.312737+00	\N	0	956ba412-34f0-4a0e-805f-0dd6dd97c377
5da78e6a-d066-4018-990a-9dc40c575d65	c6aa642e-55f0-4d88-adc2-579fd71b6c03	completed	completed	2025-10-24 21:11:12.303126+00	2025-10-24 23:41:28.979842+00	\N	0	da6db4e8-5092-4fd2-a5ae-1f60bcc5dfd1
9c6f0816-7c4a-46c0-b732-793dba353e4b	e4d35c34-c945-4de7-a974-f78728cf02b8	completed	completed	2025-10-24 21:11:12.218528+00	2025-10-24 23:41:37.175191+00	\N	0	fedafa7d-be7c-401b-a28e-62d53eaa6015
a59f10a5-2765-4654-b098-3d02d5b960d6	d0e00060-0613-4bf4-be57-76d28376ec3d	completed	completed	2025-10-24 21:11:11.992483+00	2025-10-24 23:41:52.727269+00	\N	0	9acde1f8-6c02-4e68-af7c-e97a169b197e
ec0f8265-f9e4-4c32-9610-69f073c8adbf	6bc9fd60-6593-4ccf-8212-10cbcd0d5908	completed	completed	2025-10-24 21:11:11.966369+00	2025-10-24 23:41:55.319449+00	\N	0	208f4efe-34a0-4456-b89f-921ea58a0da7
ab62ceeb-10e4-4e0c-8f68-6afbd8673c24	0541f67c-7468-4b30-a46d-4fcb410b9665	completed	completed	2025-10-24 21:11:11.888652+00	2025-10-24 23:42:00.525936+00	\N	0	cf6b902e-bec7-4e9f-9cc8-aa2cf7906f23
9bde291b-4f51-49a3-94f2-4d2772467caf	332e35bd-e36e-4016-9230-7a7f6b04578b	completed	completed	2025-10-24 21:11:11.488745+00	2025-10-24 23:58:30.100029+00	\N	0	7f3fbe8f-6e5a-43e7-8d6c-b0d211ee121e
eec67157-1d8d-428c-9aa4-c75d2afcd366	5df0098a-c8b6-4705-8f3a-f5eb03f91f10	completed	completed	2025-10-24 21:11:11.914204+00	2025-10-24 23:42:03.127707+00	\N	0	22666707-26ba-47ff-a3b8-ae96069e4664
112092de-f9a9-4906-b369-b770e434c448	091a7192-a296-43e0-a796-d50e8b65201c	completed	completed	2025-10-24 21:11:12.716232+00	2025-10-25 00:23:14.446293+00	\N	0	2fde6acb-4a43-4c7c-ace5-9c4afc23dcb0
b1dd854c-9b71-478d-957a-3a595d8e3edc	cd97cba1-3273-498b-91d2-8c31726944dc	completed	completed	2025-10-24 21:11:12.663922+00	2025-10-25 00:23:14.794815+00	\N	0	a0ff99aa-bab7-435a-a0d5-37a17d9f45c2
a5a2c8a9-2dd5-46c1-86f2-66183f2fe913	2b8ba8da-333e-43ec-8edc-1dc24f3f2be8	completed	completed	2025-10-24 21:11:12.509237+00	2025-10-25 00:23:15.164166+00	\N	0	350abe4b-97e7-4457-a454-dacaff358e8d
54e2707b-b428-4ffa-aaf6-26692d294663	d8814410-e216-4013-b563-c24828960804	completed	completed	2025-10-24 21:11:11.81029+00	2025-10-24 23:42:11.58666+00	\N	0	ee07cde9-118c-43c6-989b-4e09e4f1f4cf
b625de54-06db-4f1e-aab6-681d6f204d42	229299ee-e075-42e5-8919-883f1ab4ed93	completed	completed	2025-10-24 21:11:12.483646+00	2025-10-25 00:23:15.494528+00	\N	0	9a7acbca-f9d5-42fe-9414-c2c220817844
8eb956ad-3b38-4415-9758-e03b47cca672	d83ffa99-91c5-44d7-86b3-6fa6ec5ddd27	completed	completed	2025-10-24 21:11:11.78355+00	2025-10-24 23:42:14.148378+00	\N	0	4d90d090-0967-484c-aaad-41e080f2322b
df478a5a-3dc1-49b4-aac4-a877e28d2be3	fe618e8e-d1c4-45fe-a6e2-6e33ed9a2822	completed	completed	2025-10-24 21:11:12.458256+00	2025-10-25 00:23:15.885869+00	\N	0	3f8bc517-eee1-4d40-ab7d-1fd2d0d93667
6d884dd5-ae38-4e77-ae77-649d80e50a1e	0fcb33fe-8733-419b-8e16-b921125f5afc	completed	completed	2025-10-24 21:11:12.432302+00	2025-10-25 00:23:16.229996+00	\N	0	daddcf38-4a99-4a28-b1bd-a2dfab8f9096
f990787a-bb87-4d35-b71e-8919678fa6b7	b41f0246-cb2d-4e3e-ada2-f1e318a2be41	completed	completed	2025-10-24 21:11:11.75621+00	2025-10-24 23:42:16.582776+00	\N	0	ca9a354b-9594-40c3-a3a4-a341ec7a36be
b38efb9d-4973-4c88-bf61-5937f1328f1c	c746f80d-5d1c-49ff-b386-1434376a2700	completed	completed	2025-10-24 21:11:12.40611+00	2025-10-25 00:23:16.853721+00	\N	0	17ee75b3-22b8-44a7-821b-b4d0ba9b7994
b79601ba-ee21-448c-bda7-67048f5eb4f9	9a65ff54-449c-461b-9c04-5b3828828d8f	completed	completed	2025-10-24 21:11:11.696257+00	2025-10-24 23:42:21.69808+00	\N	0	a7a1ddf2-a93b-4f93-b43e-82d0f47807e0
b6613a53-40b6-4206-b031-8c03e1aaeeb5	d9196d0e-78a2-45a4-b55d-4da970cd9ab0	completed	completed	2025-10-24 21:11:12.379633+00	2025-10-25 00:23:17.255054+00	\N	0	e39bb4f1-05a7-4e26-884b-4247a9ff2159
7d0b3eb2-28f2-41e6-b130-bbaa3783e50a	8044ce8c-b85b-4fbc-a53b-d6cebde44d88	completed	completed	2025-10-24 21:11:12.276689+00	2025-10-25 00:23:17.730036+00	\N	0	eb799f1b-a31e-4c74-b1a5-b7764f93f6f0
4002cef5-e7e9-44e4-ad21-e623f256351b	a8731d4e-563e-4e87-b02c-cc2eda464984	completed	completed	2025-10-24 21:11:11.644078+00	2025-10-24 23:42:26.877652+00	\N	0	ef782aaf-2c39-4d99-81c2-4a68eadb97f1
37e07ca0-a912-449d-827b-fe2758a49005	a45bb205-30ee-4c47-839d-709841ab5b0f	completed	completed	2025-10-24 21:11:12.247091+00	2025-10-25 00:23:18.089713+00	\N	0	76649ce8-78ae-4b6b-9b96-f4763120e8be
012a1b99-4716-4f6e-8c52-2c37b2981147	05093057-8505-4c80-9a35-05d9cad81656	completed	completed	2025-10-24 21:11:12.192002+00	2025-10-25 00:23:18.331011+00	\N	0	0016f1e3-9515-4794-a9d2-9a130f379aa2
b76607bb-f2ff-48f5-99f6-2011b7e822a9	25785edc-bc0a-43f9-9346-19af6ea30afb	completed	completed	2025-10-24 21:11:12.14045+00	2025-10-25 00:23:19.299474+00	\N	0	eb4294ef-aeb4-46da-b4a7-3e7c71b8db64
3e2b6737-7962-4517-a18c-dd8371daafb6	ceb9baf8-4fc6-43ad-8d2f-05798d83f766	completed	completed	2025-10-24 21:11:12.11434+00	2025-10-25 00:23:19.602677+00	\N	0	1da468c4-ffe5-4c56-934d-ba740ba41781
df9d35b1-df0b-4634-a1c8-8ff05f6ef2bc	ebb2891d-0539-4b7d-b33f-d2c07fb83d67	completed	completed	2025-10-24 21:11:12.017798+00	2025-10-25 00:23:20.08102+00	\N	0	bdcb4623-3aee-45e7-97ad-5f4f00862d73
46c09c3f-ec34-446e-9da6-d99aff36935b	f129538f-3e99-49f9-8aa8-cf5b2f657681	completed	completed	2025-10-24 21:11:11.940978+00	2025-10-25 00:23:20.399048+00	\N	0	af65e456-e027-47cd-b751-09e02e243461
10828cf0-2d03-410b-98b8-ee39ab7bfecb	900275c8-2a01-4d50-b412-a34603fd3701	completed	completed	2025-10-24 21:11:11.86282+00	2025-10-25 00:23:20.757122+00	\N	0	1f2cc5c2-6670-4fc0-9d2f-2a630ce0ab96
dc72ac38-fa90-446f-9681-1fdb964a3231	9f8690fa-66f8-40d5-8b7c-297a66827dc4	completed	completed	2025-10-24 21:11:11.836302+00	2025-10-25 00:23:21.278194+00	\N	0	1415550b-a998-40a3-8839-a42f303baa2d
ae185f79-c3f9-41a9-a406-eee30e82c248	e452227e-78e4-43c5-8edf-26c0c86751f2	completed	completed	2025-10-24 21:11:11.726363+00	2025-10-25 00:23:21.65616+00	\N	0	1e51f5c6-8258-46d1-b84d-40d148ed9531
33bd66ab-b8ae-4d82-be90-b377a97b1b8a	c41d0fb3-3401-4d51-b0b2-8c6e89971f97	completed	completed	2025-10-24 21:11:11.670301+00	2025-10-25 00:23:21.992071+00	\N	0	91d746f3-620b-4bbd-a580-013d910d0c2a
f40be224-f954-42f1-86a4-a9d8970e5b94	74507603-c276-44e3-a963-51d4b5477541	completed	completed	2025-10-24 21:11:11.618385+00	2025-10-25 00:23:22.494162+00	\N	0	26155e09-4438-4a43-8579-5938724f4d4e
e1c20181-4acb-478a-ae52-4e73da11b9bc	d90f9f64-6783-4918-aace-338bc384a606	completed	completed	2025-10-24 21:11:11.592487+00	2025-10-25 00:23:22.829874+00	\N	0	e8998107-0f91-40ff-9168-3b3d60c9fcf1
8c22ec4b-9287-411c-aac2-a21ba7b0c4dc	416ff5cf-5710-498f-8f57-0b3aeec961e3	completed	completed	2025-10-24 21:11:11.541385+00	2025-10-25 00:23:23.34696+00	\N	0	75edfbda-1b78-4688-8a55-1ff8259d55e6
b49e48f2-787c-44e1-91fe-e29e7babb6d2	117e2284-c648-421f-8047-260347138e08	completed	completed	2025-10-24 21:11:11.515181+00	2025-10-25 00:23:23.81018+00	\N	0	aec83142-c468-4d42-a4fc-edd09adf35f7
9543c08d-c0ae-45c3-9825-9516afcc853a	71fdf77e-4cd7-4230-adf6-fd3274357e27	completed	completed	2025-10-24 21:11:11.461991+00	2025-10-25 00:23:24.110642+00	\N	0	4a79e705-c1f1-43c4-a930-a88b5e03a98a
a613771d-af95-48dd-b379-f57869083f43	cdc1c2a6-c3a5-48ed-b98a-6b3f155ec5dc	completed	completed	2025-10-24 21:11:11.435494+00	2025-10-25 00:23:24.437288+00	\N	0	33faf3ee-f120-41a5-a432-98661fe65d49
415f64d7-6f49-41ab-8364-324f84f5ecd0	01467111-5fc4-4442-b204-2f7ab230e98f	completed	completed	2025-10-24 21:11:11.407809+00	2025-10-25 00:23:24.834092+00	\N	0	3f1f8d4d-ebbf-49ca-9adf-f5e14cb21834
a66beef8-e5f1-43f8-a3cd-c209ed4623bc	bed45bd0-1205-47f3-8201-35611b253f10	completed	completed	2025-10-24 21:11:11.380065+00	2025-10-25 00:23:25.175309+00	\N	0	58d820e3-cd7a-4a02-9542-266eba92d63c
ac9f7986-d33d-48db-8e79-9d7b59180adb	703c6d71-f286-457a-b04a-808096b8b117	completed	completed	2025-10-24 21:11:14.075302+00	2025-10-25 00:23:06.933111+00	\N	0	ec8ee185-6190-44c3-94b1-44ecf0f454a2
fc3244af-6689-43b4-bc76-8b706b8011ab	3bed169c-e381-4430-a059-fb3ecf906d6f	completed	completed	2025-10-24 21:11:13.990726+00	2025-10-25 00:23:07.749937+00	\N	0	7fbd2fb9-c3a1-4b34-bd28-92d8399d0624
223dac8c-b94f-47c5-b970-2531ba32c44b	b948f580-4833-49ff-9d4f-e3b11700d861	completed	completed	2025-10-24 21:11:14.047323+00	2025-10-24 23:38:16.232015+00	\N	0	a5190d6d-77ce-4e6b-bb2f-96e9718dfdf5
7284db34-a928-4f13-bfcd-889ac3622c83	498a9801-d549-4d1e-bc9e-83731f1752e9	completed	completed	2025-10-24 21:11:13.712349+00	2025-10-24 23:38:42.38181+00	\N	0	2e3aecde-84f9-4025-b8ab-3bae8a824989
e95eaaa9-c96b-455c-a78c-e37f87f1ad8b	b357d25f-902c-43c5-ba04-49d2fe276da7	completed	completed	2025-10-24 21:11:12.770438+00	2025-10-24 23:41:08.220618+00	\N	0	3740df33-b9e0-400e-a9e2-3ab728e691e9
cf243bd3-bf8a-4a5d-82b8-fbdb8a89cf51	65a7354c-e521-4dea-9176-e2ce3b5f8fc9	completed	completed	2025-10-24 21:11:13.852999+00	2025-10-25 00:23:08.88755+00	\N	0	09a0aa69-8970-4b6b-8fc6-3449962c2e7e
386c3a85-0962-436a-a84a-97ede0f9302e	c88806f2-81d0-49bc-a300-6158d371e526	completed	completed	2025-10-24 21:11:13.824903+00	2025-10-25 00:23:09.542883+00	\N	0	efac0616-1631-4b4e-b7df-a84d4651182f
ca7babc7-7743-48b5-9c6f-5f568dbdc107	38fb4a0b-8d1e-4708-b6f3-cd8219cd6969	completed	completed	2025-10-24 21:11:13.962787+00	2025-10-24 23:38:24.341905+00	\N	0	e79733e3-df80-41e4-b626-cf5da6193e9a
4b9fa2b5-e328-4333-9b02-2eac63cfff15	d8a50bd8-d048-4a3b-b64e-f23f64231691	completed	completed	2025-10-24 21:11:12.822623+00	2025-10-24 23:41:10.704923+00	\N	0	53654fb7-4df0-4da9-891e-713c0525ab18
0ce088f8-a024-41bf-8dc6-d93d21a76f0a	57bdf053-7983-4e0f-9502-8dac2bb7b76a	completed	completed	2025-10-24 21:11:13.296048+00	2025-10-25 00:23:09.91397+00	\N	0	61effa7d-2396-467e-b63f-d79cfa2c6075
a41a52ad-6639-47f3-a088-9f8d5f0fb0aa	7025fe0a-611a-4dbc-b56a-d8e2af88103b	completed	completed	2025-10-24 21:11:13.582874+00	2025-10-24 23:38:48.029445+00	\N	0	2f39e863-da10-485a-80a0-a2bc3cd613e6
8ddc9b18-2b45-4ed2-b50e-063d986afa6f	e025dbfb-a5b8-400d-a518-cd4dc66ff4d8	completed	completed	2025-10-24 21:11:13.269406+00	2025-10-25 00:23:11.458033+00	\N	0	cab86a2d-d8e9-4650-99e0-76786db20fdd
b88fe4de-6555-4cb8-96db-bcaeb45ba14d	1fbb4f6b-5bfc-4669-8897-481cd329e9b4	completed	completed	2025-10-24 21:11:12.849248+00	2025-10-24 23:41:13.256175+00	\N	0	297f7af0-71f4-4c9b-ae82-127ef37d24bc
17ef2ad9-0374-4f54-8f9d-437a4f3fc0fc	72be8a5e-75be-4efb-805b-1f12fc38ede8	completed	completed	2025-10-24 21:11:13.937283+00	2025-10-24 23:38:26.978162+00	\N	0	c26303e6-33e7-4855-90b5-f102bb742e8b
9e0cb11d-0d96-4fbf-b127-c7cbbf0b560d	3ee20fa0-69ce-4c3e-8e2c-1f11d4462305	completed	completed	2025-10-24 21:11:13.608642+00	2025-10-24 23:38:50.682705+00	\N	0	f7a5c42b-5e4c-497d-a95a-2d37a3aa4e62
de6427e2-a7d6-4aef-a0ef-ae0b34a640bc	6045f651-d186-442c-b50f-5f151f35eafa	completed	completed	2025-10-24 21:11:13.239586+00	2025-10-25 00:23:12.49716+00	\N	0	fcf5604c-83f1-498f-9a22-44bd8ff30989
338f411c-3275-44bd-bec2-e51dc98bd6ba	6f06618f-92d2-4fa2-b3cf-8bc6fa2636b4	completed	completed	2025-10-24 21:11:13.212108+00	2025-10-25 00:23:13.317111+00	\N	0	73a8c005-dfa2-44c0-acc8-04f46c367413
7af29aee-30aa-4b28-9393-8a2845e1a1ac	9f813f17-3a79-43c8-9830-5f7be9637f9d	completed	completed	2025-10-24 21:11:12.797105+00	2025-10-24 23:41:15.843595+00	\N	0	20f5a986-0fd2-4704-bf38-a28463e88295
a51bb37c-4b14-4cab-b19d-02f71dd58f30	6c25abb6-eea5-40c3-b08a-9fc93b28174b	completed	completed	2025-10-24 21:11:13.740402+00	2025-10-24 23:38:53.275507+00	\N	0	b33ce38f-ed2d-4662-aa5c-0a0f8752c7be
1b0e5faa-e618-42c8-8f69-b8436768423a	f322adad-b783-4263-abcf-c60df661519d	completed	completed	2025-10-24 21:11:13.911149+00	2025-10-24 23:38:29.802151+00	\N	0	ff6a3543-bf55-4c43-a3a5-55aedc360cb3
9004da41-e1d2-4e1f-bd9d-9915e267c0b0	b39f0551-00d4-4f39-b233-4f1d76496021	completed	completed	2025-10-24 21:11:13.134407+00	2025-10-25 00:23:13.800433+00	\N	0	59e53e72-0efb-43ab-9e2b-1c8d73cda97a
9f677cd3-84a5-482d-b370-1c4417c99f7f	577cc7ba-3659-4cba-8382-50b84246d8b3	completed	completed	2025-10-24 21:11:13.108713+00	2025-10-25 00:23:14.051875+00	\N	0	1201e0be-d649-49e8-9267-7b7bf5ee9e06
0ca3c8e8-8df8-44cd-998b-c72cc0ff8f28	d46f8531-3ba2-444b-b741-4c25565d41db	completed	completed	2025-10-24 21:11:13.884848+00	2025-10-24 23:38:31.96597+00	\N	0	d640d241-b970-49c8-bffa-8316d36771a3
98bb4a4d-480d-43f3-a0c2-f025e7b29012	e086a2f9-d520-4252-b1aa-a8018c1fac0b	completed	completed	2025-10-24 21:11:13.660236+00	2025-10-24 23:38:55.802322+00	\N	0	644f1a41-a728-4a09-bd21-7cd8b81b59cc
d9002702-a311-4b51-9758-6e343a6e3f3d	f935f35d-83db-4e71-9044-fc341b16d5e0	completed	completed	2025-10-24 21:11:13.79322+00	2025-10-24 23:38:37.7125+00	\N	0	aba33253-d0c3-461e-9c1a-fda553db8881
dd1db0ec-1d76-4517-9cd6-e023217834bf	a60d0d10-8384-4732-874e-67544bc026d3	completed	completed	2025-10-24 21:11:13.686446+00	2025-10-24 23:38:39.77103+00	\N	0	7017fc0a-5323-42f2-b374-4a3c7cd0f72d
bb4ca958-1ef3-4d6a-b0a7-eec3fae6398d	c0d8e436-dbd7-468a-9708-64644f17fc4d	completed	completed	2025-10-24 21:11:13.634745+00	2025-10-24 23:38:58.391453+00	\N	0	4196ba25-bbae-4cd0-b2fb-64b4a48584b2
7a53bb90-be2c-4abc-b9df-8da417c26591	b2cd4549-1416-4811-8846-ca21511e35b7	completed	completed	2025-10-24 21:11:13.766338+00	2025-10-24 23:39:01.012416+00	\N	0	be70f432-ce42-4ddb-88ec-f121cc072a02
576430b1-c9e6-4216-ab6b-813b461a5882	12492f4c-0f46-4236-8f7d-9948b5ab82ae	completed	completed	2025-10-24 21:11:13.427324+00	2025-10-24 23:39:03.645338+00	\N	0	831406ee-9e16-4f39-8369-a22b3fdbbf18
b5aed80e-30e4-4e26-91a5-f25f1315ca08	082a9d4f-8e0a-49c7-bd7e-14f216f65af7	completed	completed	2025-10-24 21:11:13.453468+00	2025-10-24 23:39:06.206984+00	\N	0	9b8bb25c-4829-4d9c-9fc5-1fd355fe3673
6fefc865-c20c-4fe3-9c77-4312606cc62a	9a5a3f88-26c4-485c-b3da-1697b36b77dd	completed	completed	2025-10-24 21:11:13.506041+00	2025-10-24 23:39:08.805959+00	\N	0	6f5184c0-2ca6-446f-9085-003b9b5ab43a
b2e32f76-b9ab-40a7-ba4e-2d8163060b9a	4fff5e62-98cc-4679-9346-3454b3aef0fd	completed	completed	2025-10-24 21:11:13.532083+00	2025-10-24 23:39:11.399597+00	\N	0	fe8910a8-6182-44a2-b9aa-a70def2a8ebc
34edbcbc-d7a8-4d77-be54-c7ab4fc63071	d2de0cf5-1570-4d6b-9bcd-24a6fa1a0aea	completed	completed	2025-10-24 21:11:13.557245+00	2025-10-24 23:39:14.128653+00	\N	0	3a382b6d-4fd3-48fb-a66d-5d8a459c192c
3201ad4f-87c9-4e37-b217-c82180d7897e	3684f840-9802-474f-b08e-75121449f390	completed	completed	2025-10-24 21:11:13.349566+00	2025-10-24 23:39:19.748246+00	\N	0	2407a780-4bf9-47b8-82bb-5a9cf9978186
97821e7e-0050-48ac-a46a-1c19faa34280	3c3dcb8d-c188-4ff4-b3f4-10f0e6133994	completed	completed	2025-10-24 21:11:13.323424+00	2025-10-24 23:39:22.451398+00	\N	0	1c2d42cf-bc9e-40a0-8742-45428af6fc0e
c2a51a4f-a641-4013-82c9-764860fe21dd	80c32c96-9d55-43d2-8641-a772b32fbfa6	completed	completed	2025-10-24 21:11:13.375485+00	2025-10-24 23:39:24.877595+00	\N	0	98d0190f-5574-4faf-9857-5af58ffe2df2
eea23910-60ee-487c-a139-c299bef11f9c	09e3d9a0-06f7-471a-9271-7942a72935ed	completed	completed	2025-10-24 21:11:13.401607+00	2025-10-24 23:39:27.40053+00	\N	0	a8be848c-296d-477d-8c73-1a0947fd30b6
f21f9ebd-5cf9-417e-883a-3241fb6e37d9	2db5b6e3-1653-41e3-b285-0693d722560b	completed	completed	2025-10-24 21:11:13.186057+00	2025-10-24 23:39:53.398642+00	\N	0	6ecd5b3b-872e-4cff-9578-2d7d7f593036
2e618353-407a-4462-86b3-89fcdb26c865	8bf6b6c3-5c5e-4c56-af7c-9b307fb40a5c	completed	completed	2025-10-24 21:11:13.159506+00	2025-10-24 23:39:56.353487+00	\N	0	19dc3115-d9e7-49fd-adbc-3f699c3e55ab
29f54399-56e3-449d-8692-c4bdc81624e4	2eefabc1-d6d7-4482-903b-b67e848e54f0	completed	completed	2025-10-24 21:11:12.926548+00	2025-10-24 23:40:04.176961+00	\N	0	eafc1ed1-4ee3-44c2-a9df-416c6afef7e9
9d3feafb-e9da-4f8b-9429-1f174cbe85a4	001b3d66-57f5-406f-a560-16536984cf71	completed	completed	2025-10-24 21:11:12.977978+00	2025-10-24 23:40:06.765243+00	\N	0	b07f4259-f336-4271-9fc6-ebb816770c3f
a71f1b37-d28a-41f6-9b67-fc97a68c3b3b	f8501657-d629-4720-ad0e-4335a2b48ab7	completed	completed	2025-10-24 21:11:13.02993+00	2025-10-24 23:40:09.371901+00	\N	0	3357a766-822f-4a7d-9cde-9582ce9918f6
47362b88-9532-44f0-89b7-7072a44679d5	e21fc184-8599-47c6-b094-dd13cb68773b	completed	completed	2025-10-24 21:11:13.056038+00	2025-10-24 23:40:11.995308+00	\N	0	8807bdc9-e543-4881-b9c9-54bb349b2e28
95b1396a-bd17-426c-afb2-9365ffc45690	1f1f511b-edba-4cff-9f53-ab7e0a8a7887	completed	completed	2025-10-24 21:11:12.899891+00	2025-10-24 23:40:14.590411+00	\N	0	249be8da-db1d-426d-9066-6c5161c308cd
8bf0e4e6-7004-4da0-8e9c-5e655771b65f	b39aac65-4589-4a58-9be2-11c7088e2ded	completed	completed	2025-10-24 21:11:12.874552+00	2025-10-24 23:40:17.179673+00	\N	0	70dcdc54-1c89-443e-a5ce-c11d1345e100
3185b911-cab2-4afd-a16d-6ad91a67a959	67e2eba2-2d3c-41f1-ade7-f0f4de3d0dcd	completed	completed	2025-10-24 21:11:13.081834+00	2025-10-24 23:40:20.074969+00	\N	0	178d2822-ae4b-4d47-a59f-e9eb318b5e2a
9f602e55-860b-43dd-b1d3-4a44650c97ee	bd850165-ea84-433b-bf48-c4cc7e605953	completed	completed	2025-10-24 21:11:13.003496+00	2025-10-24 23:40:22.745226+00	\N	0	ac0f57da-48b7-4f53-9322-fb8829890364
1cee31eb-1748-4d63-ad22-05ef1b8c698b	18695b08-436d-419c-b8ef-ca52773eb5db	completed	completed	2025-10-24 21:11:11.566592+00	2025-10-24 23:46:05.124662+00	\N	0	311ad93c-a6ee-4185-a7d4-2ab6a68f0ffa
62f48959-b557-4013-9cd7-5d608fba0919	f6f1cd74-0654-4807-a79d-32a46f657a2c	completed	completed	2025-10-24 21:11:11.268519+00	2025-10-25 00:19:35.124664+00	\N	0	c6340f95-5478-4c99-9b2d-032ed5b05337
70b1cdf1-f881-490f-9d46-e833bf33471c	509e4005-0afa-4586-b830-482d399abfb7	completed	completed	2025-10-24 21:11:14.016633+00	2025-10-24 23:38:13.644434+00	\N	0	40462369-a615-4c02-80ef-87458e4a81ff
17daf564-0bad-46e4-a386-b8ad809e62b0	8b6d891b-7479-497d-9d7b-00d91ba6583f	completed	completed	2025-10-24 21:11:13.479669+00	2025-10-24 23:39:16.798647+00	\N	0	40d31ead-e629-4fbc-ad76-8418e27ddf8c
01b4b1ab-0f6b-45d1-ab65-952393e44d40	6c33890c-9f7e-4bb3-95c4-776baf342154	completed	completed	2025-10-24 21:11:11.237411+00	2025-10-25 00:22:23.353303+00	\N	0	e92458b5-2dc2-4c1b-9922-0b083a434642
cf94a274-488b-4e5d-9b64-9d070fe57338	4551b82a-631b-496a-865d-94e942fe2e24	completed	completed	2025-10-24 21:11:10.682557+00	2025-10-25 00:23:03.861368+00	\N	0	2a068008-b9e1-41d8-88a8-2d92c27f31dc
cf9bdbc3-f414-429a-bad8-57630904ee1d	015bcec3-acc6-462b-90e8-8556c88d099c	completed	completed	2025-10-24 21:11:12.951976+00	2025-10-24 23:40:25.340021+00	\N	0	d24807a0-74e9-4514-a526-db50504dca90
1969d9a7-f485-4f64-ace2-191231134594	bd1115f9-d7d8-401c-8791-d71f816c34c8	completed	completed	2025-10-24 21:11:10.119927+00	2025-10-25 00:23:10.425465+00	\N	0	e81b7648-dd8c-4af4-84e5-de7a1d5046ea
7ef74b9e-5b3d-4fc6-b704-a5152a828c23	0cf306d4-0af6-4be7-8213-f46d2d48d83a	completed	completed	2025-10-24 21:11:12.689682+00	2025-10-24 23:40:33.58535+00	\N	0	a57d0554-865b-479e-bc5f-6de979e7de73
c3857f99-99f3-4340-a703-3a93f5198138	c4bd43a4-3b85-4fcd-9b47-f6593d7e5ffa	completed	completed	2025-10-24 21:11:12.166261+00	2025-10-25 00:23:18.877786+00	\N	0	07c72557-c0c4-48c3-9b4a-11aa3b52c28f
\.


--
-- TOC entry 3856 (class 0 OID 24789)
-- Dependencies: 246
-- Data for Name: invite_codes; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.invite_codes (id, code, tenant_id, role, uses_limit, uses_count, expires_at, active, created_at, created_by, last_used_at, last_used_by) FROM stdin;
\.


--
-- TOC entry 3869 (class 0 OID 25306)
-- Dependencies: 261
-- Data for Name: outbox_events; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.outbox_events (id, event_type, payload, aggregate_id, content_hash, idempotency_key, created_at, schema_version, trace_id, processed_at) FROM stdin;
\.


--
-- TOC entry 3858 (class 0 OID 24842)
-- Dependencies: 248
-- Data for Name: post_enrichment; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_enrichment (post_id, vision_labels, ocr_text, crawl_md, enrichment_provider, enriched_at, enrichment_latency_ms, metadata, updated_at, kind, tags) FROM stdin;
8abdbef5-0244-46eb-9237-c8dced704757	[]	\N		gigachat	2025-10-25 12:38:15.649123+00	0	{"kind": "enrichment", "tags": [], "urls": [], "model": "GigaChat:latest", "reason": "no_tags", "source": "enrichment_task", "version": "v1", "entities": [], "provider": "gigachat", "embedding": null, "channel_id": "123456789", "latency_ms": 0, "enriched_at": "2025-10-24T21:59:15.329706+00:00", "content_length": 244}	2025-10-26 17:57:10.787314+00	tags	{}
b7382947-e2d9-486d-90b5-bcc45bac10d1	[]	\N	\N	gigachat	2025-10-26 17:57:14.146659+00	0	{"model": "GigaChat:latest", "reason": "no_tags", "provider": "gigachat", "latency_ms": 0}	2025-10-26 17:57:14.146659+00	tags	{}
2ee389e4-72c8-460e-adfc-f53e02ec2c66	[]	\N	\N	gigachat	2025-10-26 17:57:14.216417+00	0	{"model": "GigaChat:latest", "reason": "no_tags", "provider": "gigachat", "latency_ms": 0}	2025-10-26 17:57:14.216417+00	tags	{}
bf9ab06e-26a4-45f7-ae3c-d75d66b2fc5d	[]	\N	\N	gigachat	2025-10-26 17:57:14.220235+00	0	{"model": "GigaChat:latest", "reason": "no_tags", "provider": "gigachat", "latency_ms": 0}	2025-10-26 17:57:14.220235+00	tags	{}
f935f35d-83db-4e71-9044-fc341b16d5e0	[]	\N	\N	manual_retag	2025-10-24 23:38:37.69753+00	238	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:37.697405+00:00", "content_length": 0}	2025-10-25 07:12:34.5213+00	tags	{новости,экономика,бизнес}
2eefabc1-d6d7-4482-903b-b67e848e54f0	[]	\N	\N	manual_retag	2025-10-24 23:40:03.84933+00	238	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:04.164728+00:00", "content_length": 0}	2025-10-25 07:12:34.5213+00	tags	{новости,экономика,бизнес}
18695b08-436d-419c-b8ef-ca52773eb5db	[]	\N	\N	manual_retag	2025-10-24 23:42:33.332939+00	0	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:46:05.108919+00:00", "content_length": 0}	2025-10-25 07:12:34.5213+00	tags	{новости,экономика,бизнес}
85aa4570-cb9e-4520-ba8e-be064d060b7f	[]	\N	\N	manual_retag	2025-10-24 23:37:02.919672+00	259	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:04.565496+00:00", "content_length": 0}	2025-10-25 07:12:34.5213+00	tags	{новости,экономика,бизнес}
570b04fc-e265-4793-b4f5-54eba04880b6	[]	\N	\N	manual_retag	2025-10-24 23:37:07.752321+00	259	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:10.243096+00:00", "content_length": 0}	2025-10-25 07:12:34.5213+00	tags	{новости,экономика,бизнес}
c385d8eb-4ec0-4003-b42e-d5c571cb8080	[]	\N	\N	gigachat	2025-10-24 23:36:35.74889+00	505	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:11:54.389658+00:00", "content_length": 39}	2025-10-25 07:04:38.197104+00	tags	{грок4}
b7945933-58d8-4e2a-98da-803f7d08bf9f	[]	\N	\N	gigachat	2025-10-24 23:19:56.002395+00	0	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:20:11.091067+00:00", "content_length": 0}	2025-10-25 07:04:38.197104+00	tags	{}
f6f1cd74-0654-4807-a79d-32a46f657a2c	[]	\N	\N	gigachat	2025-10-25 00:19:35.111227+00	304	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:19:35.111096+00:00", "content_length": 455}	2025-10-25 07:04:38.197104+00	tags	{kimik2,opensourcemodel,rlforllms,moonshotai}
cdc1c2a6-c3a5-48ed-b98a-6b3f155ec5dc	[]	\N	\N	gigachat	2025-10-24 23:42:47.750119+00	276	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:24.424870+00:00", "content_length": 315}	2025-10-25 07:04:38.197104+00	tags	{госдолгсша,трамп,предвыборнаякампания}
01467111-5fc4-4442-b204-2f7ab230e98f	[]	\N	\N	gigachat	2025-10-24 23:48:57.295247+00	343	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:24.821078+00:00", "content_length": 577}	2025-10-25 07:04:38.197104+00	tags	{warnerbros,harrypotter,dccomics,netflix,apple,amazon,paramount}
332e35bd-e36e-4016-9230-7a7f6b04578b	[]	\N	\N	gigachat	2025-10-24 23:42:40.43949+00	579	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:58:30.087319+00:00", "content_length": 161}	2025-10-25 07:04:38.197104+00	tags	{"анализ текста","классификация контента","теги для текста"}
3b5295c7-40b0-4bbd-98d0-028a6321bf3c	[]	\N	\N	gigachat	2025-10-25 00:16:55.514001+00	324	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:16:55.513856+00:00", "content_length": 527}	2025-10-25 07:04:38.197104+00	tags	{диффузионки,llm,интерпретируемость,latentreasoning}
6c33890c-9f7e-4bb3-95c4-776baf342154	[]	\N	\N	gigachat	2025-10-25 00:22:23.340316+00	365	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:22:23.340200+00:00", "content_length": 172}	2025-10-25 07:04:38.197104+00	tags	{pmpp,gpu,программирование,параллельныевычисления}
71fdf77e-4cd7-4230-adf6-fd3274357e27	[]	\N	\N	gigachat	2025-10-24 23:42:45.100573+00	233	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:24.098417+00:00", "content_length": 107}	2025-10-25 07:04:38.197104+00	tags	{прокуратура,аязшабутдинов,мошенничество}
bed45bd0-1205-47f3-8201-35611b253f10	[]	\N	\N	gigachat	2025-10-24 23:42:52.665285+00	288	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:25.161949+00:00", "content_length": 47}	2025-10-25 07:04:38.197104+00	tags	{трамп,помилование,binance,чанпэнчжао}
f7080db0-604c-4659-9803-28ed87f08110	[]	\N	\N	gigachat	2025-10-25 00:22:59.465387+00	365	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:22:59.465300+00:00", "content_length": 308}	2025-10-25 07:04:38.197104+00	tags	{kimik2,deepseekv3r1,мультихед,эксперты,эмоциональныйинтеллект,eqbench}
a9aeb5a9-0971-4cd1-92ff-b35854c8e9e7	[]	\N	\N	gigachat	2025-10-25 00:22:59.777906+00	255	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:22:59.777789+00:00", "content_length": 216}	2025-10-25 07:04:38.197104+00	tags	{gguf,kimi-k2,instruct-gguf}
ed00d5ca-920b-4153-8827-3897169b8733	[]	\N	\N	gigachat	2025-10-25 00:23:00.079901+00	243	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:00.079795+00:00", "content_length": 171}	2025-10-25 07:04:38.197104+00	tags	{openai,впечатления,"запуск кода"}
f1af80cb-727b-4c0b-b9d1-e109c6bee007	[]	\N	\N	gigachat	2025-10-25 00:23:00.392687+00	214	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:00.392571+00:00", "content_length": 73}	2025-10-25 07:04:38.197104+00	tags	{понял,статус,xcom}
ce76c62e-dd13-470b-8c34-569f2cb4ffc2	[]	\N	\N	gigachat	2025-10-25 00:23:00.862186+00	312	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:00.862081+00:00", "content_length": 415}	2025-10-25 07:04:38.197104+00	tags	{goeldlproverv2,leancompiler,llmpruver,syntheticproofs}
26c40906-8f94-4fbc-afc7-754009231bc1	[]	\N	\N	gigachat	2025-10-25 00:23:01.396637+00	395	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:01.396035+00:00", "content_length": 2711}	2025-10-25 07:04:38.197104+00	tags	{машинноеобучение,математика,алгоритмы,графы,биоинформатика,mlrllearning}
1bf48881-0bae-4a74-8ca7-e14ee0faee6c	[]	\N	\N	gigachat	2025-10-25 00:23:01.775057+00	306	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:01.774941+00:00", "content_length": 233}	2025-10-25 07:04:38.197104+00	tags	{llmrlлаборатория,cudaoptimization,contrastivereinforcementlearning}
808856a7-97f7-43ab-b2de-9f7c0fba740b	[]	\N	\N	gigachat	2025-10-25 00:23:02.075892+00	244	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:02.075781+00:00", "content_length": 180}	2025-10-25 07:04:38.197104+00	tags	{квенов,опенсурс,дипсикунит}
698e850b-f5af-477a-9480-217b28d5b940	[]	\N	\N	gigachat	2025-10-25 00:23:02.520542+00	378	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:02.520432+00:00", "content_length": 1086}	2025-10-25 07:04:38.197104+00	tags	{refalmachine,ruadaptqwen3-4b-instruct,post-training,sftдатасет,preference-tune}
b64de121-1358-45d7-aa72-1139f6d6398c	[]	\N	\N	gigachat	2025-10-25 00:23:02.953883+00	368	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:02.953503+00:00", "content_length": 593}	2025-10-25 07:04:38.197104+00	tags	{qvikhr-3-8b-instruction,qwen-3,математика-на-русском,физика-на-русском}
8c133af9-4cf2-487e-9310-1afdb8626deb	[]	\N	\N	gigachat	2025-10-25 00:23:03.235491+00	221	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:03.235372+00:00", "content_length": 109}	2025-10-25 07:04:38.197104+00	tags	{rlконференция,rljcsumassedu}
11ff9a4d-6de2-4ead-b0ef-a886b9b471e5	[]	\N	\N	gigachat	2025-10-25 00:23:03.555269+00	240	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:03.555143+00:00", "content_length": 185}	2025-10-25 07:04:38.197104+00	tags	{gpt-oss,gpt2,квены}
4551b82a-631b-496a-865d-94e942fe2e24	[]	\N	\N	gigachat	2025-10-25 00:23:03.848881+00	233	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:03.848273+00:00", "content_length": 75}	2025-10-25 07:04:38.197104+00	tags	{запустил,кста,воркает}
11fced41-c9d9-4729-8246-5530809970b5	[]	\N	\N	gigachat	2025-10-25 00:23:04.226751+00	322	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:04.226626+00:00", "content_length": 1147}	2025-10-25 07:04:38.197104+00	tags	{gregbrockman,openai,dota2,rlалгоритмы,ppo}
79972a07-941a-4c9a-9ef9-e4ee4a84b88e	[]	\N	\N	gigachat	2025-10-25 00:23:04.616775+00	308	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:04.616658+00:00", "content_length": 2687}	2025-10-25 07:04:38.197104+00	tags	{tts,датасет,речь,espeech,модели}
e00ececa-4cd6-4867-8885-479cc26a165b	[]	\N	\N	gigachat	2025-10-25 00:23:04.945089+00	273	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:04.944997+00:00", "content_length": 261}	2025-10-25 07:04:38.197104+00	tags	{агентноеобучение,llms,реинфорсментлр}
7e48d2a7-3563-4a5b-a2ae-66fd8094346c	[]	\N	\N	gigachat	2025-10-25 00:23:05.367975+00	359	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:05.367887+00:00", "content_length": 766}	2025-10-25 07:04:38.197104+00	tags	{vikhrborealis,ttssalt,whisper,qwen,asrmode,gigam}
d3014129-403a-42e8-8e58-242d55c2a1f3	[]	\N	\N	gigachat	2025-10-25 00:23:05.677591+00	250	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:05.677293+00:00", "content_length": 376}	2025-10-25 07:04:38.197104+00	tags	{квадрокоптер,нейроннаясеть,raptor}
d4063284-2a3b-4e41-8a9b-d9a07a79ef5f	[]	\N	\N	gigachat	2025-10-25 00:23:05.993877+00	252	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:05.993745+00:00", "content_length": 886}	2025-10-25 07:04:38.197104+00	tags	{qwen3guard,"модерация контента","генерация текста"}
2cfdde07-51e5-4012-a945-593fdc2ffe14	[]	\N	\N	gigachat	2025-10-25 00:23:06.314833+00	262	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:06.314680+00:00", "content_length": 706}	2025-10-25 07:04:38.197104+00	tags	{pokerbattle,llm-игроки,красивый-интерфейс}
270db607-b928-43a7-a6bb-5e87052f9be9	[]	\N	\N	gigachat	2025-10-25 00:23:06.65216+00	264	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:06.652043+00:00", "content_length": 524}	2025-10-25 07:04:38.197104+00	tags	{paper2agent,aiагент,научныестатьи}
703c6d71-f286-457a-b04a-808096b8b117	[]	\N	\N	gigachat	2025-10-25 00:23:06.917491+00	176	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:06.917409+00:00", "content_length": 66}	2025-10-25 07:04:38.197104+00	tags	{играем}
b01f3a6b-7971-43aa-8c55-04e70353895b	[]	\N	\N	gigachat	2025-10-25 00:23:07.425963+00	329	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:07.425855+00:00", "content_length": 179}	2025-10-25 07:04:38.197104+00	tags	{gpt-oss,reinforcementlearning,cuda-kernels}
3bed169c-e381-4430-a059-fb3ecf906d6f	[]	\N	\N	gigachat	2025-10-25 00:23:07.734306+00	246	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:07.734136+00:00", "content_length": 1956}	2025-10-25 07:04:38.197104+00	tags	{балет,танец,пантомима}
43a1b69c-38b3-4fc8-9504-96ceaf760a89	[]	\N	\N	gigachat	2025-10-25 00:23:08.088228+00	283	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:08.088123+00:00", "content_length": 69}	2025-10-25 07:04:38.197104+00	tags	{покупки-в-чатегпт,chatgpt,покупки}
1d6a676c-4478-4b1c-85e2-ff317a90d01e	[]	\N	\N	gigachat	2025-10-25 00:23:08.224812+00	0	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:08.224731+00:00", "content_length": 64}	2025-10-25 07:04:38.197104+00	tags	{}
65a7354c-e521-4dea-9176-e2ce3b5f8fc9	[]	\N	\N	gigachat	2025-10-25 00:23:08.873749+00	278	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:08.873618+00:00", "content_length": 145}	2025-10-25 07:04:38.197104+00	tags	{телохранитель,императрицаалександра,фигурафаберже}
0357b00a-3872-4d54-b95c-ef203f8c56d3	[]	\N	\N	gigachat	2025-10-25 00:23:09.256456+00	313	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:09.256350+00:00", "content_length": 756}	2025-10-25 07:04:38.197104+00	tags	{genie,deepmind,worldmodels,rl,ai,генеративныеокружения}
c88806f2-81d0-49bc-a300-6158d371e526	[]	\N	\N	gigachat	2025-10-25 00:23:09.526985+00	199	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:09.526873+00:00", "content_length": 80}	2025-10-25 07:04:38.197104+00	tags	{натюрморт,мазур}
57bdf053-7983-4e0f-9502-8dac2bb7b76a	[]	\N	\N	gigachat	2025-10-25 00:23:09.898169+00	228	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:09.898035+00:00", "content_length": 71}	2025-10-25 07:04:38.197104+00	tags	{гуф,оперныйпевец}
bd1115f9-d7d8-401c-8791-d71f816c34c8	[]	\N	\N	gigachat	2025-10-25 00:23:10.411532+00	370	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:10.411435+00:00", "content_length": 720}	2025-10-25 07:04:38.197104+00	tags	{симуляторатомногеоратора,промышленность,искусственныйинтеллект,генерацияданных,открытыйкод}
e9592709-064d-4839-883e-85f66000290a	[]	\N	\N	gigachat	2025-10-25 00:23:10.6991+00	225	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:10.699021+00:00", "content_length": 144}	2025-10-25 07:04:38.197104+00	tags	{документация,детализация,понимание}
99999c80-03f3-4958-8d92-1893cf3bffba	[]	\N	\N	gigachat	2025-10-25 00:23:11.013745+00	246	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:11.011928+00:00", "content_length": 156}	2025-10-25 07:04:38.197104+00	tags	{qwen,qwen3vl4b,инструкции}
e025dbfb-a5b8-400d-a518-cd4dc66ff4d8	[]	\N	\N	gigachat	2025-10-25 00:23:11.435846+00	212	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:11.435709+00:00", "content_length": 93}	2025-10-25 07:04:38.197104+00	tags	{опера-турандот}
405ae69b-897d-40c2-8833-cb6e5f866fdd	[]	\N	\N	gigachat	2025-10-25 00:23:12.08964+00	384	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:12.086270+00:00", "content_length": 1575}	2025-10-25 07:04:38.197104+00	tags	{cayleypy,машинноеобучение,графы,биоинформатика,алгебра,теориягрупп}
6045f651-d186-442c-b50f-5f151f35eafa	[]	\N	\N	gigachat	2025-10-25 00:23:12.47959+00	284	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:12.479465+00:00", "content_length": 475}	2025-10-25 07:04:38.197104+00	tags	{усталость,балетщелкунчик,новыйгод}
db0e00be-e162-4ff8-9085-8ccdda2ee455	[]	\N	\N	gigachat	2025-10-25 00:23:12.839951+00	246	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:12.839848+00:00", "content_length": 179}	2025-10-25 07:04:38.197104+00	tags	{llmk,реалтайм,торговля}
6f06618f-92d2-4fa2-b3cf-8bc6fa2636b4	[]	\N	\N	gigachat	2025-10-25 00:23:13.277583+00	340	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:13.277456+00:00", "content_length": 1820}	2025-10-25 07:04:38.197104+00	tags	{китайскоеискусство,символизмвискусстве,религиявкитайскомискусстве}
b39f0551-00d4-4f39-b233-4f1d76496021	[]	\N	\N	gigachat	2025-10-25 00:23:13.787261+00	402	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:13.787161+00:00", "content_length": 1300}	2025-10-25 07:04:38.197104+00	tags	{ограбление-лувра,драгоценности-наполеона,искусство-похищение,музей-лувр}
577cc7ba-3659-4cba-8382-50b84246d8b3	[]	\N	\N	gigachat	2025-10-25 00:23:14.036902+00	193	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-25T00:23:14.036776+00:00", "content_length": 81}	2025-10-25 07:04:38.197104+00	tags	{дэвидзинн}
091a7192-a296-43e0-a796-d50e8b65201c	[]	\N	\N	gigachat	2025-10-25 00:23:14.431328+00	337	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:14.431235+00:00", "content_length": 248}	2025-10-25 07:04:38.197104+00	tags	{доходысша,пошлиныbloomberg,финансовыйгод2025,рекордныедоходы}
cd97cba1-3273-498b-91d2-8c31726944dc	[]	\N	\N	gigachat	2025-10-25 00:23:14.780339+00	288	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:14.780214+00:00", "content_length": 261}	2025-10-25 07:04:38.197104+00	tags	{павелдуров,лувр,драгоценности,абу-даби}
2b8ba8da-333e-43ec-8edc-1dc24f3f2be8	[]	\N	\N	gigachat	2025-10-25 00:23:15.148864+00	309	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:15.148704+00:00", "content_length": 452}	2025-10-25 07:04:38.197104+00	tags	{япония,приложение,верификация,успешныелюди,tinder}
229299ee-e075-42e5-8919-883f1ab4ed93	[]	\N	\N	gigachat	2025-10-25 00:23:15.479064+00	266	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:15.478945+00:00", "content_length": 222}	2025-10-25 07:04:38.197104+00	tags	{volkswagen,финансовыйкрах,немецкийавтогигант}
fe618e8e-d1c4-45fe-a6e2-6e33ed9a2822	[]	\N	\N	gigachat	2025-10-25 00:23:15.870771+00	332	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:15.870668+00:00", "content_length": 158}	2025-10-25 07:04:38.197104+00	tags	{криптовалюта,внешняяторговля,силуанов,ведомство,цб}
0fcb33fe-8733-419b-8e16-b921125f5afc	[]	\N	\N	gigachat	2025-10-25 00:23:16.215573+00	288	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:16.215426+00:00", "content_length": 325}	2025-10-25 07:04:38.197104+00	tags	{tether,usdt,биткоин,эмиссия,инвесторы}
cb42b3d6-fcde-4973-9c87-5909b971687b	[]	\N	\N	gigachat	2025-10-25 00:23:16.517521+00	232	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-25T00:23:16.517396+00:00", "content_length": 91}	2025-10-25 07:04:38.197104+00	tags	{chatgpt}
c746f80d-5d1c-49ff-b386-1434376a2700	[]	\N	\N	gigachat	2025-10-25 00:23:16.836044+00	245	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:16.835964+00:00", "content_length": 214}	2025-10-25 07:04:38.197104+00	tags	{банкротства,снижение_ндс,ип}
d9196d0e-78a2-45a4-b55d-4da970cd9ab0	[]	\N	\N	gigachat	2025-10-25 00:23:17.234982+00	334	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:17.234670+00:00", "content_length": 581}	2025-10-25 07:04:38.197104+00	tags	{предпринимательство,вендинг,молодежныйбизнес,нью-йорк,мерч}
8044ce8c-b85b-4fbc-a53b-d6cebde44d88	[]	\N	\N	gigachat	2025-10-25 00:23:17.715779+00	414	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:17.715632+00:00", "content_length": 238}	2025-10-25 07:04:38.197104+00	tags	{chatgpt,atlas,ии-браузер,"режим агента",macos,подписчики,"chatgpt plus"}
a45bb205-30ee-4c47-839d-709841ab5b0f	[]	\N	\N	gigachat	2025-10-25 00:23:18.075112+00	300	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:18.075012+00:00", "content_length": 326}	2025-10-25 07:04:38.197104+00	tags	{налоговоезаконодательство,интернетплощадки,штрафыпродавцам,фнс}
05093057-8505-4c80-9a35-05d9cad81656	[]	\N	\N	gigachat	2025-10-25 00:23:18.315604+00	180	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:18.315487+00:00", "content_length": 44}	2025-10-25 07:04:38.197104+00	tags	{ростндс}
c4bd43a4-3b85-4fcd-9b47-f6593d7e5ffa	[]	\N	\N	gigachat	2025-10-25 00:23:18.863442+00	484	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:18.863355+00:00", "content_length": 521}	2025-10-25 07:04:38.197104+00	tags	{российскиекредиты,зарубежныекредиты,экономическиесвязи,развитиеинфраструктуры,закупкароссийскойпродукции,иран,вьетнам}
25785edc-bc0a-43f9-9346-19af6ea30afb	[]	\N	\N	gigachat	2025-10-25 00:23:19.285633+00	367	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:19.285536+00:00", "content_length": 579}	2025-10-25 07:04:38.197104+00	tags	{google,openai,atlas,капитализация,chatgpt,macos,ios}
ceb9baf8-4fc6-43ad-8d2f-05798d83f766	[]	\N	\N	gigachat	2025-10-25 00:23:19.588823+00	242	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:19.588696+00:00", "content_length": 293}	2025-10-25 07:04:38.197104+00	tags	{telegram,whatsapp,кибербезопасность,ограничения}
ebb2891d-0539-4b7d-b33f-d2c07fb83d67	[]	\N	\N	gigachat	2025-10-25 00:23:20.064417+00	395	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:20.064322+00:00", "content_length": 425}	2025-10-25 07:04:38.197104+00	tags	{nebius,аркадийволож,uber,avride,беспилотныетехнологии,yandexn.v,роботакси}
f129538f-3e99-49f9-8aa8-cf5b2f657681	[]	\N	\N	gigachat	2025-10-25 00:23:20.385329+00	263	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:20.385231+00:00", "content_length": 361}	2025-10-25 07:04:38.197104+00	tags	{thesims,boosty,виртуальнаяархитектура}
9f8690fa-66f8-40d5-8b7c-297a66827dc4	[]	\N	\N	gigachat	2025-10-25 00:23:21.263694+00	461	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:21.263606+00:00", "content_length": 1217}	2025-10-25 07:04:38.197104+00	tags	{налоговыепоправки,поднятиендс,уменьшениеусн,акцизынаалкоголь,отменальготпоимуществу,увеличениетарифвзносоваиткомпании}
e452227e-78e4-43c5-8edf-26c0c86751f2	[]	\N	\N	gigachat	2025-10-25 00:23:21.641538+00	289	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:21.641440+00:00", "content_length": 295}	2025-10-25 07:04:38.197104+00	tags	{ндс,российскоепо,минцифра,итотрасль}
74507603-c276-44e3-a963-51d4b5477541	[]	\N	\N	gigachat	2025-10-25 00:23:22.480936+00	436	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:22.480858+00:00", "content_length": 616}	2025-10-25 07:04:38.197104+00	tags	{исходitгигантовизкитая,пошлинысша,локдаунывкитае,переноспроизводствавазии,подорожаниэлектроники}
d90f9f64-6783-4918-aace-338bc384a606	[]	\N	\N	gigachat	2025-10-25 00:23:22.816001+00	274	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:22.815868+00:00", "content_length": 499}	2025-10-25 07:04:38.197104+00	tags	{apple,сэмсанг,ванкувер,струан}
416ff5cf-5710-498f-8f57-0b3aeec961e3	[]	\N	\N	gigachat	2025-10-24 23:42:35.695295+00	450	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:23.326682+00:00", "content_length": 347}	2025-10-25 07:04:38.197104+00	tags	{российскиебанки,мошенническаябазацб,продажакрипты,p2p-сервисы,реабилитацияроссиян,высокорискованнаяоперация}
117e2284-c648-421f-8047-260347138e08	[]	\N	\N	gigachat	2025-10-24 23:42:38.053087+00	396	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:23.792842+00:00", "content_length": 150}	2025-10-25 07:04:38.197104+00	tags	{"запрет поставок","европейский союз",россия,"трехколесные велосипеды",самокаты,"игрушечные педальные автомобили","кукольные коляски"}
36f50e2b-715b-41f0-bb8f-f02594414899	[]	\N	\N	gigachat	2025-10-24 23:37:12.463165+00	259	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:12.775492+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
76bb6f80-33f4-4bb1-928d-e21e2abbbf88	[]	\N	\N	gigachat	2025-10-24 23:37:24.880002+00	240	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:25.671455+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
570b4caf-eb53-4556-ac4a-eb446a52170c	[]	\N	\N	gigachat	2025-10-24 23:37:39.330193+00	235	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:41.352106+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
4ec78401-078d-46ec-a880-9468ed644d6f	[]	\N	\N	gigachat	2025-10-24 23:36:55.451432+00	234	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:36:56.417917+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
fcb2ab52-7038-41a7-a019-4c0e14944a47	[]	\N	\N	gigachat	2025-10-24 23:36:57.892953+00	242	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:36:59.094593+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
0c51ba82-d4fc-40bd-814d-ebdaca484b7d	[]	\N	\N	gigachat	2025-10-24 23:37:54.071451+00	239	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:54.683723+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
219110b2-9e72-43ff-ac7a-94b088dddc62	[]	\N	\N	gigachat	2025-10-24 23:37:41.795739+00	244	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:44.348855+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
f1d60b79-b25a-426e-a46d-8587c65cecbe	[]	\N	\N	gigachat	2025-10-24 23:37:51.68191+00	247	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:52.066804+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
496e39fb-3567-4185-bec9-bbd2fb795a2c	[]	\N	\N	gigachat	2025-10-24 23:37:56.492609+00	223	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:37:57.262170+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
d951b65c-2f2f-498b-aa4e-771fe39b84f4	[]	\N	\N	gigachat	2025-10-24 23:38:11.071204+00	250	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:38:11.375265+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
509e4005-0afa-4586-b830-482d399abfb7	[]	\N	\N	gigachat	2025-10-24 23:38:13.214527+00	218	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:13.627870+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
b948f580-4833-49ff-9d4f-e3b11700d861	[]	\N	\N	gigachat	2025-10-24 23:38:15.915584+00	232	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:16.209373+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
d46f8531-3ba2-444b-b741-4c25565d41db	[]	\N	\N	gigachat	2025-10-24 23:38:30.871731+00	246	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:31.952939+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
38fb4a0b-8d1e-4708-b6f3-cd8219cd6969	[]	\N	\N	gigachat	2025-10-24 23:38:23.526797+00	244	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:24.328927+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
72be8a5e-75be-4efb-805b-1f12fc38ede8	[]	\N	\N	gigachat	2025-10-24 23:38:25.908958+00	255	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:26.965928+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
f322adad-b783-4263-abcf-c60df661519d	[]	\N	\N	gigachat	2025-10-24 23:38:28.332627+00	247	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:29.784562+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
a60d0d10-8384-4732-874e-67544bc026d3	[]	\N	\N	gigachat	2025-10-24 23:38:38.098476+00	237	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:39.757583+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
498a9801-d549-4d1e-bc9e-83731f1752e9	[]	\N	\N	gigachat	2025-10-24 23:38:40.584902+00	233	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:42.365599+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
082a9d4f-8e0a-49c7-bd7e-14f216f65af7	[]	\N	\N	gigachat	2025-10-24 23:39:04.977+00	235	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:06.194555+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
7025fe0a-611a-4dbc-b56a-d8e2af88103b	[]	\N	\N	gigachat	2025-10-24 23:38:45.577258+00	237	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:48.017202+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
3ee20fa0-69ce-4c3e-8e2c-1f11d4462305	[]	\N	\N	gigachat	2025-10-24 23:38:50.367196+00	241	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:50.669872+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
6c25abb6-eea5-40c3-b08a-9fc93b28174b	[]	\N	\N	gigachat	2025-10-24 23:38:52.943088+00	250	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:53.262943+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
e086a2f9-d520-4252-b1aa-a8018c1fac0b	[]	\N	\N	gigachat	2025-10-24 23:38:55.317215+00	245	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:55.790261+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
c0d8e436-dbd7-468a-9708-64644f17fc4d	[]	\N	\N	gigachat	2025-10-24 23:38:57.757226+00	245	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:38:58.378850+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
b2cd4549-1416-4811-8846-ca21511e35b7	[]	\N	\N	gigachat	2025-10-24 23:39:00.121832+00	242	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:00.999805+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
12492f4c-0f46-4236-8f7d-9948b5ab82ae	[]	\N	\N	gigachat	2025-10-24 23:39:02.536904+00	249	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:03.632978+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
9a5a3f88-26c4-485c-b3da-1697b36b77dd	[]	\N	\N	gigachat	2025-10-24 23:39:07.357418+00	241	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:08.793403+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
4fff5e62-98cc-4679-9346-3454b3aef0fd	[]	\N	\N	gigachat	2025-10-24 23:39:09.809683+00	245	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:11.387294+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
d2de0cf5-1570-4d6b-9bcd-24a6fa1a0aea	[]	\N	\N	gigachat	2025-10-24 23:39:12.167198+00	251	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:14.110273+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
8b6d891b-7479-497d-9d7b-00d91ba6583f	[]	\N	\N	gigachat	2025-10-24 23:39:14.7838+00	252	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:16.786131+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
80c32c96-9d55-43d2-8641-a772b32fbfa6	[]	\N	\N	gigachat	2025-10-24 23:39:24.569286+00	237	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:24.864559+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
3684f840-9802-474f-b08e-75121449f390	[]	\N	\N	gigachat	2025-10-24 23:39:17.235324+00	241	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:19.731512+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
3c3dcb8d-c188-4ff4-b3f4-10f0e6133994	[]	\N	\N	gigachat	2025-10-24 23:39:22.119089+00	267	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:22.439175+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
09e3d9a0-06f7-471a-9271-7942a72935ed	[]	\N	\N	gigachat	2025-10-24 23:39:27.000093+00	248	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:27.387448+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
2db5b6e3-1653-41e3-b285-0693d722560b	[]	\N	\N	gigachat	2025-10-24 23:39:51.385035+00	246	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:53.385444+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
8bf6b6c3-5c5e-4c56-af7c-9b307fb40a5c	[]	\N	\N	gigachat	2025-10-24 23:39:53.811281+00	247	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:39:56.339431+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
001b3d66-57f5-406f-a560-16536984cf71	[]	\N	\N	gigachat	2025-10-24 23:40:06.241356+00	238	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:06.752188+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
f8501657-d629-4720-ad0e-4335a2b48ab7	[]	\N	\N	gigachat	2025-10-24 23:40:08.645566+00	237	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:09.359522+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
e21fc184-8599-47c6-b094-dd13cb68773b	[]	\N	\N	gigachat	2025-10-24 23:40:11.027794+00	244	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:11.982275+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
1f1f511b-edba-4cff-9f53-ab7e0a8a7887	[]	\N	\N	gigachat	2025-10-24 23:40:13.434914+00	243	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:14.577703+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
b39aac65-4589-4a58-9be2-11c7088e2ded	[]	\N	\N	gigachat	2025-10-24 23:40:15.930685+00	241	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:17.165551+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
67e2eba2-2d3c-41f1-ade7-f0f4de3d0dcd	[]	\N	\N	gigachat	2025-10-24 23:40:18.36731+00	257	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:20.056668+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
bd850165-ea84-433b-bf48-c4cc7e605953	[]	\N	\N	gigachat	2025-10-24 23:40:21.013974+00	254	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:22.732865+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
015bcec3-acc6-462b-90e8-8556c88d099c	[]	\N	\N	gigachat	2025-10-24 23:40:23.417751+00	238	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:40:25.324868+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
0cf306d4-0af6-4be7-8213-f46d2d48d83a	[]	\N	\N	gigachat	2025-10-24 23:40:33.267948+00	252	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:40:33.573294+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
53ba5b41-af7f-479b-bcae-0edced8b16f6	[]	\N	\N	gigachat	2025-10-24 23:40:25.860553+00	244	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:40:28.323254+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
3e64acd9-1377-4a59-b488-b76ac99355c3	[]	\N	\N	gigachat	2025-10-24 23:40:38.082671+00	231	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:40:38.586947+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
e576cc7b-6cc8-46c5-9956-311a8d4955d7	[]	\N	\N	gigachat	2025-10-24 23:40:40.645904+00	248	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:40:41.257021+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
996582f5-0a1b-4a70-882c-9da977ce1a75	[]	\N	\N	gigachat	2025-10-24 23:40:43.53663+00	248	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:40:44.065175+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
5bdb67f0-dfba-452b-84b6-522a65c6449f	[]	\N	\N	gigachat	2025-10-24 23:40:45.905675+00	245	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:40:46.689701+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
8004b799-5c5d-47c9-950c-19d24d90de09	[]	\N	\N	gigachat	2025-10-24 23:40:48.299239+00	277	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:40:49.334150+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
b357d25f-902c-43c5-ba04-49d2fe276da7	[]	\N	\N	gigachat	2025-10-24 23:41:07.916543+00	235	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:41:08.206905+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
5e9091d2-1f60-4ab0-aa52-6689a74b0a63	[]	\N	\N	gigachat	2025-10-24 23:41:02.854434+00	238	{"provider": "gigachat", "channel_id": "11c77f6b-2a54-4139-a20b-43d8a7950f34", "enriched_at": "2025-10-24T23:41:05.529218+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
d8a50bd8-d048-4a3b-b64e-f23f64231691	[]	\N	\N	gigachat	2025-10-24 23:41:10.373315+00	253	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:41:10.692516+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
1fbb4f6b-5bfc-4669-8897-481cd329e9b4	[]	\N	\N	gigachat	2025-10-24 23:41:12.823461+00	247	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:41:13.243325+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
9f813f17-3a79-43c8-9830-5f7be9637f9d	[]	\N	\N	gigachat	2025-10-24 23:41:15.219172+00	244	{"provider": "gigachat", "channel_id": "7f194a2a-5206-4348-b42d-1b3976ec7d43", "enriched_at": "2025-10-24T23:41:15.830559+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
94b19828-0461-4c1a-a9f4-984331766cd4	[]	\N	\N	gigachat	2025-10-24 23:41:22.409102+00	249	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:41:23.678278+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
28b3ac4e-dde9-4ef7-8a28-369cffd7406d	[]	\N	\N	gigachat	2025-10-24 23:41:24.857596+00	241	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:41:26.292110+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
c6aa642e-55f0-4d88-adc2-579fd71b6c03	[]	\N	\N	gigachat	2025-10-24 23:41:27.539942+00	240	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:41:28.966758+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
d83ffa99-91c5-44d7-86b3-6fa6ec5ddd27	[]	\N	\N	gigachat	2025-10-24 23:42:13.857676+00	233	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:42:14.137516+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
e4d35c34-c945-4de7-a974-f78728cf02b8	[]	\N	\N	gigachat	2025-10-24 23:41:34.722722+00	243	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:41:37.157672+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
d0e00060-0613-4bf4-be57-76d28376ec3d	[]	\N	\N	gigachat	2025-10-24 23:41:51.959219+00	254	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:41:52.712895+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
6bc9fd60-6593-4ccf-8212-10cbcd0d5908	[]	\N	\N	gigachat	2025-10-24 23:41:54.370339+00	245	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:41:55.307106+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
0541f67c-7468-4b30-a46d-4fcb410b9665	[]	\N	\N	gigachat	2025-10-24 23:41:59.158664+00	248	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:42:00.512954+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
5df0098a-c8b6-4705-8f3a-f5eb03f91f10	[]	\N	\N	gigachat	2025-10-24 23:42:01.583602+00	241	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:42:03.115758+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
d8814410-e216-4013-b563-c24828960804	[]	\N	\N	gigachat	2025-10-24 23:42:08.806895+00	265	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:42:11.568933+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
b41f0246-cb2d-4e3e-ada2-f1e318a2be41	[]	\N	\N	gigachat	2025-10-24 23:42:16.273142+00	239	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:42:16.570183+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
9a65ff54-449c-461b-9c04-5b3828828d8f	[]	\N	\N	gigachat	2025-10-24 23:42:21.076093+00	243	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:42:21.686132+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
a8731d4e-563e-4e87-b02c-cc2eda464984	[]	\N	\N	gigachat	2025-10-24 23:42:25.941585+00	240	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-24T23:42:26.865478+00:00", "content_length": 0}	2025-10-25 07:04:41.601827+00	tags	{}
900275c8-2a01-4d50-b412-a34603fd3701	[]	\N	\N	gigachat	2025-10-25 00:23:20.74257+00	300	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:20.742456+00:00", "content_length": 223}	2025-10-25 07:04:41.601827+00	tags	{}
c41d0fb3-3401-4d51-b0b2-8c6e89971f97	[]	\N	\N	gigachat	2025-10-25 00:23:21.97549+00	258	{"provider": "gigachat", "channel_id": "630bbcf5-a6ad-43ab-a18e-be91cb3fef1b", "enriched_at": "2025-10-25T00:23:21.975293+00:00", "content_length": 590}	2025-10-25 07:04:41.601827+00	tags	{}
\.


--
-- TOC entry 3867 (class 0 OID 25071)
-- Dependencies: 259
-- Data for Name: post_forwards; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_forwards (id, post_id, from_chat_id, from_message_id, from_chat_title, from_chat_username, forwarded_at, created_at) FROM stdin;
\.


--
-- TOC entry 3859 (class 0 OID 24859)
-- Dependencies: 249
-- Data for Name: post_media; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_media (id, post_id, media_type, media_url, thumbnail_url, file_size_bytes, width, height, duration_seconds, tg_file_id, tg_file_unique_id, sha256, created_at) FROM stdin;
45cd73f4-d3d6-48b4-9352-33b861ca5af4	8abdbef5-0244-46eb-9237-c8dced704757	photo	5388788620846040247	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
86d4f15a-5f98-4432-a7c3-7d73b77fa453	7e48d2a7-3563-4a5b-a2ae-66fd8094346c	photo	5341377730995944248	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
dcbac112-7ec9-45d2-9dc7-f78b7140401a	698e850b-f5af-477a-9480-217b28d5b940	photo	5192681092715508345	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
76e91276-e254-4ced-b723-10040e75d592	01467111-5fc4-4442-b204-2f7ab230e98f	photo	5462903925125544784	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
0f9bba5e-75a9-4f61-91d7-83e250626a71	d90f9f64-6783-4918-aace-338bc384a606	photo	5463286014006129840	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
f26fea12-8aa1-4821-8b7e-fae3192afa27	e452227e-78e4-43c5-8edf-26c0c86751f2	photo	5460972663016126831	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
444920db-d4dc-43c8-8c5a-d844889a79b4	25785edc-bc0a-43f9-9346-19af6ea30afb	photo	5461000103562179235	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
b44040df-18ed-4923-8abc-0197981bfc1c	d9196d0e-78a2-45a4-b55d-4da970cd9ab0	photo	5458809292284099790	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
5f43d997-7c6f-43cb-aafc-497329972920	bd1115f9-d7d8-401c-8791-d71f816c34c8	photo	5438285894139383451	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
1e2dfd4b-ef4f-4c6c-b3ec-fe0a37220316	b7945933-58d8-4e2a-98da-803f7d08bf9f	photo	5440724477785796064	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
aeb41937-5d0d-45c3-a7ec-c8f71dcb3bbf	bed45bd0-1205-47f3-8201-35611b253f10	photo	5462903925125544944	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
aab792a7-f8e4-4517-89d9-b1ec64b58246	b39f0551-00d4-4f39-b233-4f1d76496021	photo	5449708359663091491	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
c099d8bf-ce5e-4319-a1f9-6c78a600ad28	d4063284-2a3b-4e41-8a9b-d9a07a79ef5f	photo	5375428790064381009	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
2cfc1d3f-d941-4f00-a233-0a5e1803357c	d3014129-403a-42e8-8e58-242d55c2a1f3	photo	5936978137470581332	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
0031f873-3557-47ed-b60f-bf194cc18e5a	11fced41-c9d9-4729-8246-5530809970b5	photo	5837834074482391600	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4a048ca9-5be6-4c5c-ab7e-e606d98da3b2	11ff9a4d-6de2-4ead-b0ef-a886b9b471e5	photo	5817735792200628591	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
90fffab8-58d1-430c-bcb8-fce9ad3d6a1c	b64de121-1358-45d7-aa72-1139f6d6398c	photo	5231148305877169242	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
7ec1e51a-0647-4f35-9e38-c8ef5db7495c	85aa4570-cb9e-4520-ba8e-be064d060b7f	photo	5458797274965604308	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4f9a73c2-4fc9-4ffa-9492-da5957a536fa	4ec78401-078d-46ec-a880-9468ed644d6f	photo	5458797274965604309	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a4b07d12-7cff-4dc5-a47a-800770057ee2	fcb2ab52-7038-41a7-a019-4c0e14944a47	photo	5458797274965604306	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
5a3b1696-dbec-48fd-822b-95467325b7b9	f1af80cb-727b-4c0b-b9d1-e109c6bee007	photo	5458797274965604307	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
317312ed-727e-4783-adc7-6893eace9eac	a9aeb5a9-0971-4cd1-92ff-b35854c8e9e7	photo	6029490358636886491	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a3b578c0-0a5f-4df5-bb21-9de8b0b74f74	f7080db0-604c-4659-9803-28ed87f08110	photo	5449820441129643813	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
f340643d-766e-478d-a36d-af9984d8d3d7	6c33890c-9f7e-4bb3-95c4-776baf342154	photo	6029249870533081603	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
7b04798b-84dd-4894-a7f7-5f35f996d523	f6f1cd74-0654-4807-a79d-32a46f657a2c	photo	5442965686210133767	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
bb4db0ce-b3cf-4f50-b190-2dfaf36bb1f5	405ae69b-897d-40c2-8833-cb6e5f866fdd	photo	5388615215836429818	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
cc05fcb4-a7cb-49e2-885f-436652d409b3	0357b00a-3872-4d54-b95c-ef203f8c56d3	photo	5411248194542763534	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
19e959a5-0c09-4d13-8bb2-9745100f1900	270db607-b928-43a7-a6bb-5e87052f9be9	photo	5379793176916784027	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
e8bd1ddd-aee4-4b38-8c2a-7c9e9d636c85	2cfdde07-51e5-4012-a945-593fdc2ffe14	photo	5950600944804805246	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
e953b1ff-cb8d-4219-9507-e3ab98439826	79972a07-941a-4c9a-9ef9-e4ee4a84b88e	video	5289698025449492088	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
1474ec3b-ff0b-45d6-aeca-49096fbd130f	79972a07-941a-4c9a-9ef9-e4ee4a84b88e	document	5289698025449492088	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
b0423519-c336-4540-bbc2-e8dd3db5d050	ce76c62e-dd13-470b-8c34-569f2cb4ffc2	photo	5461009956217155848	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
f4c43fd8-b138-4ae9-a06a-a46e726a520c	3b5295c7-40b0-4bbd-98d0-028a6321bf3c	photo	6016102412999310160	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
638fa10b-ca7b-42a3-9183-ccdb3e025476	c41d0fb3-3401-4d51-b0b2-8c6e89971f97	photo	5462903925125543956	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4fa83fd3-4a65-41e7-a8e7-e8abfaff36fa	d0e00060-0613-4bf4-be57-76d28376ec3d	photo	5461000103562180325	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a48504be-cdd8-4716-a06c-39ebeb20e972	53ba5b41-af7f-479b-bcae-0edced8b16f6	photo	5456582618029094291	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a0f531cf-c91d-4ef7-bede-a235e8cc8abc	2b8ba8da-333e-43ec-8edc-1dc24f3f2be8	video	5456582617572867621	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
ca97cb7e-8897-48e0-88e7-c69361349bf9	2b8ba8da-333e-43ec-8edc-1dc24f3f2be8	document	5456582617572867621	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a3d6706c-1417-43a5-8fcd-7d74722a48bb	6f06618f-92d2-4fa2-b3cf-8bc6fa2636b4	photo	5449772560834231955	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
fad166a1-20b7-41ea-a77f-43d663de07d5	900275c8-2a01-4d50-b412-a34603fd3701	photo	5461000103562180441	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
eee9621c-60aa-4afe-9ab4-6c0a904e6c81	0541f67c-7468-4b30-a46d-4fcb410b9665	photo	5458589050656128849	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
b5928902-5f2f-4b97-9940-6697239301ce	5df0098a-c8b6-4705-8f3a-f5eb03f91f10	photo	5458589050656128847	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
52b60f37-ba04-44eb-bd39-c770f1515192	f129538f-3e99-49f9-8aa8-cf5b2f657681	video	5458589050199900558	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4060d5fc-b109-42e1-bf6e-724afd0129c5	f129538f-3e99-49f9-8aa8-cf5b2f657681	document	5458589050199900558	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
41267035-a9f2-493e-8a6e-795e92c9b95f	6bc9fd60-6593-4ccf-8212-10cbcd0d5908	photo	5461000103562180329	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
83ded101-92bb-4341-ae34-d355a4d4690c	ebb2891d-0539-4b7d-b33f-d2c07fb83d67	photo	5461000103562179981	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
c5953679-a73e-4b17-ab6e-923259e9aaac	577cc7ba-3659-4cba-8382-50b84246d8b3	photo	5456425667039197463	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
8a3be402-c0a8-41ed-9afd-978ebca7b5db	8044ce8c-b85b-4fbc-a53b-d6cebde44d88	video	5461000103105951089	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
b1de9639-66c2-45e0-b8d4-4c2255c88825	8044ce8c-b85b-4fbc-a53b-d6cebde44d88	document	5461000103105951089	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
6232e657-9888-4431-8d28-2017b9e83c03	28b3ac4e-dde9-4ef7-8a28-369cffd7406d	photo	5458809292284099791	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
f549d6bf-377b-4748-989a-9042f3659b00	229299ee-e075-42e5-8919-883f1ab4ed93	photo	5456582618029095208	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
38c1049d-addc-4015-a62d-adcd2c81cb72	996582f5-0a1b-4a70-882c-9da977ce1a75	photo	5456255285686567846	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
5a4d7386-a5d0-4936-9eec-00f43e976051	e576cc7b-6cc8-46c5-9956-311a8d4955d7	photo	5456255285686567845	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
3a692109-fe93-4f62-96f3-b2195b76a3d3	cd97cba1-3273-498b-91d2-8c31726944dc	photo	5456582618029094480	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
0ad5a414-de82-47ac-896b-16156299f97e	091a7192-a296-43e0-a796-d50e8b65201c	photo	5456582618029094370	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
cd20f6d0-2a00-4914-a841-de0795018f4e	b357d25f-902c-43c5-ba04-49d2fe276da7	photo	5458590669858799014	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
de7d56c4-edc6-4049-9333-63c6e16d2f65	9f813f17-3a79-43c8-9830-5f7be9637f9d	photo	5458590669858799013	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
8c609a24-51be-4084-8347-acf0d3f3805b	d8a50bd8-d048-4a3b-b64e-f23f64231691	photo	5458590669858799012	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
d213e1d9-b2f2-4cfa-84f9-7ec96ad94155	1fbb4f6b-5bfc-4669-8897-481cd329e9b4	photo	5458590669858799011	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
c2f4c5b3-58c3-4990-b44b-12e7f1b07938	b39aac65-4589-4a58-9be2-11c7088e2ded	photo	5456425667039197472	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
82575871-5e2b-49a8-93ed-f938e4f10d4b	1f1f511b-edba-4cff-9f53-ab7e0a8a7887	photo	5456425667039197471	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
d482bfc7-8e76-4242-b90c-d5ad07009314	2eefabc1-d6d7-4482-903b-b67e848e54f0	photo	5456425667039197470	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
e035567b-73d8-401e-954c-4146968bbdad	015bcec3-acc6-462b-90e8-8556c88d099c	photo	5456425667039197469	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
40512c42-1742-4a66-8633-e889ff957733	001b3d66-57f5-406f-a560-16536984cf71	photo	5456425667039197468	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
fc3c763f-6d89-4011-998f-1da6a36b62af	bd850165-ea84-433b-bf48-c4cc7e605953	photo	5456425667039197467	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4eedecc3-f535-4841-9c18-1993ac029ecc	f8501657-d629-4720-ad0e-4335a2b48ab7	photo	5456425667039197466	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
7a7d38cd-c3b6-44a7-bb84-01986d1ea5c7	e21fc184-8599-47c6-b094-dd13cb68773b	photo	5456425667039197465	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
c68d626d-67f8-47fb-9c47-4903ea53cc9a	e025dbfb-a5b8-400d-a518-cd4dc66ff4d8	photo	5869617498192720673	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
60433007-5a9f-4531-958e-b215747406da	57bdf053-7983-4e0f-9502-8dac2bb7b76a	video	5420371456242514923	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
68716d27-4a01-4d87-909d-4088254d82a7	57bdf053-7983-4e0f-9502-8dac2bb7b76a	document	5420371456242514923	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
18bea28f-e4a0-4956-9b09-3f60595501ac	3c3dcb8d-c188-4ff4-b3f4-10f0e6133994	photo	5413887211427987279	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
0ef4556d-c0e6-4e11-a922-550fbf919f9b	3684f840-9802-474f-b08e-75121449f390	photo	5413887211427987278	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
6b06d109-27a4-4638-a693-eacf35c41814	80c32c96-9d55-43d2-8641-a772b32fbfa6	photo	5413887211427987277	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
c0a977e5-234f-46eb-81f4-9d92ddbe0c6f	09e3d9a0-06f7-471a-9271-7942a72935ed	photo	5413887211427987276	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
b7625867-4933-47f1-be9a-1691d2c514bc	12492f4c-0f46-4236-8f7d-9948b5ab82ae	photo	5413887211427987275	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
1a2789eb-1d34-4205-b4cc-799792c6d984	082a9d4f-8e0a-49c7-bd7e-14f216f65af7	photo	5413887211427987274	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
2ec2cbcd-2dab-4acb-b458-224e32eaea66	8b6d891b-7479-497d-9d7b-00d91ba6583f	photo	5413887211427987273	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4bf8c799-faaa-4732-83a7-41e48727b151	9a5a3f88-26c4-485c-b3da-1697b36b77dd	photo	5413887211427987272	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
5a0b2615-f909-44f2-a3ae-998c1e4ce63a	4fff5e62-98cc-4679-9346-3454b3aef0fd	photo	5413887211427987271	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
5e8cc1ed-0e81-4950-bf9e-ba860a598303	d2de0cf5-1570-4d6b-9bcd-24a6fa1a0aea	photo	5413887211427987270	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
7da21c03-2241-4ae9-b191-a317be83e479	7025fe0a-611a-4dbc-b56a-d8e2af88103b	photo	5413887211427987269	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
62a4c104-9417-42c9-bcb7-cf830ac7219f	3ee20fa0-69ce-4c3e-8e2c-1f11d4462305	photo	5413887211427987268	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
6c5215ca-0c81-4140-b445-f6c1daef2757	c0d8e436-dbd7-468a-9708-64644f17fc4d	photo	5413887211427987267	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a800b382-bdcd-40ac-9534-216eb44b3d90	e086a2f9-d520-4252-b1aa-a8018c1fac0b	photo	5413887211427987266	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
1a7e629f-4213-45d6-b13d-0b3557c81d72	a60d0d10-8384-4732-874e-67544bc026d3	photo	5413887211427987265	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
1d5afada-ad3a-4c58-bb34-e349a0189dfd	498a9801-d549-4d1e-bc9e-83731f1752e9	photo	5413887211427987264	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
d4110e12-128c-4c14-a6a3-1389d8e6317d	6c25abb6-eea5-40c3-b08a-9fc93b28174b	photo	5413887211427987263	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
7f8d4d8f-9f04-4e71-9cb5-792f909d1c05	b2cd4549-1416-4811-8846-ca21511e35b7	photo	5413887211427987262	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
e7c788b6-fc02-4d36-8b41-df138f8b931a	f935f35d-83db-4e71-9044-fc341b16d5e0	photo	5413887211427987261	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4e6ac3fd-87d5-4eb5-9277-104684dd4343	c88806f2-81d0-49bc-a300-6158d371e526	photo	5413887211427987260	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
0bb8edc0-1a51-45f4-a209-23727e427cfb	cb42b3d6-fcde-4973-9c87-5909b971687b	photo	5458755781286560587	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
8a04b08a-ba52-409b-bc98-e5c9f47f0b8d	db0e00be-e162-4ff8-9085-8ccdda2ee455	photo	5449393139128335028	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
2b9a0665-d2e9-4799-80cd-8ebcdb02b6a4	99999c80-03f3-4958-8d92-1893cf3bffba	photo	6013898222898101179	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
94d648fb-0120-4efa-af7c-8114612334cf	e9592709-064d-4839-883e-85f66000290a	document	5435888434348592775	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
23c68a1a-63a1-44ae-a4b5-5896748bb8ae	1d6a676c-4478-4b1c-85e2-ff317a90d01e	photo	5835715328460569245	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a27c0a26-6b72-4730-8b43-79c55772b8b2	43a1b69c-38b3-4fc8-9504-96ceaf760a89	photo	5975254976206846895	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
ee8926f6-7621-49e4-9047-d294f1f20b08	b01f3a6b-7971-43aa-8c55-04e70353895b	photo	5971183501829060902	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
b804dc7e-84d9-4835-be67-f8d06749c746	67e2eba2-2d3c-41f1-ade7-f0f4de3d0dcd	photo	5456425667039197464	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
1cd9d3db-e979-4fa0-8997-c771d8fcd913	e00ececa-4cd6-4867-8885-479cc26a165b	photo	5321366086794473624	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
173f6cdc-9b32-4a6f-ab76-22864d56cbfa	219110b2-9e72-43ff-ac7a-94b088dddc62	video	5289698025449492073	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
fd750f7d-1064-4301-ac34-ce31f503ca1b	219110b2-9e72-43ff-ac7a-94b088dddc62	document	5289698025449492073	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
0bcd4f9a-2650-4572-bc6a-c3330adeb615	4551b82a-631b-496a-865d-94e942fe2e24	photo	6034048143575419678	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
a4b10194-05c1-4644-a688-e3b7a9b31d74	8c133af9-4cf2-487e-9310-1afdb8626deb	photo	5235802688921402340	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
383531f4-462e-4f1a-b14c-974c6fe4ec40	808856a7-97f7-43ab-b2de-9f7c0fba740b	photo	5474237437581587760	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
7d8d614b-7164-4d9f-a896-62fe7c9088cf	1bf48881-0bae-4a74-8ca7-e14ee0faee6c	photo	5472253398914037574	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
e23a9a8d-b09d-4b63-b5ab-2a1cb0ebd285	26c40906-8f94-4fbc-afc7-754009231bc1	photo	5447190971956721008	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
abdb75b0-6735-4ba7-8b91-310c2073fa2d	570b04fc-e265-4793-b4f5-54eba04880b6	photo	5461009956217155849	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
72737878-5e26-483f-9d00-8db39afd06ed	71fdf77e-4cd7-4230-adf6-fd3274357e27	photo	5462903925125544752	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
410cee09-3be4-4d51-b717-86b88ed27df5	18695b08-436d-419c-b8ef-ca52773eb5db	photo	5463286014006129841	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
5a3e793b-877f-4e0f-9986-ae3611d541da	05093057-8505-4c80-9a35-05d9cad81656	photo	5461000103562178959	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
4ffdca90-7d86-4b64-930d-a8b355eedece	c6aa642e-55f0-4d88-adc2-579fd71b6c03	photo	5458809292284099793	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
775201c3-6385-4616-aae3-fb851c260b08	94b19828-0461-4c1a-a9f4-984331766cd4	photo	5458809292284099792	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
976258d7-e4a1-44f9-94a7-373e6a172c01	5bdb67f0-dfba-452b-84b6-522a65c6449f	photo	5456582618029094736	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
741844dc-bc87-4b9e-be72-0624be159464	6045f651-d186-442c-b50f-5f151f35eafa	photo	6031817015435572933	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
9a7bddcc-0f1f-4b98-88e0-97251475d7f3	65a7354c-e521-4dea-9176-e2ce3b5f8fc9	photo	5408972102394050594	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
74156014-38b4-4a24-b321-db7e82ef6c8c	d46f8531-3ba2-444b-b741-4c25565d41db	photo	5975524579893949283	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
b8b9defc-36da-4b2b-8fed-4216eb0fb701	f322adad-b783-4263-abcf-c60df661519d	video	5393269911957044245	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
83382b41-4337-4678-9d37-bedbaf92842a	f322adad-b783-4263-abcf-c60df661519d	document	5393269911957044245	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
969d2c74-bb3c-4499-ac13-b59e963ad2be	72be8a5e-75be-4efb-805b-1f12fc38ede8	photo	5393269912413272197	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
f5e5d4ec-9790-4769-830d-1964032e3364	38fb4a0b-8d1e-4708-b6f3-cd8219cd6969	video	5993283401623274233	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
ab8cef65-3bb9-4e15-9219-5e9bc1176b8b	38fb4a0b-8d1e-4708-b6f3-cd8219cd6969	document	5993283401623274233	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
23d3c271-bc95-42ea-895d-848d5d7ba600	509e4005-0afa-4586-b830-482d399abfb7	photo	5379802471226014871	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
bb012557-d4c9-4303-b737-ce3df2d0e634	b948f580-4833-49ff-9d4f-e3b11700d861	photo	5379802471226014858	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
f0de9b3c-9589-4004-b616-9c6b97ed1cb9	703c6d71-f286-457a-b04a-808096b8b117	photo	5379802471226014859	\N	\N	\N	\N	\N	\N	\N	\N	2025-10-24 23:32:42.387244+00
\.


--
-- TOC entry 3866 (class 0 OID 25049)
-- Dependencies: 258
-- Data for Name: post_reactions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_reactions (id, post_id, reaction_type, reaction_value, user_tg_id, is_big, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 3868 (class 0 OID 25089)
-- Dependencies: 260
-- Data for Name: post_replies; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.post_replies (id, post_id, reply_to_post_id, reply_message_id, reply_chat_id, reply_author_tg_id, reply_author_username, reply_content, reply_posted_at, created_at) FROM stdin;
\.


--
-- TOC entry 3848 (class 0 OID 16432)
-- Dependencies: 235
-- Data for Name: posts; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.posts (id, channel_id, telegram_message_id, content, media_urls, created_at, is_processed, posted_at, url, has_media, yyyymm, views_count, forwards_count, reactions_count, replies_count, is_pinned, is_edited, edited_at, post_author, reply_to_message_id, reply_to_chat_id, via_bot_id, via_business_bot_id, is_silent, is_legacy, noforwards, invert_media, last_metrics_update, telegram_post_url) FROM stdin;
b7382947-e2d9-486d-90b5-bcc45bac10d1	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	999999	Тестовый пост для e2e пайплайна с валидным UUID	[]	2025-10-25 12:42:04.91853+00	f	2025-10-25 12:42:04.91853+00	\N	f	202510	0	0	0	0	f	f	\N	\N	\N	\N	\N	\N	f	f	f	f	2025-10-25 12:42:04.91853+00	https://t.me/@business_ru/999999
8abdbef5-0244-46eb-9237-c8dced704757	11c77f6b-2a54-4139-a20b-43d8a7950f34	1213	**Vistral-24B-Instruct**\n\n**Vistral** - это наша новая флагманская унимодальная LLM представляющая из себя улучшенную версию **Mistral-Small-3.2-24B-Instruct-2506** командой VikhrModels, адаптированную преимущественно для русского и английского языков. Удалён визуальный энкодер, убрана мультимодальность. Сохранена стандартная архитектура **MistralForCausalLM** без изменений в базовой структуре модели.\n\n🔗 **Карточка модели**: https://huggingface.co/Vikhrmodels/Vistral-24B-Instruct\n🔗 **GGUF (скоро)**: https://huggingface.co/Vikhrmodels/Vistral-24B-Instruct-GGUF\n⚖️ **Лицензия**: apache-2.0\n\n**Сайт**: https://vikhr.org\n**Донаты**: [Здесь](https://www.tbank.ru/cf/3W1Ko1rj8ah)\n\n👥 **Авторы**: @LakoMoorDev @nlpwanderer	["photo:5388788620846040247"]	2025-09-29 09:05:33+00	t	2025-09-29 09:05:33+00	\N	t	202509	1946	19	0	0	f	t	2025-09-29 09:44:13+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.498828+00	https://t.me/@AGI_and_RL/1213
2cfdde07-51e5-4012-a945-593fdc2ffe14	11c77f6b-2a54-4139-a20b-43d8a7950f34	1209	**Poker Battle**. Прогресс за третью, четвёртую и пятую недели.\n\n__Надеюсь ни у кого не осталось сомнений, что я не буду регулярно писать в канал ))__\n\nПо ощущениям проект готов процентов на 80%. Значит, остались последние 80%.\n\nЧто готово:\n* LLM-игроки\n* Заметки игроков друг на друга\n* Лог событий за столом\n* Статистика сессии\n* Поддержка нескольких столов\n\nОсталось сделать всё сложить в красивый интерфейс для просмотра.\n\nТем не менее я определился с датой старта — **27 октября**. Оставшийся месяц я потрачу на доделки, тесты и промо.\n\nСегодня сделал лендинг: https://www.pokerbattle.ai/\n\nЕсли у вас есть контакты людей из AI или покер related компаний, которые могли бы стать спонсорами, делитесь :)	["photo:5950600944804805246"]	2025-09-24 15:57:32+00	t	2025-09-24 15:57:32+00	https://www.pokerbattle.ai/	t	202509	2008	26	0	0	f	t	2025-09-24 16:19:11+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.507803+00	https://t.me/@AGI_and_RL/1209
7e48d2a7-3563-4a5b-a2ae-66fd8094346c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1206	Vikhr Borealis - первая русскоязычная открытая audio llm\n\nМы долго и не очень успешно развивали свой tts - Salt, от него исторически осталось довольно много данных и наработок, мы решили - чо бы не сварить asr + llm как модно?\n\nНу и сварили. Архитектурно - whisper + qwen, учили на 7к часов аудио только адаптер+llm, сейчас работает только в ASR режиме, позже возможно довезем инструктивный режим. Так же выйдет бенчмарк для русского asr, он пока в доработке. \nБлог так же выйдет, там будут небольшие аблейшены по данным\n\nМодель в данный момент бьет whisperы на русском и на части бенчей лучше чем gigam. \n\n[Модель](https://huggingface.co/Vikhrmodels/Borealis)\n[Сolab поиграться](https://colab.research.google.com/drive/1ac7apyGO24iAYMwg3DLcqLZRjo-w4QWf?usp=sharing)	["photo:5341377730995944248"]	2025-09-12 12:09:11+00	t	2025-09-12 12:09:11+00	\N	t	202509	2277	24	0	0	f	t	2025-09-12 12:09:57+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.51774+00	https://t.me/@AGI_and_RL/1206
cdc1c2a6-c3a5-48ed-b98a-6b3f155ec5dc	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24656	📈 Госдолг США превысил** $38 трлн. **Это исторический рекорд. Трамп неоднократно обещал погасить госдолг, если станет президентом. «Эта страна должна** $35 трлн,** но это может быстро сойти на нет», — говорил он в ходе предвыборной компании. Однако за время его президентства темпы роста госдолга только ускорились.	[]	2025-10-23 15:46:31+00	t	2025-10-23 15:46:31+00	\N	f	202510	6211	31	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.887792+00	https://t.me/@business_ru/24656
2ee389e4-72c8-460e-adfc-f53e02ec2c66	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	888888	Тестовый пост для e2e пайплайна с детальным логированием	[]	2025-10-25 12:53:03.931583+00	f	2025-10-25 12:53:03.931583+00	\N	f	202510	0	0	0	0	f	f	\N	\N	\N	\N	\N	\N	f	f	f	f	2025-10-25 12:53:03.931583+00	https://t.me/@business_ru/888888
79972a07-941a-4c9a-9ef9-e4ee4a84b88e	11c77f6b-2a54-4139-a20b-43d8a7950f34	1199	**Наш русскоязычный датасет для TTS опубликован!**\n\nСегодня выкладываем открытые корпуса на 4000+ часов речи, а еще синтезатор речи ESpeech-TTS-1\n\nНаш датасет содержит больше 4000 часов русской речи. Статистика по корпусам:\n\n**Многоголосые:**\n[ESpeech-podcasts](https://huggingface.co/datasets/ESpeech/ESpeech-podcasts) - 3200 часов\n[ESpeech-webinars](https://huggingface.co/datasets/ESpeech/ESpeech-webinars2) - 850 часов\n\n**Одноголосые:**\n[ESpeech-igm](https://huggingface.co/datasets/ESpeech/ESpeech-igm) - 220 часов\n[ESpeech-buldjat ](https://huggingface.co/datasets/ESpeech/ESpeech-buldjat)- 54 часа\n[ESpeech-upvote](https://huggingface.co/datasets/ESpeech/ESpeech-upvote) - 296 часов\n[ESpeech-tuchniyzhab](https://huggingface.co/datasets/ESpeech/ESpeech-tuchniyzhab) - 306 часов\n\nДанные лежат вот тут: https://huggingface.co/ESpeech\n\nТехрепорт датасета доступен тут: https://github.com/Den4ikAI/ESpeech/blob/main/ESpeech_techreport.pdf\n\n\nТакже, мы решили провести некоторые эксперименты с TTS. Получилось обучить F5-TTS на 10000 часов речи и сделать одну из лучших по нашим замерам моделей в опенсурсе для русского языка. \n\n**Какие модели доступны?**\n[ESpeech-TTS-1 [RL] V1 ](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V1)- Первая версия модели с RL\n[ESpeech-TTS-1 [RL] V2 ](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V2)- Вторая версия модели с RL\n[ESpeech-TTS-1 PODCASTER [SFT]](https://huggingface.co/ESpeech/ESpeech-TTS-1_podcaster) - Модель обученная только на подкастах, лучше генерирует спонтанную речь\n[ESpeech-TTS-1 [SFT] 95K ](https://huggingface.co/ESpeech/ESpeech-TTS-1_SFT-95K)- чекпоинт с 95000 шагов (на нем основана RL V1)\n[ESpeech-TTS-1 [SFT] 265K](https://huggingface.co/ESpeech/ESpeech-TTS-1_SFT-256K) - чекпоинт с 265000 шагов (на нем основана RL V2)\n\nЛайкайте модель которая больше понравится чтобы мы понимали есть ли смысл запускать RL.\n\n**Послушать модели без скачивания можно вот здесь:**\n\n[https://huggingface.co/spaces/Den4ikAI/ESpeech-TTS\n](https://huggingface.co/spaces/Den4ikAI/ESpeech-TTS)\nСовместно с @speech_recognition_ru ещё сделали **лидерборд русского ТТС**, где можно глянуть метрики:\n\n[https://huggingface.co/spaces/ESpeech/open_tts_leaderboard_ru](https://huggingface.co/spaces/ESpeech/open_tts_leaderboard_ru)\nЗадать вопросы по поводу данных и модели можно в наших телеграм каналах:\n[https://t.me/den4ikresearch](https://t.me/den4ikresearch)\n[https://t.me/voice_stuff_chat](https://t.me/voice_stuff_chat)\n\nВы можете мне задонатить, чтобы у меня были ресурсы делать более крутые модели и датасеты:\n\nUSDT (TRC20): TEpEM4VVmGmqKHn4Xz1FxM7qZiXjWtUEUB\nBTC: bc1qw5lq7fc455e47hggax6zp8txw4ru7yvsxvawv3\nhttps://www.tbank.ru/cf/7WKnNMqWtOx	["video:5289698025449492088", "document:5289698025449492088"]	2025-08-26 10:41:53+00	t	2025-08-26 10:41:53+00	\N	t	202508	2372	44	0	0	f	t	2025-10-02 19:50:35+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.532678+00	https://t.me/@AGI_and_RL/1199
2a40376e-c02a-4198-978c-7bc4abdeb9ac	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24667	Акции Intel выросли на 9% после отчёта — компания получила **$4,1 млрд** прибыли. Это первая квартальная прибыль компании с 2023 года, до этого она получала убытки.\n\nТакже Intel дала оптимистичный прогноз по выручке в четвёртом квартале — **от $12,8 млрд до $13,8 млрд.**	["photo:5465155724939229272"]	2025-10-24 11:16:31+00	f	2025-10-24 11:16:31+00	\N	t	202510	4993	11	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.839555+00	https://t.me/@business_ru/24667
c45df06d-db59-40a3-a365-4c23728b8f82	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24666	В Госдуме предложили запретить международные входящие звонки на стационарные телефоны, объясняя инициативу борьбой с телефонным мошенничеством. Авторы считают, что именно через такие звонки чаще всего обманывают пенсионеров, которые продолжают пользоваться проводной связью.	[]	2025-10-24 10:33:39+00	f	2025-10-24 10:33:39+00	\N	f	202510	4927	21	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.842688+00	https://t.me/@business_ru/24666
4b1f9a0e-da3f-4245-9bd5-cd87388498f0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24665	Yandex B2B Tech представил Stackland — решение, которое позволяет развернуть в on-premises собственную ИИ-инфраструктуру всего за несколько часов. Он создан для компаний, которым важно хранить данные внутри контура, сохраняя преимущества облачных технологий. Все в одном решении — оркестратор контейнеров, хранилища и предустановленные базы данных, в том числе векторные, средства управления GPU-ресурсами и сети InfiniBand. Stackland подойдет финтеху, e-commerce, ритейлу и промышленности с жесткими регуляторными требованиями. Официальный релиз назначен на первый квартал 2026 года, а [сейчас](https://clck.ru/3PuZRN/?erid=2SDnjeHU6cF) разработчики проводят консультации и демо.	["photo:5465278891716375455"]	2025-10-24 09:34:21+00	f	2025-10-24 09:34:21+00	\N	t	202510	5174	39	0	0	f	f	\N	\N	\N	\N	\N	\N	f	f	f	f	2025-10-25 08:31:43.845658+00	https://t.me/@business_ru/24665
bf9ab06e-26a4-45f7-ae3c-d75d66b2fc5d	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	777777	Тестовый пост для e2e пайплайна с детальным логированием - trace	[]	2025-10-25 13:03:51.555027+00	f	2025-10-25 13:03:51.555027+00	\N	f	202510	0	0	0	0	f	f	\N	\N	\N	\N	\N	\N	f	f	f	f	2025-10-25 13:03:51.555027+00	https://t.me/@business_ru/777777
b7945933-58d8-4e2a-98da-803f7d08bf9f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1173		["photo:5440724477785796064"]	2025-07-10 12:47:03+00	t	2025-07-10 12:47:03+00	\N	t	202507	2089	10	0	0	f	t	2025-07-10 12:47:06+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.592762+00	https://t.me/@AGI_and_RL/1173
bdaebe83-03f0-41bb-a539-6bac3b2a8041	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24664	Евросоюз отложил до декабря принятие решения о том, стоит ли использовать замороженные активы Банка России для помощи Украине. Причиной переноса срока стала позиция Бельгии, которая настаивает на гарантиях того, что ей не придется нести риски по кредитам Украине в то время, как российские активы в основном хранятся в Бельгии, передает Bloomberg.	[]	2025-10-24 09:11:14+00	f	2025-10-24 09:11:14+00	\N	f	202510	4876	7	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.848587+00	https://t.me/@business_ru/24664
9ea37ede-aa68-429c-bd53-2fabcddb4118	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24663	Норвегия сохраняет мировое лидерство по доле электромобилей: в 2025 году на них пришлось 93% всех проданных машин. Страна стала примером успешного перехода на экологичный транспорт благодаря системе налоговых льгот и субсидий, сделавшей электрокары доступнее бензиновых аналогов.	["photo:5465155724939228935"]	2025-10-24 08:31:03+00	f	2025-10-24 08:31:03+00	\N	t	202510	5283	37	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.851463+00	https://t.me/@business_ru/24663
f19cc2f2-4ab7-4d1d-8812-3c9fc6364ede	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24662	Аналитики прогнозируют, что ключевая ставка в России может снизиться до 16% на фоне ослабления экономической активности и постепенного замедления инфляции. Эксперты отмечают, что рост цен замедлился, а потребительская и инвестиционная активность сокращаются, что создаёт предпосылки для мягкой денежно-кредитной политики. Решение Банка России ожидается в ближайшие месяцы.	[]	2025-10-24 07:46:06+00	f	2025-10-24 07:46:06+00	\N	f	202510	5025	9	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.854424+00	https://t.me/@business_ru/24662
d90f9f64-6783-4918-aace-338bc384a606	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24650	Сотрудник Apple по имени Сэм Санг (Sam Sung), прославившийся благодаря своей визитке с надписью «Sam Sung — Apple», сменил фамилию после интернет-хайпа.\n\nРаботая в Apple Store в Ванкувере, он стал вирусной сенсацией — ведь сочетание его имени с брендом конкурента выглядело как шутка судьбы. Через 12 лет он рассказал, что тогда специально не увольнялся, чтобы не подогревать внимание.\n\nТеперь он носит фамилию Струан и признаётся: та история до сих пор всплывает в разговорах, где бы он ни работал.	["photo:5463286014006129840"]	2025-10-23 12:35:50+00	t	2025-10-23 12:35:50+00	\N	t	202510	6304	38	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.901703+00	https://t.me/@business_ru/24650
e452227e-78e4-43c5-8edf-26c0c86751f2	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24645	Власти согласились не вводить НДС для сделок с российским ПО из реестра Минцифры. Меру планируют исключить из налогового законопроекта ко второму чтению.\n\nО сохранении льготы просили участники ИТ-отрасли. Они предупреждали, что изменения приведут к сокращению выручки, оттоку кадров и закрытиям.	["photo:5460972663016126831"]	2025-10-23 08:47:06+00	t	2025-10-23 08:47:06+00	\N	t	202510	6351	53	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.912448+00	https://t.me/@business_ru/24645
bd1115f9-d7d8-401c-8791-d71f816c34c8	11c77f6b-2a54-4139-a20b-43d8a7950f34	1217	Я считаю что нам всем нужен симулятор атомного реактора. Вот (ну конечно пока в начально виде)\n\nhttps://github.com/researchim-ai/atomic-sim\n\nЯ например ничего про них не знаю. Ллмки кое-чего знают и помогают.\n\nВ целом это такой заход в симы и енвайроменты для промышленности\nОпенсурс конечно же\n\nТо есть делаем симчик, потом в gym и генерим данные чтобы потом затачивать ллмки/рл в какой-то области\n\nСейчас реактор - потому что интересно и я особо такого не видел\n\n(хотя охота конечно вообще в целом станцию сделать, но пока далековато до этого)\n\nВ ресечим кстати делаются разные интересные проектики (в том числе и для прома еще один, про него расскажу чутка позже) https://t.me/researchim\nНу и стараюсь собирать статьи	["photo:5438285894139383451"]	2025-10-14 12:25:10+00	t	2025-10-14 12:25:10+00	\N	t	202510	1906	28	0	5	f	t	2025-10-15 05:43:19+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.488114+00	https://t.me/@AGI_and_RL/1217
5542944b-8058-4215-9bf3-478301f038fd	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24660	📱**Apple проиграла суд в Великобритании по иску на 1,5 млрд фунтов стерлингов, который подали от имени около 20 млн пользователей.\n**\nСуд решил, что компания злоупотребила доминирующим положением, подавляя конкуренцию на рынке распространения приложений.	[]	2025-10-24 06:19:01+00	f	2025-10-24 06:19:01+00	\N	f	202510	5341	7	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.860704+00	https://t.me/@business_ru/24660
20b7f8fc-e67b-4c3f-addb-e2e691e09003	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24659	«Мне нужен 1 триллион долларов». Маск назвал сумму, которая, по его словам, необходима, чтобы построить армию роботов Tesla. Эту фразу он произнес во время внутреннего звонка, где подводили финансовые итоги за третий квартал года.\n\nТот самый скромный айтишник, когда HR спросил о зарплатных ожиданиях.	["photo:5465155724939228853"]	2025-10-24 05:30:00+00	f	2025-10-24 05:30:00+00	\N	t	202510	5835	61	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.86341+00	https://t.me/@business_ru/24659
d9196d0e-78a2-45a4-b55d-4da970cd9ab0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24623	**8-летний нью-йоркский предприниматель Линус Пипмейер покоряет рынок вендинга.\n**\nМальчик создает значки собственного дизайна — с панорамами города и уличными мотивами. Он сам придумывает рисунки, оцифровывает, печатает, собирает и загружает их в гача-автомат, который установил в центре Нью-Йорка.\n\nМаркетинг у юного бизнесмена не хуже взрослого: он расклеивает объявления, рисует стрелки мелом и рассказывает прохожим, где стоит его автомат. У Линуса уже появились фанаты, следящие за новыми «коллекциями».\n\nНедавно он открыл сайт и начал продавать мерч с собственными принтами.	["photo:5458809292284099790"]	2025-10-22 05:15:23+00	t	2025-10-22 05:15:23+00	\N	t	202510	6504	85	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.958815+00	https://t.me/@business_ru/24623
bed45bd0-1205-47f3-8201-35611b253f10	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24658	Трамп помиловал основателя Binance Чанпэн Чжао.	["photo:5462903925125544944"]	2025-10-23 18:01:46+00	t	2025-10-23 18:01:46+00	\N	t	202510	6065	25	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.8826+00	https://t.me/@business_ru/24658
25785edc-bc0a-43f9-9346-19af6ea30afb	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24632	Google потерял около **$100 миллиардов капитализации** после анонса нового проекта OpenAI — браузера **Atlas**. Акции компании упали на **4%**.\n\nAtlas — это «умный браузер» со встроенным ChatGPT, который может **искать информацию, писать тексты, делать саммари и заполнять таблицы**. Его ключевая идея — **один запрос, один точный ответ**, без рекламы и бесконечных переходов между сайтами.\n\nСейчас Atlas доступен на **macOS**, а вскоре появится на **iOS, Android и Windows**. В планах — **интеграция с почтой и Google Sheets**, что делает его прямым конкурентом сервисам Google.	["photo:5461000103562179235"]	2025-10-22 11:01:12+00	t	2025-10-22 11:01:12+00	\N	t	202510	6694	76	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.94+00	https://t.me/@business_ru/24632
76bb6f80-33f4-4bb1-928d-e21e2abbbf88	11c77f6b-2a54-4139-a20b-43d8a7950f34	1192	Мне кажется, что опенаи скинут опенсурсную модельку сегодня	[]	2025-08-05 18:43:16+00	t	2025-08-05 18:43:16+00	\N	f	202508	2471	4	0	6	f	t	2025-08-05 18:43:30+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.550026+00	https://t.me/@AGI_and_RL/1192
85aa4570-cb9e-4520-ba8e-be064d060b7f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1184		["photo:5458797274965604308"]	2025-07-16 18:48:38+00	t	2025-07-16 18:48:38+00	\N	t	202507	2502	52	0	0	f	t	2025-07-16 18:48:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.568912+00	https://t.me/@AGI_and_RL/1184
4ec78401-078d-46ec-a880-9468ed644d6f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1183		["photo:5458797274965604309"]	2025-07-16 18:48:38+00	t	2025-07-16 18:48:38+00	\N	t	202507	3172	52	0	0	f	t	2025-07-16 18:48:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.571408+00	https://t.me/@AGI_and_RL/1183
fcb2ab52-7038-41a7-a019-4c0e14944a47	11c77f6b-2a54-4139-a20b-43d8a7950f34	1182		["photo:5458797274965604306"]	2025-07-16 18:48:38+00	t	2025-07-16 18:48:38+00	\N	t	202507	2544	51	0	0	f	t	2025-07-16 18:48:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.573442+00	https://t.me/@AGI_and_RL/1182
ed00d5ca-920b-4153-8827-3897169b8733	11c77f6b-2a54-4139-a20b-43d8a7950f34	1180	Мб кому интересно\nЧел который уволился из OpenAI 3 недели назад рассказывает о своих впечатлениях.\nРаботал кстати над запуском кодекса\nhttps://calv.info/openai-reflections	[]	2025-07-16 12:55:30+00	t	2025-07-16 12:55:30+00	\N	f	202507	2321	45	0	0	f	t	2025-07-16 13:02:36+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.577816+00	https://t.me/@AGI_and_RL/1180
117e2284-c648-421f-8047-260347138e08	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24653	ЕС также запретил поставлять в Россию трёхколесные велосипеды, самокаты, игрушечные педальные автомобили, коляски для кукол, сами куклы и головоломки.	[]	2025-10-23 14:01:34+00	t	2025-10-23 14:01:34+00	\N	f	202510	6271	55	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.89511+00	https://t.me/@business_ru/24653
74507603-c276-44e3-a963-51d4b5477541	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24649	**Техника в России может заметно подорожать из-за массового исхода американских IT-гигантов из Китая.\n**Microsoft, Google, Amazon и Apple сворачивают производство в Поднебесной — причиной стали политические пошлины, новые тарифы США и последствия локдаунов.\n\nMicrosoft переносит выпуск Surface, серверов и консолей Xbox, Amazon строит ИИ-кластеры во Вьетнаме и Индии, Google открывает сборочные линии в Таиланде, а Apple переносит производство iPad и HomePod во Вьетнам.\n\nПерестройка логистики и рост себестоимости приведут к подорожанию электроники по всему миру — в России особенно, где импорт и без того осложнён.	[]	2025-10-23 11:46:27+00	t	2025-10-23 11:46:27+00	\N	f	202510	7319	60	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.903698+00	https://t.me/@business_ru/24649
416ff5cf-5710-498f-8f57-0b3aeec961e3	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24652	Клиенты российских банков стали чаще попадать в мошенническую базу ЦБ за продажу крипты на p2p-сервисах. Из-за этого банки могут заблокировать их счета. При этом осенью ЦБ пообещал разработать механизм реабилитации таких россиян, но пока этого не произошло и продажа крипты  через эти площадки остается высокорискованной, отмечают участники рынка.	[]	2025-10-23 13:16:25+00	t	2025-10-23 13:16:25+00	\N	f	202510	6358	36	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.897497+00	https://t.me/@business_ru/24652
9f8690fa-66f8-40d5-8b7c-297a66827dc4	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24641	**📈**** Полный список налоговых поправок. Сегодня Госдума приняла их в первом чтении.\n**\n— НДС поднимают с 20 до 22%.\n\n— Порог УСН для бизнеса уменьшают с 60 млн доходов в год до 10 млн. Бизнес, который зарабатывает больше 10 млн в год, должен будет платить НДС.\n\n— Ставка акцизов будет проиндексирована на уровень инфляции в 2026 и 2027 годах.\n\n— Акцизы на алкоголь и сигареты поднимут выше уровня инфляции.\n\n— Льготы НДФЛ от продажи имущества отменяют. Теперь от НДФЛ освобождают при владении имуществом минимум от трёх лет.\n\n— Тариф взносов для IT-компаний увеличивается с 7,6% до 15%.\n\n— Налоговики получат право осматривать территорию и помещения компаний под налоговым мониторингом.\n\n— Поправки существенно расширяют рамки мониторинга. Теперь для участия в нем будет достаточно соответствовать одному из критериев — по выручке, активам или сумме уплаченных налогов, тогда как сейчас необходимо выполнение всех трех.\n\n— Упрощаются условия получения отсрочек, рассрочек и инвестиционных налоговых кредитов. Пакет документов сократят, а срок инвестиционного налогового кредита увеличивается с 5 до 10 лет.\n\n— Поправки к законопроекту принимаются до 10 ноября.\n\nПоправки должны вступить в силу с 1 января 2026 года.	[]	2025-10-23 05:45:29+00	t	2025-10-23 05:45:29+00	\N	f	202510	7117	230	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.921445+00	https://t.me/@business_ru/24641
698e850b-f5af-477a-9480-217b28d5b940	11c77f6b-2a54-4139-a20b-43d8a7950f34	1191	Сегодня мы выложили улучшенную версию RefalMachine/RuadaptQwen3-4B-Instruct 🎉\n\nМодель стала лучше по всем фронтам: \n1️⃣ На бенчмарке по каждой категории рост, в частности, на математике.\n2️⃣ Стабильность модели повысилась (меньше циклов).\n3️⃣ На арене также наблюдается рост (при снижении средней длины ответа!). \n\nТекущая версия (v2) на данный момент вероятно SoTA для русского языка среди всех тюнов и/или адаптаций 4B модели (на основании нашего бенчмарка). От исходной версии присутствуют небольшие отставания, однако на арене RuadaptQwen3-4B-Instruct стабильно обходит Qwen3-4B, а скорость генерации русскоязычного текста существенно лучше. Бенч можно посмотреть по ссылке (там без арены) https://huggingface.co/datasets/RefalMachine/llmtf_open_benchmark\n\nУлучшения связаны с более качественным post-training, включая использование нового SFT датасета (T-Wix), а также добавление preference-tune шага.\n\nВеса в основном репозитории и GGUF также обновлены:\nhttps://huggingface.co/RefalMachine/RuadaptQwen3-4B-Instruct\nhttps://huggingface.co/RefalMachine/RuadaptQwen3-4B-Instruct-GGUF	["photo:5192681092715508345"]	2025-07-24 16:37:27+00	t	2025-07-24 16:37:27+00	\N	t	202507	2943	33	0	0	f	t	2025-07-24 16:37:59+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.552125+00	https://t.me/@AGI_and_RL/1191
9ae657da-a12c-410b-93f5-9c003359dc6c	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24676	**В России могут ввести уголовную и административную ответственность за нелегальный майнинг\n**\nСоответствующие законопроекты подготовили глава комитета Госдумы по энергетике Николай Шульгинов и полпред президента Юрий Чайка. Документы предлагают дополнить КоАП и Уголовный кодекс новыми статьями, а хищение электроэнергии для добычи криптовалюты признать отягчающим обстоятельством.\n\nАвторы инициативы считают, что такие меры помогут снизить нагрузку на энергосистему и вывести майнинг из тени.	[]	2025-10-24 16:31:41+00	f	2025-10-24 16:31:41+00	\N	f	202510	4914	18	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.273674+00	https://t.me/@business_ru/24676
9614d360-4fca-41d7-8e1e-6826287799f0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24675	**В Китае начался новый тренд — Чебурашка вытесняет Лабубу.\n**\nМестная блогерша купила мягкую игрушку на стенде «Культура Москвы» в Шанхае и заявила, что теперь будет снимать «своего Чебурашку». Пост моментально стал вирусным: тысячи подписчиков попросили ссылку, где можно купить такую же игрушку.\n\nИнтерес к российской культуре в Китае продолжает расти. Там уже планируют запуск коллекций одежды с элементами народной росписи, выпуск игрушек на космическую тематику и съёмки фильмов о космосе.	["photo:5467407524752915147"]	2025-10-24 15:33:28+00	f	2025-10-24 15:33:28+00	\N	t	202510	5428	126	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.276744+00	https://t.me/@business_ru/24675
e5a38826-c80c-4a48-842a-db535dab7b5d	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24674	Ряд предпринимателей ждет «возвращения покупателей» в офлайн, а в этом время другие уже строят бизнес-модель вокруг онлайн-привычек. \n\nЧья схема работает лучше — разобрала директор hh.ru по маркетингу и PR на канале Яндекс Маркета.	["video:5467781066192357517", "document:5467781066192357517"]	2025-10-24 14:30:04+00	f	2025-10-24 14:30:04+00	\N	t	202510	4820	14	0	0	f	f	\N	\N	\N	\N	\N	\N	f	f	f	f	2025-10-25 13:57:45.279671+00	https://t.me/@business_ru/24674
fcef3a90-ac6e-4bf7-ae8b-ec90b3276b13	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24673		["photo:5465438093269137358"]	2025-10-24 14:01:32+00	f	2025-10-24 14:01:32+00	\N	t	202510	5116	119	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.282609+00	https://t.me/@business_ru/24673
0f2fba16-9e1c-46ed-b6af-38acdb2867d6	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24672		["photo:5465438093269137357"]	2025-10-24 14:01:32+00	f	2025-10-24 14:01:32+00	\N	t	202510	5239	119	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.28511+00	https://t.me/@business_ru/24672
a82ecc0f-800f-44bd-95d6-bd31e5ee2aec	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24671		["photo:5465162369253637616"]	2025-10-24 14:01:32+00	f	2025-10-24 14:01:32+00	\N	t	202510	5181	120	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.287793+00	https://t.me/@business_ru/24671
ce76c62e-dd13-470b-8c34-569f2cb4ffc2	11c77f6b-2a54-4139-a20b-43d8a7950f34	1185	Хм, похоже новая сота опенсурс ллм-прувер\nСтатьи пока нет.\n\nГенерили синтетические доказательства с возрастающей сложностью + самокоррекция на фидбеке от Lean компилера. RL\n\nGoedel-Prover-V2-**8B** моделька пишут что примерно как DeepSeek-Prover-V2-**671B**. \n32B еще лучше\n\nhttps://blog.goedel-prover.com/\n\nhttps://huggingface.co/Goedel-LM/Goedel-Prover-V2-8B\n\nhttps://huggingface.co/Goedel-LM/Goedel-Prover-V2-32B	["photo:5461009956217155848"]	2025-07-17 12:05:54+00	t	2025-07-17 12:05:54+00	\N	t	202507	6544	55	0	5	f	t	2025-07-17 12:12:07+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.566466+00	https://t.me/@AGI_and_RL/1185
6b5b2f66-0ba5-48c3-92f6-f0fb8a7e2be0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24670	Геймеры начали самоубиваться на фоне обвала рынка скинов в CS2. Компания-разработчик позволила легко создавать игровые предметы, которые ранее оценивались в тысячи долларов. В связи с этим молодые люди, потерявшие накопления, не выдерживают и сводят счёты с жизнью. СМИ сообщают о подростке, спрыгнувшем с крыши в Китае, и ещё более десятке неподтверждённых смертей.	["photo:5465438093269137365"]	2025-10-24 14:01:32+00	f	2025-10-24 14:01:32+00	\N	t	202510	5137	120	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.29044+00	https://t.me/@business_ru/24670
0974a1ff-cab2-4242-af6a-91a19ceba3f0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24669	**В Париже продали картину Ива Кляйна «Калифорния», созданную в 1961 году**. Монохромное полотно насыщенного синего цвета ушло с аукциона за **2 миллиарда рублей**, став одной из самых дорогих работ художника.	["photo:5465155724939229289"]	2025-10-24 13:01:53+00	f	2025-10-24 13:01:53+00	\N	t	202510	5073	91	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.292744+00	https://t.me/@business_ru/24669
00a02f30-a14e-4e12-86c7-95d9b5f7d6e6	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24668	В Госдуме предложили обязать продавцов недвижимости подписывать отдельное согласие на сделку, чтобы защитить их от мошенников. Авторы инициативы отмечают, что чаще всего жертвами афер становятся пожилые люди, которых злоумышленники убеждают оформить продажу под разными предлогами.	[]	2025-10-24 12:01:35+00	f	2025-10-24 12:01:35+00	\N	f	202510	4894	11	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 13:57:45.295136+00	https://t.me/@business_ru/24668
9f0a14f3-e496-4250-8c47-d742c733ec9b	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24661	Издание **The Times** сообщает, что Россия и Китай якобы начали новую форму разведывательной кампании, получившую в прессе название «секс-война». По данным журналистов, речь идёт о целенаправленной отправке в Кремниевую долину привлекательных агенток, задача которых — устанавливать связи с предпринимателями, инженерами и специалистами из крупных технологических компаний.\n\nИсточник издания утверждает, что цель операции — получение доступа к инсайдерской информации и технологическим разработкам. Официальных подтверждений этим данным нет, однако спецслужбы США, по сообщениям The Times, усилили наблюдение за подобными случаями.	["photo:5465155724939228927"]	2025-10-24 07:02:05+00	f	2025-10-24 07:02:05+00	\N	t	202510	5536	64	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-25 08:31:43.8579+00	https://t.me/@business_ru/24661
01467111-5fc4-4442-b204-2f7ab230e98f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24657	**Все хотят приобрести студию Warner Bros, чтобы получить права на библиотеку с Гарри Поттером, DC и сериалами HBO, — Bloomberg.\n**\nОпубликован полный список претендентов на покупку студии:\n- Apple,\n-Amazon,\n- Netflix,\n- NBCUniversal,\n- Paramount.\n\nНа данный момент фаворитом считается Paramount, которую возглавляет Дэвид Эллисон — сын одного из самых богатых людей планеты, Ларри Эллисона. Эллисон пообещал гендиректору Warner Bros Дэвиду Заславу, что в случае продажи тот сохранит свою должность.\n\nЕсли победит Apple, то, вероятно, в «Гарри Поттере» появится реклама iPhone.	["photo:5462903925125544784"]	2025-10-23 17:33:20+00	t	2025-10-23 17:33:20+00	\N	t	202510	6251	27	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.884664+00	https://t.me/@business_ru/24657
a8731d4e-563e-4e87-b02c-cc2eda464984	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24648	Одна из крупнейших металлургических и горнодобывающих компаний РФ Evraz с активами в России, США, Канаде, Чехии, Италии и Kазахстане попала под санкции ЕС.	[]	2025-10-23 11:01:22+00	t	2025-10-23 11:01:22+00	\N	f	202510	5766	19	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.905836+00	https://t.me/@business_ru/24648
a45bb205-30ee-4c47-839d-709841ab5b0f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24628	Крупные посреднические интернет-площадки будут обязаны следить за соблюдением налогового законодательства своими контрагентами и передавать данные о нарушениях в ФНС — такой проект подготовила налоговая, узнал РБК.\n\nЗа нарушение правил платформам грозят штрафы в 100 тысяч рублей, а продавцам — блокировка их товаров в поиске.	[]	2025-10-22 07:13:01+00	t	2025-10-22 07:13:01+00	\N	f	202510	6446	25	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.948429+00	https://t.me/@business_ru/24628
d4063284-2a3b-4e41-8a9b-d9a07a79ef5f	11c77f6b-2a54-4139-a20b-43d8a7950f34	1208	М прикольновое\n\nКвены дропнули Qwen3Guard модельки для анализа промтов и ответов моделек на сейфти\n\n0.6B 4B 8B\n\n```Обнаружение в реальном времени: Qwen3Guard-Stream специально оптимизирован для потоковой передачи данных, обеспечивая эффективную и своевременную модерацию при инкрементальной генерации токенов.\n\nТрёхуровневая классификация серьёзности: обеспечивает детальную оценку рисков, разделяя выходные данные на безопасные, спорные и небезопасные уровни серьёзности, что позволяет адаптировать их к различным сценариям развертывания.\n\nМногоязыковая поддержка: поддерживает 119 языков и диалектов, обеспечивая стабильную работу в глобальных и кросс-языковых приложениях.```\nhttps://qwen.ai/blog?id=f0bbad0677edf58ba93d80a1e12ce458f7a80548&from=research.research-list\n\nhttps://huggingface.co/collections/Qwen/qwen3guard-68d2729abbfae4716f3343a1\n\nhttps://github.com/QwenLM/Qwen3Guard	["photo:5375428790064381009"]	2025-09-24 07:22:56+00	t	2025-09-24 07:22:56+00	\N	t	202509	2574	79	0	0	f	t	2025-09-24 07:28:46+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.510903+00	https://t.me/@AGI_and_RL/1208
3e64acd9-1377-4a59-b488-b76ac99355c3	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24613	В России криптовалюту могут приравнять к совместно нажитому имуществу. Согласно законопроекту депутата Игоря Антропенко, цифровые активы, купленные в браке, будут считаться общей собственностью супругов. Исключение составят монеты, приобретённые до свадьбы или полученные безвозмездно. Инициатива направлена на защиту прав при разводах, поскольку криптовалюта всё чаще используется как инструмент инвестиций и накоплений.	[]	2025-10-21 11:31:11+00	t	2025-10-21 11:31:11+00	\N	f	202510	6282	39	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.98071+00	https://t.me/@business_ru/24613
d3014129-403a-42e8-8e58-242d55c2a1f3	11c77f6b-2a54-4139-a20b-43d8a7950f34	1207	Тут опять учат квадрокоптеры летать рлем\nпричем в нейронке всего 2084 параметров и она норм работает на 10 разных квадрокоптерах\n\nВидосик тут\nhttps://www.reddit.com/r/robotics/comments/1njl25z/raptor_a_foundation_policy_for_quadrotor_control/\n\nRAPTOR: A Foundation Policy for Quadrotor Control\nhttps://arxiv.org/abs/2509.11481\nhttps://www.alphaxiv.org/ru/overview/2509.11481v1	["photo:5936978137470581332"]	2025-09-18 20:46:02+00	t	2025-09-18 20:46:02+00	https://www.reddit.com/r/reinforcementlearning/s/plgpZd7Zm9	t	202509	2902	81	0	1	f	t	2025-09-18 20:50:19+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.515412+00	https://t.me/@AGI_and_RL/1207
b39f0551-00d4-4f39-b233-4f1d76496021	7f194a2a-5206-4348-b42d-1b3976ec7d43	1219	📰  **Лувр ограбили: из главного музея Франции сегодня похитили 9 драгоценностей**\n\nИсчезли предметы из коллекции вещей, принадлежавших __Наполеону Бонопарту,__ его супруге __Жозефине__ и другим монархам. Речь, в частности, идет об ожерелье, броши и диадеме.\n\nГрабители проникли в музей утром. Трое или четверо злоумышленников в масках подъехали к музею **на скутерах** со стороны Сены, где ведутся ремонтные работы. Используя автолестницу, они поднялись до окон на втором этаже и **разбили** их, проникнув в галерею. Затем грабители с помощью небольших **бензопил** вскрыли витрины с драгоценностями. В общей сложности ограбление длилось всего** 7 минут.**\n\nСогласно последним данным, один из похищенных предметов был** найден неподалеку** от Лувра. Речь идет о короне императрицы Евгении де Монтихо, супруги Наполеона III. Из-за ограбления музей был закрыт для посещения на весь день. Никто из-за этого происшествия не пострадал, хотя сообщалось о панике внутри музея в момент ограбления.\n\n🎭  [**__@artnews_tg__**](https://t.me/+x7FBBnWsOFhjYzdi)** **— __новости искусства. самое важное и интересное. подписывайтесь.__\n\n[источник](https://www.lemonde.fr/societe/article/2025/10/19/le-musee-du-louvre-victime-d-un-braquage-et-ferme-pour-la-journee-annonce-la-ministre-de-la-culture_6648121_3224.html)	["photo:5449708359663091491"]	2025-10-19 17:09:57+00	t	2025-10-19 17:09:57+00	\N	t	202510	30	0	0	6	f	t	2025-10-19 17:10:01+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.411676+00	https://t.me/@okolo_art/1219
11fced41-c9d9-4729-8246-5530809970b5	11c77f6b-2a54-4139-a20b-43d8a7950f34	1197	Смешное из подкаста с ex-CTO OpenAI, Greg Brockman. Он рассказал про времена, когда компания занималась разработкой ботов для DOTA 2:\n\n— Мы хотели разработать новые RL алгоритмы, потому что всем в тот момент времени было очевидно, что тогдашние методы не масштабировались. Все знали это. Я помню мой коллега сказал: «а почему это так? Кто-то проверял? Мы правда это знаем?». Я тогда ответил, мол, да, это наш бейзлайн, мы должны отмасштабировать текущий метод и отталкиваться от него. Я помню, как приходил в офис каждую неделю: они удваивали количество ядер на сервере, они играли больше игр, рейтинг агента рос и рос. Я говорил, что нужно продолжать, пока мы не упрёмся в стену. А потом уже можно пойти заняться интересными вещами.\n\nИ мы так и не упёрлись в стену...\n\n(прим.: у них по итогу работал тот же метод, PPO, что они придумали ранее. И им же годы спустя дообучали LLM-ки следовать инструкциям. И, вероятно, им же — или его модификацией — учат агентов / рассуждения. GRPO от DeepSeek — это модификация PPO)\n\n[Клип](https://youtube.com/clip/Ugkx665gtfANA0SRKppuNnvscrbgzKQf6cH7?si=jMcp5lHc0aeXRqsj) (не знал эту историю, решил поделиться)	["photo:5837834074482391600"]	2025-08-20 19:55:46+00	t	2025-08-20 19:55:46+00	https://youtube.com/clip/Ugkx665gtfANA0SRKppuNnvscrbgzKQf6cH7?si=jMcp5lHc0aeXRqsj	t	202508	2612	20	0	2	f	t	2025-08-20 20:04:40+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.538684+00	https://t.me/@AGI_and_RL/1197
11ff9a4d-6de2-4ead-b0ef-a886b9b471e5	11c77f6b-2a54-4139-a20b-43d8a7950f34	1195	Может кому интересно про сравнение архитектур gpt-oss с GPT2 и Квенов недавних\nhttps://magazine.sebastianraschka.com/p/from-gpt-2-to-gpt-oss-analyzing-the?utm_campaign=posts-open-in-app	["photo:5817735792200628591"]	2025-08-11 16:42:46+00	t	2025-08-11 16:42:46+00	https://magazine.sebastianraschka.com/p/from-gpt-2-to-gpt-oss-analyzing-the	t	202508	4066	101	0	0	f	t	2025-08-11 16:43:07+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.543332+00	https://t.me/@AGI_and_RL/1195
9a65ff54-449c-461b-9c04-5b3828828d8f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24646	Евросоюз принял 19-й пакет санкций против России. Новые санкции направлены против российских банков, криптобирж, компаний в Индии и Китае. ЕС также ограничит передвижения российских дипломатов, чтобы «противостоять попыткам дестабилизации».	[]	2025-10-23 09:31:25+00	t	2025-10-23 09:31:25+00	\N	f	202510	5854	9	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.910369+00	https://t.me/@business_ru/24646
0541f67c-7468-4b30-a46d-4fcb410b9665	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24639		["photo:5458589050656128849"]	2025-10-22 18:04:52+00	t	2025-10-22 18:04:52+00	\N	t	202510	7706	221	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.92569+00	https://t.me/@business_ru/24639
ceb9baf8-4fc6-43ad-8d2f-05798d83f766	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24633	В Госдуме опровергли слухи о грядущем запрете Telegram и WhatsApp. По словам депутатов, введённые ограничения **временные** и направлены исключительно на **повышение кибербезопасности и защиту от злоумышленников**.\n\nПолная блокировка мессенджеров **не рассматривается**, заверили в парламенте.	[]	2025-10-22 12:47:00+00	t	2025-10-22 12:47:00+00	\N	f	202510	6332	42	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.938173+00	https://t.me/@business_ru/24633
e4d35c34-c945-4de7-a974-f78728cf02b8	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24629	АвтоВАЗ массово переводит станочников в уборщики, чтобы сохранить рабочие места, пишет Mash. Теперь квалифицированные специалисты убирают стружку, моют подвалы и красят оборудование. До этого из-за сокращения объёмов производства компания перешла на четырехдневную рабочую неделю без сохранения зарплаты.	[]	2025-10-22 08:01:16+00	t	2025-10-22 08:01:16+00	\N	f	202510	6133	61	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.946483+00	https://t.me/@business_ru/24629
6f06618f-92d2-4fa2-b3cf-8bc6fa2636b4	7f194a2a-5206-4348-b42d-1b3976ec7d43	1216	Знаете, самая моя слабая тема в искусстве это символизм. \n\nНу никак мне не удаётся разглядеть образ Христа в запеченой рыбе на натюрмортах художников. Даже учитывая, что я помню про ИХТИС и ИНРИ, всё равно для меня это сопоставимо с конспирологией. Не доросла, видимо. \n\nПравда, есть одно исключение — искусство Китая. \n\nВ Поднебесной религия особенно не прижилась. Даже буддизм был принесён извне. Главные темы в китайском искусстве — природа и взаимодействие с ней человека. Поскольку общество Китая оставалось аграрным вплоть до двадцатого века.\n\nСчиталось, что искусство некое зеркало природы, способное либо опустошить, либо обновить художника духовно. Отсюда вдохновляющие и благородные тематики. Или социально нравоучительные функции, например, в портретах и фресках. Когда изображались мудрые императоры и их злые противоположности. \n\nИскусство Китая никогда не стремилось к фотографической точности и реализму. Изобразить внутреннюю сущность объекта было важнее.\n\nСимволизм там вполне понятный, я бы даже сказала, конкретный: \n\nБамбук олицетворяет дух (гнётся, но не ломается), дракон — символ императора, журавль — долголетия, пара уток — верность в браке, орхидея — символ чистоты и верности, а сосна символизирует стойкость и т. д. \n\nРассцвет китайского искусства пришёлся на период с 618 — 906 годы н. э. \nВо времена правления династии Тан. \nЕсли будете гуглить, ищите "Империя Тан". \n\nТам император Тай-цзун расширил империю вглубь Центральной Азии и до Кореи. Потом передал наследие сыну, и тот тоже постарался. Искусство и технологии развивались шустро. Люди жили в мире и гармонии. \n\nНо не все. \n\nПоэтому династию потом свергли. А вот достижения остались. Потому то мы и можем теперь разбирать на символы изображения на китайском фарфоре и наслаждаться атмосферой древних китайских пейзажей.\n\n#искусство	["photo:5449772560834231955"]	2025-10-18 11:15:46+00	t	2025-10-18 11:15:46+00	\N	t	202510	51	1	0	0	f	t	2025-10-18 11:21:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.418127+00	https://t.me/@okolo_art/1216
0cf306d4-0af6-4be7-8213-f46d2d48d83a	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24610	📈 В 2026 году кофе в России подорожает примерно на 25% — этому способствуют колебания доллара и ценовые войны на биржах. Однако, по прогнозам аналитиков, спрос не снизится: даже при росте стоимости россияне продолжат покупать напиток по 500 рублей за стакан.	[]	2025-10-21 10:01:16+00	t	2025-10-21 10:01:16+00	\N	f	202510	6119	39	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.984682+00	https://t.me/@business_ru/24610
8004b799-5c5d-47c9-950c-19d24d90de09	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24617	**США и Австралия подписали соглашение о сотрудничестве в сфере редкоземельных металлов на сумму до $8,5 млрд.** Это сотрудничество расширит для Вашингтона доступ к критически важным материалам в условиях контроля за экспортом со стороны Китая, отмечает Bloomberg. На этом фоне сильно выросли акции австралийских компаний, связанных с редкоземельными металлами.	[]	2025-10-21 13:48:01+00	t	2025-10-21 13:48:01+00	\N	f	202510	6316	5	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.972116+00	https://t.me/@business_ru/24617
b64de121-1358-45d7-aa72-1139f6d6398c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1193	**QVikhr-3-8B-Instruction** \n\nПополнение еще одной моделью на базе **Qwen 3**. В **DOoM**, **QVikhr-3-8B-Instruction** получила оценку 0.445, что существенно превосходит результат базовой модели **Qwen3-8B**. Модель подходит для решения задач по математике и физике на русском языке.\n\n🔗 **Карточка модели:** https://huggingface.co/Vikhrmodels/QVikhr-3-8B-Instruction\n🔗 **GGUF (скоро):** https://huggingface.co/Vikhrmodels/QVikhr-3-8B-Instruction-GGUF\n⚖️ **Лицензия: **apache-2.0\n\nСайт: https://vikhr.org\nДонаты: [Здесь](https://www.tbank.ru/cf/3W1Ko1rj8ah)\n\n👥 Авторы: @LakoMoorDev @nlpwanderer	["photo:5231148305877169242"]	2025-08-06 14:19:51+00	t	2025-08-06 14:19:51+00	\N	t	202508	3138	12	0	0	f	t	2025-08-06 14:59:58+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.547897+00	https://t.me/@AGI_and_RL/1193
3bed169c-e381-4430-a059-fb3ecf906d6f	7f194a2a-5206-4348-b42d-1b3976ec7d43	1182	В закрытом чатике канала разгадали, верно. \nТри картинки выше: Танец, лёгкость и пантомиму объединяет слово **балет**. \n\nС лёгкостью балетных танцоров вроде всё понятно. За ней стоят тяжёлые тренировки, стёртные в кровь ноги и постоянное преодоление своих физических возможностей. \n\nС танцем тоже всё ясно. Каждое движение имеет своё название и регламент правильности его выполнения. Связку движений между собой в одну цепочку помогают оттачивать строгие преподаватели. И, конечно же, тренировки с утра и до атрофии мышц. \n\nА пантомима? \n\nСчитается, что без пантомимы балету бы не хватало содержательности. Но балетная пантомима — вещь особая. Несколько сотен человек из зала просто не могут рассмотреть мимику танцующих или неявные жесты. \nПоэтому жесты и позы в балете нарочито объёмные. Не заметить их возможным не представляется. \n\nОбычно пантомимой обозначают сюжетные повороты или пиковые моменты эмоций героев. Так сказать, моменты максимального кипения чувств.\n\nЕсли в девятнадцатом веке пантомима была буквальной. Определённый жест выражал конкретное слово. Например, два поднятых вверх пальца обозначают клятву, а указание на безымянный палец — свадьбу. \n\nТо в двадцатом веке пантомима стала более абстрактной. Жесты могли выражать целые фразы. Например, указательный палец по очереди показывает на глаза. Это означает: "Смотри, я тебе кое-что покажу." Или "Я видел." \n\nНа данный момент пантомима в балете стала почти что "специей". Некоторые режиссёры считают её пережитком прошлого и почти не используют. \n\nНо другие режиссёры увлекаются до такой степени, что превращают постановку в "немое кино." Смотреть на такое сложно. \n\nКак бы там не было, пока пантомима остаётся неотъемлемой частью балета. \n\n— Вращение кистями над головой это приглашение на танец. \n— Движение кулаками к земле или к сердцу — смерть. \n— Прикосновение к сердцу логично говорит о нежных и трепетных чувствах. \n— Касание лба означает видение или сон.\n\n#искусство@okolo_art	[]	2025-09-29 10:55:36+00	t	2025-09-29 10:55:36+00	\N	f	202509	58	1	0	0	f	t	2025-10-06 20:28:38+00	\N	1178	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.478765+00	https://t.me/@okolo_art/1182
c746f80d-5d1c-49ff-b386-1434376a2700	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24622	Правительство предупредило о цунами банкротств после снижения порога доходов для уплаты НДС с 60 миллионов до 10. \n\nСогласно статистике, от этого больше всего пострадают ИП с доходом менее 200 тысяч рублей в месяц.	[]	2025-10-22 04:27:48+00	t	2025-10-22 04:27:48+00	\N	f	202510	6642	73	0	0	f	f	\N	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.961273+00	https://t.me/@business_ru/24622
0fcb33fe-8733-419b-8e16-b921125f5afc	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24621	💶 **Глава Tehter Паоло Ардоино сообщил, что число пользователей стейблкоина USDT достигло 500 млн. А общий объём эмиссии, по подсчётам The Block, составил почти $182 млрд. \n**\nВ сентябре 2025 года источники Bloomberg рассказали, что Tether ведёт переговоры с инвесторами о привлечении **$20 млрд **при оценке в** $500 млрд.**	[]	2025-10-21 16:47:11+00	t	2025-10-21 16:47:11+00	\N	f	202510	6642	20	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.963902+00	https://t.me/@business_ru/24621
fe618e8e-d1c4-45fe-a6e2-6e33ed9a2822	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24620	В России легализуют криптовалюту для внешней торговли, — Силуанов. \n\nЗа этими операциями будет повышенный контроль со стороны ведомства и ЦБ, уточнил министр.	[]	2025-10-21 16:06:52+00	t	2025-10-21 16:06:52+00	\N	f	202510	6648	34	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.966023+00	https://t.me/@business_ru/24620
f1af80cb-727b-4c0b-b9d1-e109c6bee007	11c77f6b-2a54-4139-a20b-43d8a7950f34	1181	Как же он понял... 👍👍👍\nhttps://x.com/_jasonwei/status/1945294042138599722	["photo:5458797274965604307"]	2025-07-16 18:48:38+00	t	2025-07-16 18:48:38+00	\N	t	202507	2190	50	0	1	f	t	2025-07-16 18:51:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.575711+00	https://t.me/@AGI_and_RL/1181
b357d25f-902c-43c5-ba04-49d2fe276da7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1233		["photo:5458590669858799014"]	2025-10-21 21:24:20+00	t	2025-10-21 21:24:20+00	\N	t	202510	35	1	0	0	f	t	2025-10-21 21:24:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.382186+00	https://t.me/@okolo_art/1233
405ae69b-897d-40c2-8833-cb6e5f866fdd	11c77f6b-2a54-4139-a20b-43d8a7950f34	1220	https://www.arxiv.org/abs/2509.19162\n\nМы выложили на архив третью статью проекта CayleyPy. \n(Первая статья была принята на самую топовую конференцию [NeaurIPS как spotlight](https://t.me/sberlogabig/596) - то есть в топ3%.)\n\nА также представляем первый релиз нашей библиотеки - CayleyPy - для работы методами МЛ/RL с графами размера гугл: https://github.com/cayleypy/cayleypy (Кидайте звезды  ⭐⭐  на наш гитхаб - они нам очень помогут !) Библиотека также ставится через pypi: https://pypi.org/project/cayleypy/ . \n\nСама статья с упором на математику - предложено около 200 новых математических гипотез полученных с помощью вычислительных экспериментов с нашей библиотекой, которая позволяет делать расчеты - которые за пределами существовавших ранее систем компьютерной алгебры.  Если у Вас есть знакомые математики занимающиеся теорий групп или графов - свяжите их с нами - @alexander_v_c  . [Slides at Oberwolfach](https://docs.google.com/presentation/d/1wI4XY9s-Y6L5qfpCMpFb1wMeon-7c8u0BMt1QZAjxd8/edit?usp=sharing).\n\nА также мы рады всем добровольцам - кто знает Питон или математику и имеет несколько свободных часов  - будем рады всем участникам - пинганите @alexander_v_c\n\nЧтобы бенчмаркать методы и одновременно двигать математику и биоинформатику - мы создали более 10 челленжей на Каггл.\nВсем кому интересен Каггл  - тоже присоединяйтесь\nhttps://www.kaggle.com/competitions/cayleypy-christophers-jewel\nhttps://www.kaggle.com/competitions/cayleypy-glushkov\nhttps://www.kaggle.com/competitions/CayleyPy-pancake\nhttps://www.kaggle.com/competitions/cayleypy-transposons	["photo:5388615215836429818"]	2025-10-15 18:36:31+00	t	2025-10-15 18:36:31+00	\N	t	202510	1986	25	0	0	f	t	2025-10-15 18:37:22+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.480343+00	https://t.me/@AGI_and_RL/1220
f7080db0-604c-4659-9803-28ed87f08110	11c77f6b-2a54-4139-a20b-43d8a7950f34	1178	Кстати Kimi K2 это раздутый DeepSeek V3/R1. Меньше хедов в мульти-хеде, больше экспертов \n\nhttps://x.com/rasbt/status/1944056316424577525\n\nЕще померили на бенчмарке "эмоционального интеллекта" https://eqbench.com/\nЩас у него больший скор из всех моделек в бенче. \nЕще он лучший в креативном написании текстов	["photo:5449820441129643813"]	2025-07-13 12:57:08+00	t	2025-07-13 12:57:08+00	\N	t	202507	2609	45	0	1	f	t	2025-07-13 12:57:43+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.582074+00	https://t.me/@AGI_and_RL/1178
f935f35d-83db-4e71-9044-fc341b16d5e0	7f194a2a-5206-4348-b42d-1b3976ec7d43	1189		["photo:5413887211427987261"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	53	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.46303+00	https://t.me/@okolo_art/1189
a9aeb5a9-0971-4cd1-92ff-b35854c8e9e7	11c77f6b-2a54-4139-a20b-43d8a7950f34	1179	Gguf с любыми квантами Kimi K2 от анслота на месте. Рекомендуют 256гб оперативы и 16гб врам+ иметь для мелких квантов\n\nunsloth/Kimi-K2-Instruct-GGUF · Hugging Face\nhttps://huggingface.co/unsloth/Kimi-K2-Instruct-GGUF	["photo:6029490358636886491"]	2025-07-15 15:40:00+00	t	2025-07-15 15:40:00+00	https://huggingface.co/unsloth/Kimi-K2-Instruct-GGUF	t	202507	2622	33	0	0	f	t	2025-07-15 15:43:22+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.579894+00	https://t.me/@AGI_and_RL/1179
6c33890c-9f7e-4bb3-95c4-776baf342154	11c77f6b-2a54-4139-a20b-43d8a7950f34	1177	Для тех кому куда и гпу прог интересен\n\nчел выложил решения ко всем задачкам из известной книжки Programming Massively Parallel Processors\n\nhttps://github.com/tugot17/pmpp/	["photo:6029249870533081603"]	2025-07-13 07:37:28+00	t	2025-07-13 07:37:28+00	https://github.com/tugot17/pmpp	t	202507	2463	67	0	0	f	t	2025-07-13 07:44:51+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.584218+00	https://t.me/@AGI_and_RL/1177
c88806f2-81d0-49bc-a300-6158d371e526	7f194a2a-5206-4348-b42d-1b3976ec7d43	1188	Настроение:\nСмотреть натюрморты Николая Мазура. \n\nА остальное сдюжим как нибудь)	["photo:5413887211427987260"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	53	1	0	0	f	t	2025-10-06 20:27:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.465406+00	https://t.me/@okolo_art/1188
f6f1cd74-0654-4807-a79d-32a46f657a2c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1176	Как насчет опенсурсной агенточной модельки на 1Т параметров? Kimi K2\n\nhttps://moonshotai.github.io/Kimi-K2/\n\nhttps://huggingface.co/moonshotai/Kimi-K2-Instruct\n\nhttps://huggingface.co/moonshotai/Kimi-K2-Base\n\nhttps://github.com/MoonshotAI/Kimi-K2?tab=License-1-ov-file#readme\n\nMOE  с 32б активных параметров. Но все равно 1Т общих оч много\nНо зато опенсурс и поэтому кайфуем. Еще и от челов которые RL над ллмками активно делают\nВсем РЛьным респект всегда	["photo:5442965686210133767"]	2025-07-11 16:18:09+00	t	2025-07-11 16:18:09+00	\N	t	202507	2444	29	0	7	f	t	2025-07-11 16:42:47+00	\N	1160	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.586414+00	https://t.me/@AGI_and_RL/1176
b2cd4549-1416-4811-8846-ca21511e35b7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1190		["photo:5413887211427987262"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.460994+00	https://t.me/@okolo_art/1190
0357b00a-3872-4d54-b95c-ef203f8c56d3	11c77f6b-2a54-4139-a20b-43d8a7950f34	1216	Помним ли мы Genie - ворлд моделс от дипмаинда?\nСтатья выходила в феврале 2024\n\n**Genie: Generative Interactive Environments**\nhttps://arxiv.org/abs/2402.15391\nhttps://www.alphaxiv.org/ru/overview/2402.15391v1\n\n(в августе вот уже 3ю версию анонсили https://deepmind.google/discover/blog/genie-3-a-new-frontier-for-world-models/)\n\nофициального имплемента не выкладывали,\nно есть неофициальный \nhttps://github.com/myscience/open-genie\n\nТак вот - на этот раз чел сделал так сказать минималистичную учебную реализацию, так что мы можем сами поизучать и чего-нибудь потренить\n\nhttps://github.com/AlmondGod/tinyworlds\n\nВсем кому тема интересна считаю обязательно стоит покопаться\n\nставим автору звездочки, изучаем и делаем свои ворлмоделсы (для RLя конечно же 🎩)	["photo:5411248194542763534"]	2025-10-05 15:09:11+00	t	2025-10-05 15:09:11+00	\N	t	202510	2225	52	0	0	f	t	2025-10-05 15:28:57+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.490698+00	https://t.me/@AGI_and_RL/1216
270db607-b928-43a7-a6bb-5e87052f9be9	11c77f6b-2a54-4139-a20b-43d8a7950f34	1210	Тут выходила работа от стенфордских\n\nАгент делает других агентов прямо из научных статей\n\nПишет код для MCP сервера, всякие тулы, сам тестит\n\nПоказывают на примере AlphaGenome и говрят что в результате полученный агент 100% на примерах из статьи выбивает\nАгент реализовал все нужное за 3 часа\n\nПо-моему ну прям хорошо\n\n**Paper2Agent: Reimagining Research Papers As Interactive and Reliable AI Agents**\nhttps://arxiv.org/abs/2509.06917\nhttps://www.alphaxiv.org/ru/overview/2509.06917v1\n\nhttps://github.com/jmiao24/Paper2Agent	["photo:5379793176916784027"]	2025-09-25 03:15:55+00	t	2025-09-25 03:15:55+00	\N	t	202509	3177	139	0	0	f	t	2025-10-02 19:48:32+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.505544+00	https://t.me/@AGI_and_RL/1210
53ba5b41-af7f-479b-bcae-0edced8b16f6	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24608	Павел Дуров прокомментировал ограбление Лувра, заявив, что причиной подобных событий стала политика французских властей. По его словам, она привела к «упадку некогда великой страны» и росту социальной нестабильности.	["photo:5456582618029094291"]	2025-10-21 08:33:56+00	t	2025-10-21 08:33:56+00	\N	t	202510	6549	14	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.988842+00	https://t.me/@business_ru/24608
5e9091d2-1f60-4ab0-aa52-6689a74b0a63	11c77f6b-2a54-4139-a20b-43d8a7950f34	1223		[]	2025-10-21 18:14:29+00	t	2025-10-21 18:14:29+00	\N	f	202510	1249	2	0	1	f	t	2025-10-21 18:14:32+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:20.965984+00	https://t.me/@AGI_and_RL/1223
c41d0fb3-3401-4d51-b0b2-8c6e89971f97	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24647	**Последнее обновление в CS2 вызвало обвал рынка скинов и подорвало внутриигровую экономику — за несколько часов игроки потеряли свыше $1 млрд\n**\nТеперь пять предметов тайного качества можно обменять на нож из той же коллекции, что резко снизило его редкость и стоимость. Если раньше за нож приходилось платить $5 000, то теперь его можно получить примерно за $5.\n\nЦены на «красные» пушки выросли в 10–20 раз, а стоимость ножей, наоборот, обрушилась. Ещё недавно скины считались более надёжной инвестицией, чем крипта или фондовый рынок, но обновление показало, насколько хрупок этот рынок.	["photo:5462903925125543956"]	2025-10-23 10:14:01+00	t	2025-10-23 10:14:01+00	\N	t	202510	6306	99	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.908145+00	https://t.me/@business_ru/24647
cb42b3d6-fcde-4973-9c87-5909b971687b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1222	https://openai.com/index/introducing-chatgpt-atlas/\n\n(он так и не написал чатгпт нормально)	["photo:5458755781286560587"]	2025-10-21 18:13:27+00	t	2025-10-21 18:13:27+00	\N	t	202510	1268	8	0	1	f	t	2025-10-21 19:02:52+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.475455+00	https://t.me/@AGI_and_RL/1222
99999c80-03f3-4958-8d92-1893cf3bffba	11c77f6b-2a54-4139-a20b-43d8a7950f34	1219	Опа, квен3вл 4б (и там ещё 8б)\nДо этого были только 30б и 235б - жирновато\n\nА тут и в домашний комп влезет\n\nhttps://huggingface.co/Qwen/Qwen3-VL-4B-Instruct	["photo:6013898222898101179"]	2025-10-14 18:37:51+00	t	2025-10-14 18:37:51+00	https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct	t	202510	2138	70	0	4	f	t	2025-10-15 05:43:30+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.483366+00	https://t.me/@AGI_and_RL/1219
3b5295c7-40b0-4bbd-98d0-028a6321bf3c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1175	Пара приятных и полезных находок\n\nВведение в диффузионки\n\n**Step-by-Step Diffusion: An Elementary Tutorial**\nhttps://arxiv.org/abs/2406.08929\n\nИ обзор методов скрытых рассуждений в ллмках (т.е. когда ллмы "рассуждают" не текстом в лицо, а во внутренних представлениях модельки)\nВ целом какие подходы бывают, как тренируют и про интерпретируемость\n\n**A Survey on Latent Reasoning**\nhttps://arxiv.org/abs/2507.06203\nhttps://www.alphaxiv.org/ru/overview/2507.06203v1\n\nhttps://github.com/multimodal-art-projection/LatentCoT-Horizon	["photo:6016102412999310160"]	2025-07-10 15:18:41+00	t	2025-07-10 15:18:41+00	https://arxiv.org/abs/2406.08929	t	202507	2381	103	0	0	f	t	2025-07-10 16:29:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.588549+00	https://t.me/@AGI_and_RL/1175
c385d8eb-4ec0-4003-b42e-d5c571cb8080	11c77f6b-2a54-4139-a20b-43d8a7950f34	1174	Ну шьто, как вам грок 4 кто уже трогал?	[]	2025-07-10 12:47:41+00	t	2025-07-10 12:47:41+00	\N	f	202507	1777	2	0	3	f	t	2025-07-10 13:19:03+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.590837+00	https://t.me/@AGI_and_RL/1174
d0e00060-0613-4bf4-be57-76d28376ec3d	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24635	Миллиардер Михаил Гуцериев выиграл в суде ЕС дело об отмене санкций против него за 2024 год. Как сказано в постановлении суда в Люксембурге, отмене подлежит решение от 26 февраля 2024 года. Постановление суда не означает исключение Гуцериева из санкционного списка: после указанной даты санкции ЕС вновь продлевались. Гуцериев оказался в санкционном списке ЕС в июне 2021-го — в Брюсселе обвинили его в поддержке режима Лукашенко.	["photo:5461000103562180325"]	2025-10-22 16:06:15+00	t	2025-10-22 16:06:15+00	\N	t	202510	6307	7	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.934189+00	https://t.me/@business_ru/24635
d951b65c-2f2f-498b-aa4e-771fe39b84f4	11c77f6b-2a54-4139-a20b-43d8a7950f34	1211	RL должен быть в школьной программе	[]	2025-09-25 08:27:10+00	t	2025-09-25 08:27:10+00	\N	f	202509	2373	14	0	11	f	t	2025-09-25 08:27:20+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.503466+00	https://t.me/@AGI_and_RL/1211
900275c8-2a01-4d50-b412-a34603fd3701	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24640	Запрещённая в России Meta уволит около 600 сотрудников ИИ-подразделения на фоне миллиардных инвестиций в ИИ-гонку.\n\nПо мнению компании, это позволит избавиться от чрезмерной бюрократии и сделать процесс работы более гибким.	["photo:5461000103562180441"]	2025-10-23 05:02:52+00	t	2025-10-23 05:02:52+00	\N	t	202510	5928	18	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.92371+00	https://t.me/@business_ru/24640
5df0098a-c8b6-4705-8f3a-f5eb03f91f10	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24638		["photo:5458589050656128847"]	2025-10-22 18:04:52+00	t	2025-10-22 18:04:52+00	\N	t	202510	7763	220	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.927568+00	https://t.me/@business_ru/24638
f129538f-3e99-49f9-8aa8-cf5b2f657681	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24637	Девушка ушла из офиса и теперь зарабатывает **200 тысяч рублей в месяц**, строя дома в The Sims.\n\nБывший маркетолог собрала команду виртуальных строителей и продаёт их работы через **Boosty**. Постройки расходятся мгновенно — себестоимость почти нулевая, а прибыль стабильная.\n\n«Никакого начальства, дедлайнов и корпоративов — только симы и кэш», — говорит она.	["video:5458589050199900558", "document:5458589050199900558"]	2025-10-22 18:04:52+00	t	2025-10-22 18:04:52+00	\N	t	202510	7721	221	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.929693+00	https://t.me/@business_ru/24637
2b8ba8da-333e-43ec-8edc-1dc24f3f2be8	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24618	В Японии запустили приложение для состоятельных мужчин, которое помогает им искать спутниц.\nРазработчики обещают полную проверку анкет: девушки проходят верификацию личности и внешности, а мужчины могут зарегистрироваться только после подтверждения дохода — не менее 5,3 миллиона рублей в год.\n\nСоздатели сервиса называют его «элитной альтернативой Tinder» и уверяют, что цель проекта — «соединять успешных мужчин и женщин, ценящих статус и честность».	["video:5456582617572867621", "document:5456582617572867621"]	2025-10-21 14:31:45+00	t	2025-10-21 14:31:45+00	\N	t	202510	6851	106	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.970249+00	https://t.me/@business_ru/24618
6bc9fd60-6593-4ccf-8212-10cbcd0d5908	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24636	Маркетплейсы вытесняют традиционную розницу, предлагая товары по ценам, которые в среднем в три–пять раз ниже, чем в обычных магазинах.	["photo:5461000103562180329"]	2025-10-22 16:51:51+00	t	2025-10-22 16:51:51+00	\N	t	202510	6480	43	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.932071+00	https://t.me/@business_ru/24636
67e2eba2-2d3c-41f1-ade7-f0f4de3d0dcd	7f194a2a-5206-4348-b42d-1b3976ec7d43	1221		["photo:5456425667039197464"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	32	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.407887+00	https://t.me/@okolo_art/1221
8044ce8c-b85b-4fbc-a53b-d6cebde44d88	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24627	OpenAI показала ChatGPT Atlas — ИИ-браузер с возможностью открыть диалог с чат-ботом на любой вкладке и режимом агента.\n\nБраузер пока доступен только для macOS. Режим агента в превью-версии на старте получат подписчики ChatGPT Plus и Pro.	["video:5461000103105951089", "document:5461000103105951089"]	2025-10-22 06:27:01+00	t	2025-10-22 06:27:01+00	\N	t	202510	6173	46	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.950467+00	https://t.me/@business_ru/24627
577cc7ba-3659-4cba-8382-50b84246d8b3	7f194a2a-5206-4348-b42d-1b3976ec7d43	1220	Художник Дэвид Зинн (David Zinn).\n\nХорошего вам настроения! \nКак сегодня у меня 😎	["photo:5456425667039197463"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	33	1	0	0	f	t	2025-10-24 07:45:25+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.409815+00	https://t.me/@okolo_art/1220
28b3ac4e-dde9-4ef7-8a28-369cffd7406d	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24624		["photo:5458809292284099791"]	2025-10-22 05:15:23+00	t	2025-10-22 05:15:23+00	\N	t	202510	6484	83	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.95653+00	https://t.me/@business_ru/24624
229299ee-e075-42e5-8919-883f1ab4ed93	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24619	**Volkswagen на грани финансового краха\n**\nНемецкий автогигант Volkswagen может столкнуться с серьезными финансовыми проблемами. Компании не хватает 11 миллиардов евро для стабильной работы в следующем году, сообщает BILD.	["photo:5456582618029095208"]	2025-10-21 15:16:37+00	t	2025-10-21 15:16:37+00	\N	t	202510	6878	93	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.968131+00	https://t.me/@business_ru/24619
496e39fb-3567-4185-bec9-bbd2fb795a2c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1205	Надеюсь ребята RLем буду заниматься	[]	2025-09-08 19:21:04+00	t	2025-09-08 19:21:04+00	\N	f	202509	3070	4	0	1	f	t	2025-09-08 19:21:28+00	\N	1203	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.520796+00	https://t.me/@AGI_and_RL/1205
cd97cba1-3273-498b-91d2-8c31726944dc	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24612	Павел Дуров заявил, что готов выкупить украденные из Лувра драгоценности и передать их в филиал музея в Абу-Даби.\n\n«Никто не ворует из Лувра в Абу-Даби», — написал он в X, добавив, что произошедшее символизирует «упадок Франции и потерю ею культурного величия».	["photo:5456582618029094480"]	2025-10-21 10:46:31+00	t	2025-10-21 10:46:31+00	\N	t	202510	6325	56	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.98283+00	https://t.me/@business_ru/24612
e576cc7b-6cc8-46c5-9956-311a8d4955d7	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24614	Нелегальный мигрант в Канаде выиграл $5 млн в лотерею, но не смог оформить билет на своё имя и записал его на знакомую девушку. После получения выигрыша она исчезла с деньгами и новым бойфрендом. История быстро разлетелась по соцсетям, а настоящий победитель теперь ждёт решения суда, чтобы попытаться вернуть свой приз.	["photo:5456255285686567845"]	2025-10-21 12:13:01+00	t	2025-10-21 12:13:01+00	\N	t	202510	6699	86	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.978491+00	https://t.me/@business_ru/24614
0c51ba82-d4fc-40bd-814d-ebdaca484b7d	11c77f6b-2a54-4139-a20b-43d8a7950f34	1204	Поздравляем) 🥳	[]	2025-09-08 19:19:21+00	t	2025-09-08 19:19:21+00	\N	f	202509	3104	4	0	0	f	t	2025-09-08 19:19:48+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.522987+00	https://t.me/@AGI_and_RL/1204
996582f5-0a1b-4a70-882c-9da977ce1a75	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24615		["photo:5456255285686567846"]	2025-10-21 12:13:01+00	t	2025-10-21 12:13:01+00	\N	t	202510	6561	87	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.976125+00	https://t.me/@business_ru/24615
f1d60b79-b25a-426e-a46d-8587c65cecbe	11c77f6b-2a54-4139-a20b-43d8a7950f34	1203	https://spbu.ru/news-events/novosti/studenty-spbgu-stali-pobeditelyami-mezhdunarodnogo-chempionata-po	[]	2025-09-08 19:18:23+00	t	2025-09-08 19:18:23+00	\N	f	202509	3277	6	0	0	f	t	2025-09-08 19:19:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.525221+00	https://t.me/@AGI_and_RL/1203
570b4caf-eb53-4556-ac4a-eb446a52170c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1198	RL пушить надо	[]	2025-08-20 19:56:12+00	t	2025-08-20 19:56:12+00	\N	f	202508	2930	6	0	2	f	t	2025-08-20 19:58:32+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.536337+00	https://t.me/@AGI_and_RL/1198
ebb2891d-0539-4b7d-b33f-d2c07fb83d67	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24634	Nebius Аркадия Воложа и Uber инвестируют до $375 млн в разработчика беспилотных технологий Avride — подразделение бывшей Yandex N.V. Компания запланировала расширить географию присутствия и увеличить парк до 500 беспилотных автомобилей.\n\nПервые автомобили с беспилотными системами Avride должны появиться в американском Далласе до конца 2025 года в рамках сервиса роботакси от Uber. Это будут собранные в США Hyundai Ioniq 5.	["photo:5461000103562179981"]	2025-10-22 13:36:05+00	t	2025-10-22 13:36:05+00	\N	t	202510	6403	14	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.936229+00	https://t.me/@business_ru/24634
1f1f511b-edba-4cff-9f53-ab7e0a8a7887	7f194a2a-5206-4348-b42d-1b3976ec7d43	1228		["photo:5456425667039197471"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	37	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.393972+00	https://t.me/@okolo_art/1228
36f50e2b-715b-41f0-bb8f-f02594414899	11c77f6b-2a54-4139-a20b-43d8a7950f34	1187	Ставим звездочки и участвуем в интересном проекте	[]	2025-07-18 14:37:08+00	t	2025-07-18 14:37:08+00	\N	f	202507	2062	2	0	0	f	t	2025-07-18 14:49:38+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.562221+00	https://t.me/@AGI_and_RL/1187
9f813f17-3a79-43c8-9830-5f7be9637f9d	7f194a2a-5206-4348-b42d-1b3976ec7d43	1232		["photo:5458590669858799013"]	2025-10-21 21:24:20+00	t	2025-10-21 21:24:20+00	\N	t	202510	35	1	0	0	f	t	2025-10-21 21:24:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.384379+00	https://t.me/@okolo_art/1232
d8a50bd8-d048-4a3b-b64e-f23f64231691	7f194a2a-5206-4348-b42d-1b3976ec7d43	1231		["photo:5458590669858799012"]	2025-10-21 21:24:20+00	t	2025-10-21 21:24:20+00	\N	t	202510	35	1	0	0	f	t	2025-10-21 21:24:23+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.386797+00	https://t.me/@okolo_art/1231
1fbb4f6b-5bfc-4669-8897-481cd329e9b4	7f194a2a-5206-4348-b42d-1b3976ec7d43	1230		["photo:5458590669858799011"]	2025-10-21 21:24:20+00	t	2025-10-21 21:24:20+00	\N	t	202510	36	1	0	0	f	t	2025-10-21 21:31:26+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.389142+00	https://t.me/@okolo_art/1230
b39aac65-4589-4a58-9be2-11c7088e2ded	7f194a2a-5206-4348-b42d-1b3976ec7d43	1229		["photo:5456425667039197472"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	37	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.391641+00	https://t.me/@okolo_art/1229
2eefabc1-d6d7-4482-903b-b67e848e54f0	7f194a2a-5206-4348-b42d-1b3976ec7d43	1227		["photo:5456425667039197470"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	37	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.396179+00	https://t.me/@okolo_art/1227
015bcec3-acc6-462b-90e8-8556c88d099c	7f194a2a-5206-4348-b42d-1b3976ec7d43	1226		["photo:5456425667039197469"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	35	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.398136+00	https://t.me/@okolo_art/1226
001b3d66-57f5-406f-a560-16536984cf71	7f194a2a-5206-4348-b42d-1b3976ec7d43	1225		["photo:5456425667039197468"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	35	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.400058+00	https://t.me/@okolo_art/1225
091a7192-a296-43e0-a796-d50e8b65201c	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24609	**В 2025 финансовом году доходы США от пошлин выросли до рекордных $195 млрд, что в 2,5 раза превышает показатель 2024 финансового года, — Bloomberg.\n**\nПри текущих темпах роста доходы от пошлин могут превысить** $350 млрд** в 2026 финансовом году.	["photo:5456582618029094370"]	2025-10-21 09:17:01+00	t	2025-10-21 09:17:01+00	\N	t	202510	6521	18	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.986813+00	https://t.me/@business_ru/24609
bd850165-ea84-433b-bf48-c4cc7e605953	7f194a2a-5206-4348-b42d-1b3976ec7d43	1224		["photo:5456425667039197467"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	35	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.402023+00	https://t.me/@okolo_art/1224
f8501657-d629-4720-ad0e-4335a2b48ab7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1223		["photo:5456425667039197466"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	35	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.403978+00	https://t.me/@okolo_art/1223
e21fc184-8599-47c6-b094-dd13cb68773b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1222		["photo:5456425667039197465"]	2025-10-21 08:08:01+00	t	2025-10-21 08:08:01+00	\N	t	202510	33	1	0	0	f	t	2025-10-21 08:08:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.40582+00	https://t.me/@okolo_art/1222
3c3dcb8d-c188-4ff4-b3f4-10f0e6133994	7f194a2a-5206-4348-b42d-1b3976ec7d43	1207		["photo:5413887211427987279"]	2025-10-06 17:44:58+00	t	2025-10-06 17:44:58+00	\N	t	202510	81	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.426651+00	https://t.me/@okolo_art/1207
3684f840-9802-474f-b08e-75121449f390	7f194a2a-5206-4348-b42d-1b3976ec7d43	1206		["photo:5413887211427987278"]	2025-10-06 17:44:58+00	t	2025-10-06 17:44:58+00	\N	t	202510	90	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.428786+00	https://t.me/@okolo_art/1206
082a9d4f-8e0a-49c7-bd7e-14f216f65af7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1202		["photo:5413887211427987274"]	2025-10-06 17:44:57+00	t	2025-10-06 17:44:57+00	\N	t	202510	81	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.436938+00	https://t.me/@okolo_art/1202
8b6d891b-7479-497d-9d7b-00d91ba6583f	7f194a2a-5206-4348-b42d-1b3976ec7d43	1201		["photo:5413887211427987273"]	2025-10-06 17:44:57+00	t	2025-10-06 17:44:57+00	\N	t	202510	80	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.438704+00	https://t.me/@okolo_art/1201
9a5a3f88-26c4-485c-b3da-1697b36b77dd	7f194a2a-5206-4348-b42d-1b3976ec7d43	1200		["photo:5413887211427987272"]	2025-10-06 17:44:57+00	t	2025-10-06 17:44:57+00	\N	t	202510	76	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.440844+00	https://t.me/@okolo_art/1200
4fff5e62-98cc-4679-9346-3454b3aef0fd	7f194a2a-5206-4348-b42d-1b3976ec7d43	1199		["photo:5413887211427987271"]	2025-10-06 17:44:57+00	t	2025-10-06 17:44:57+00	\N	t	202510	78	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.442922+00	https://t.me/@okolo_art/1199
e025dbfb-a5b8-400d-a518-cd4dc66ff4d8	7f194a2a-5206-4348-b42d-1b3976ec7d43	1212	Смотрим оперу Турандот. Пока это возможно 🩷\n\nhttps://youtu.be/fnE2N09nuPI?si=Ykhxx99eVEiDI3Ba	["photo:5869617498192720673"]	2025-10-14 20:08:52+00	t	2025-10-14 20:08:52+00	https://youtu.be/fnE2N09nuPI?si=Ykhxx99eVEiDI3Ba	t	202510	59	1	0	0	f	t	2025-10-14 20:39:42+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.422489+00	https://t.me/@okolo_art/1212
57bdf053-7983-4e0f-9502-8dac2bb7b76a	7f194a2a-5206-4348-b42d-1b3976ec7d43	1209	Гуф «оперный певец»))\n\nВидео стащила [тут](https://t.me/aistarsss/8130)	["video:5420371456242514923", "document:5420371456242514923"]	2025-10-08 21:07:55+00	t	2025-10-08 21:07:55+00	\N	t	202510	88	2	0	3	f	t	2025-10-09 14:11:41+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.424621+00	https://t.me/@okolo_art/1209
80c32c96-9d55-43d2-8641-a772b32fbfa6	7f194a2a-5206-4348-b42d-1b3976ec7d43	1205		["photo:5413887211427987277"]	2025-10-06 17:44:58+00	t	2025-10-06 17:44:58+00	\N	t	202510	93	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.430785+00	https://t.me/@okolo_art/1205
09e3d9a0-06f7-471a-9271-7942a72935ed	7f194a2a-5206-4348-b42d-1b3976ec7d43	1204		["photo:5413887211427987276"]	2025-10-06 17:44:58+00	t	2025-10-06 17:44:58+00	\N	t	202510	92	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.432801+00	https://t.me/@okolo_art/1204
12492f4c-0f46-4236-8f7d-9948b5ab82ae	7f194a2a-5206-4348-b42d-1b3976ec7d43	1203		["photo:5413887211427987275"]	2025-10-06 17:44:57+00	t	2025-10-06 17:44:57+00	\N	t	202510	83	1	0	0	f	t	2025-10-06 17:45:00+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.435074+00	https://t.me/@okolo_art/1203
db0e00be-e162-4ff8-9085-8ccdda2ee455	11c77f6b-2a54-4139-a20b-43d8a7950f34	1221	заходите смотреть как ллмки делают деньги в реалтайме\nhttps://nof1.ai/\n\nмоделькам дали всем один промт и по 10к$ и отпустили трейдить, ну и вот\n\nувидел в https://t.me/j_links/8169	["photo:5449393139128335028"]	2025-10-18 11:11:22+00	t	2025-10-18 11:11:22+00	\N	t	202510	5439	343	0	13	f	t	2025-10-18 11:12:04+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.477908+00	https://t.me/@AGI_and_RL/1221
e9592709-064d-4839-883e-85f66000290a	11c77f6b-2a54-4139-a20b-43d8a7950f34	1218	А еще погенерил прикольную (на мой вкус) документацию с подробностями и базой, реально может стать понятнее если прям ничего не знаете.\nзацените	["document:5435888434348592775"]	2025-10-14 12:26:06+00	t	2025-10-14 12:26:06+00	\N	t	202510	1830	12	0	0	f	t	2025-10-15 05:43:24+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.485897+00	https://t.me/@AGI_and_RL/1218
1d6a676c-4478-4b1c-85e2-ff317a90d01e	11c77f6b-2a54-4139-a20b-43d8a7950f34	1215	Щас смотрю, нравится\nhttps://www.youtube.com/watch?v=nzsRVwgx2vo	["photo:5835715328460569245"]	2025-10-02 05:06:28+00	t	2025-10-02 05:06:28+00	https://www.youtube.com/watch?v=nzsRVwgx2vo	t	202510	2405	33	0	0	f	t	2025-10-02 05:06:51+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.494336+00	https://t.me/@AGI_and_RL/1215
b01f3a6b-7971-43aa-8c55-04e70353895b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1212	unsloth завезли ноутбук с рлем для gpt-oss моделек\nВ примере учат ллмку рлем писать более оптимизированные CUDA-кернелы\n\nhttps://docs.unsloth.ai/new/gpt-oss-reinforcement-learning	["photo:5971183501829060902"]	2025-09-28 02:19:50+00	t	2025-09-28 02:19:50+00	https://docs.unsloth.ai/new/gpt-oss-reinforcement-learning	t	202509	2149	85	0	0	f	t	2025-09-28 02:48:43+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.501232+00	https://t.me/@AGI_and_RL/1212
43a1b69c-38b3-4fc8-9504-96ceaf760a89	11c77f6b-2a54-4139-a20b-43d8a7950f34	1214	Скоро покупочки в чатегпт\nhttps://openai.com/index/buy-it-in-chatgpt/	["photo:5975254976206846895"]	2025-09-30 05:25:27+00	t	2025-09-30 05:25:27+00	https://openai.com/index/buy-it-in-chatgpt/	t	202509	2345	10	0	3	f	t	2025-09-30 05:27:09+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.496445+00	https://t.me/@AGI_and_RL/1214
e00ececa-4cd6-4867-8885-479cc26a165b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1201	опять обзор агентного ллмного рля\nценой всего\n\n**The Landscape of Agentic Reinforcement Learning for LLMs: A Survey**\nhttps://arxiv.org/abs/2509.02547\nhttps://www.alphaxiv.org/ru/overview/2509.02547v1\n\nhttps://github.com/xhyumiracle/Awesome-AgenticLLM-RL-Papers	["photo:5321366086794473624"]	2025-09-05 09:27:25+00	t	2025-09-05 09:27:25+00	\N	t	202509	4257	120	0	1	f	t	2025-09-05 09:28:39+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.527868+00	https://t.me/@AGI_and_RL/1201
332e35bd-e36e-4016-9230-7a7f6b04578b	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24654	Санкции США против российских нефтяных компаний вызвали беспокойство в Китае, — Bloomberg	[]	2025-10-23 14:31:38+00	t	2025-10-23 14:31:38+00	\N	f	202510	5979	2	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.8928+00	https://t.me/@business_ru/24654
18695b08-436d-419c-b8ef-ca52773eb5db	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24651		["photo:5463286014006129841"]	2025-10-23 12:35:50+00	t	2025-10-23 12:35:50+00	\N	t	202510	6296	37	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.899573+00	https://t.me/@business_ru/24651
7025fe0a-611a-4dbc-b56a-d8e2af88103b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1197		["photo:5413887211427987269"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	63	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.447005+00	https://t.me/@okolo_art/1197
e086a2f9-d520-4252-b1aa-a8018c1fac0b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1194		["photo:5413887211427987266"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	55	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.452587+00	https://t.me/@okolo_art/1194
a60d0d10-8384-4732-874e-67544bc026d3	7f194a2a-5206-4348-b42d-1b3976ec7d43	1193		["photo:5413887211427987265"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.454443+00	https://t.me/@okolo_art/1193
498a9801-d549-4d1e-bc9e-83731f1752e9	7f194a2a-5206-4348-b42d-1b3976ec7d43	1192		["photo:5413887211427987264"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.4569+00	https://t.me/@okolo_art/1192
6c25abb6-eea5-40c3-b08a-9fc93b28174b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1191		["photo:5413887211427987263"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	52	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.45888+00	https://t.me/@okolo_art/1191
219110b2-9e72-43ff-ac7a-94b088dddc62	11c77f6b-2a54-4139-a20b-43d8a7950f34	1200		["video:5289698025449492073", "document:5289698025449492073"]	2025-08-26 10:41:53+00	t	2025-08-26 10:41:53+00	\N	t	202508	2763	43	0	0	f	t	2025-08-26 10:41:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.530354+00	https://t.me/@AGI_and_RL/1200
4551b82a-631b-496a-865d-94e942fe2e24	11c77f6b-2a54-4139-a20b-43d8a7950f34	1196	https://gemini.google.com/app\nВпервые запустил кста\n\nPS все уже, не воркает	["photo:6034048143575419678"]	2025-08-15 21:19:22+00	t	2025-08-15 21:19:22+00	https://gemini.google.com/app	t	202508	3042	16	0	0	f	t	2025-08-16 14:23:06+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.541106+00	https://t.me/@AGI_and_RL/1196
8c133af9-4cf2-487e-9310-1afdb8626deb	11c77f6b-2a54-4139-a20b-43d8a7950f34	1194	я вот только вспомнил\nСейчас же RL конференция проходит!\n\nСтатьи https://rlj.cs.umass.edu/2025/2025issue.html	["photo:5235802688921402340"]	2025-08-07 20:33:37+00	t	2025-08-07 20:33:37+00	\N	t	202508	4009	19	0	0	f	t	2025-08-07 20:35:24+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.545619+00	https://t.me/@AGI_and_RL/1194
808856a7-97f7-43ab-b2de-9f7c0fba740b	11c77f6b-2a54-4139-a20b-43d8a7950f34	1190	шьто ни день то новые соты опенсурсы\n\nОбнова самой большой модельки от квенов\n\nhttps://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507\n\nЩас бы дипсику чонить выложить. Или ОпенАИ	["photo:5474237437581587760"]	2025-07-21 20:51:29+00	t	2025-07-21 20:51:29+00	\N	t	202507	2799	6	0	2	f	t	2025-07-21 20:53:59+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.555039+00	https://t.me/@AGI_and_RL/1190
d2de0cf5-1570-4d6b-9bcd-24a6fa1a0aea	7f194a2a-5206-4348-b42d-1b3976ec7d43	1198		["photo:5413887211427987270"]	2025-10-06 17:44:57+00	t	2025-10-06 17:44:57+00	\N	t	202510	72	1	0	0	f	t	2025-10-06 20:27:37+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.444896+00	https://t.me/@okolo_art/1198
3ee20fa0-69ce-4c3e-8e2c-1f11d4462305	7f194a2a-5206-4348-b42d-1b3976ec7d43	1196		["photo:5413887211427987268"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	67	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.448705+00	https://t.me/@okolo_art/1196
c0d8e436-dbd7-468a-9708-64644f17fc4d	7f194a2a-5206-4348-b42d-1b3976ec7d43	1195		["photo:5413887211427987267"]	2025-10-06 17:44:53+00	t	2025-10-06 17:44:53+00	\N	t	202510	66	1	0	0	f	t	2025-10-06 17:44:56+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.450569+00	https://t.me/@okolo_art/1195
1bf48881-0bae-4a74-8ca7-e14ee0faee6c	11c77f6b-2a54-4139-a20b-43d8a7950f34	1189	Каждая рандомная группа челов автоматически становится новой LLM RL лабой\n\nCUDA-L1: Improving CUDA Optimization via Contrastive Reinforcement Learning\nhttps://arxiv.org/abs/2507.14111\nhttps://www.alphaxiv.org/ru/overview/2507.14111v1	["photo:5472253398914037574"]	2025-07-21 14:07:35+00	t	2025-07-21 14:07:35+00	\N	t	202507	2761	13	0	0	f	t	2025-07-21 14:13:18+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.55716+00	https://t.me/@AGI_and_RL/1189
26c40906-8f94-4fbc-afc7-754009231bc1	11c77f6b-2a54-4139-a20b-43d8a7950f34	1188	🚀 Уважаемые коллеги,  кому интересна математика и машинное обучение,  приглашаем Вас принять участие в неформальном научном проекте.\n\nМы разрабатываем новые методы и опен-соурс библиотеку CayleyPy, которая на основе МЛ/РЛ методов позволяет решить математические задачи, которые были  не доступны ранее. Как пример наша система уже по всем параметрам  на порядки превсходит аналогичные методы в системе компьютерной алгебры GAP   (де-факто стандарт)  - использующую алгоритмы доработанные самим Д. Кнутом.\n\nЕсли у Вас желание поучаствовать в проекте,  есть знание Питона и несколько свободных часов в неделю - то присоединяйтесь к нам - при активной работе - Вы будете соавтором научных публикаций. (Напишите @alexander_v_c - к.ф.-м.н. Александр Червов).\n\nКраткая суть задачи может быть описана несколькими способами - нахождение пути на графе размером  10^20-10^200 (из-за размера  обычные методы не применимы - только МЛ/РЛ). Решение пазла типа кубика Рубика, задача сортировки, математически - разложение элемента группы по образующим  - все это в реальности одна и та же  задача. Задача близка к прошедшему конкурсу [Каггл Санта 2023](https://www.kaggle.com/competitions/santa-2023). Более общо - это задача планирования - типичная для реинфорсмент ленинг - спланировать действия так чтобы кумулятивный эффект давал лучший результат - управлением манипулятором робота, системы АльфаГо, АльфаТензор, успех DeepSeek  - это задачи - тесно связанные с тем, что мы делаем.\n\nА зачем это нужно биологам ? А чтобы превращать людей в мышей ))) (А [капусту в репу](https://dl.acm.org/doi/abs/10.1145/300515.300516)).  Так назвал свои [статьи](https://ieeexplore.ieee.org/abstract/document/492588) известный биоинформатик П.Певзнер - оказывается эволюционная дистанция - соответствует дистанции на определенных графах - и наша цель улучшить ее оценку через МЛ/РЛ.   Зачем нужно нужно в сетях  - задержка сигнала (latency) сети определяется диаметром сети - оценка диаметра графов - одна из наших целей.    В теории квантовых вычислений тоже нужны подобные графы и приложения этим не ограничены.   И, кроме того, а знаете чем знаменит Билл Гейтс ?)) Он отлично [сортировал блины](https://en.wikipedia.org/wiki/Pancake_sorting#The_original_pancake_problem) ! Наша задача - побить его - через МЛ/РЛ)))\n\nВ нашем коллективе есть профессора математики, Каггл градмастеры, и легендарные иностранные специалисты - Tomas Rokicki , Herbert Kociemba  - Вам будет у кого поучиться. \n\nПодробнее о проекте вы можете узнать в наших статьях https://arxiv.org/abs/2502.18663 https://arxiv.org/abs/2502.13266 и в нашей группе https://t.me/sberlogasci/1 и  ⭐ СТАВЬТЕ СТАРС ⭐ (звездочки) на наш гитхаб: https://github.com/cayleypy/cayleypy	["photo:5447190971956721008"]	2025-07-18 14:37:08+00	t	2025-07-18 14:37:08+00	\N	t	202507	2263	32	0	0	f	t	2025-07-18 14:38:13+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.559317+00	https://t.me/@AGI_and_RL/1188
570b04fc-e265-4793-b4f5-54eba04880b6	11c77f6b-2a54-4139-a20b-43d8a7950f34	1186		["photo:5461009956217155849"]	2025-07-17 12:05:54+00	t	2025-07-17 12:05:54+00	\N	t	202507	6345	55	0	0	f	t	2025-07-17 12:05:58+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:22.564344+00	https://t.me/@AGI_and_RL/1186
71fdf77e-4cd7-4230-adf6-fd3274357e27	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24655	Прокуратура запросила 8 лет колонии для Аяза Шабутдинова по делу о мошенничестве. Блогер признал свою вину.	["photo:5462903925125544752"]	2025-10-23 15:01:33+00	t	2025-10-23 15:01:33+00	\N	t	202510	6650	74	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.890363+00	https://t.me/@business_ru/24655
b41f0246-cb2d-4e3e-ada2-f1e318a2be41	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24644	**За первое полугодие рынок микрозаймов для бизнеса увеличился на 29%**. Объем новых займов составил 72,8 млрд рублей, а общий портфель достиг 111 млрд. Почти половина средств пришлась на продавцов с маркетплейсов, которые оформляют кредиты примерно в два раза чаще других предпринимателей.	[]	2025-10-23 08:02:06+00	t	2025-10-23 08:02:06+00	\N	f	202510	5847	14	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.914415+00	https://t.me/@business_ru/24644
d83ffa99-91c5-44d7-86b3-6fa6ec5ddd27	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24643	📉 Глава Минфина США Джанет Бессент заявила, что в ближайшие дни санкции против России будут существенно ужесточены.	[]	2025-10-23 07:11:21+00	t	2025-10-23 07:11:21+00	\N	f	202510	5813	12	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.916599+00	https://t.me/@business_ru/24643
d8814410-e216-4013-b563-c24828960804	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24642	👑 Минфин приступил к созданию реестра майнеров. По словам Антона Силуанова, в списке уже числится 1 364 человека.	[]	2025-10-23 06:31:41+00	t	2025-10-23 06:31:41+00	\N	f	202510	5853	12	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.919043+00	https://t.me/@business_ru/24642
c6aa642e-55f0-4d88-adc2-579fd71b6c03	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24626		["photo:5458809292284099793"]	2025-10-22 05:15:24+00	t	2025-10-22 05:15:24+00	\N	t	202510	6643	83	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.952366+00	https://t.me/@business_ru/24626
c4bd43a4-3b85-4fcd-9b47-f6593d7e5ffa	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24631	💰Россия нарастит объём зарубежных кредитов до **1,8 трлн рублей** в ближайшие три года — это на **14% больше**, чем планировалось ранее.\n\nСредства направят на поддержку экономик партнёров, развитие инфраструктуры и закупку российской продукции.\nСреди получателей — **Иран**, которому выделят деньги на строительство железной дороги, **Вьетнам** — на закупку военной техники, и **Египет** — на возведение атомной электростанции.\n\nТаким образом, Москва укрепляет экономические связи и продвигает свои технологии за рубежом.	[]	2025-10-22 10:01:04+00	t	2025-10-22 10:01:04+00	\N	f	202510	6027	25	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.941966+00	https://t.me/@business_ru/24631
94b19828-0461-4c1a-a9f4-984331766cd4	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24625		["photo:5458809292284099792"]	2025-10-22 05:15:23+00	t	2025-10-22 05:15:23+00	\N	t	202510	6711	83	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.954311+00	https://t.me/@business_ru/24625
05093057-8505-4c80-9a35-05d9cad81656	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24630	Просто и наглядно о росте НДС\n\n__Сохраняем__	["photo:5461000103562178959"]	2025-10-22 09:01:22+00	t	2025-10-22 09:01:22+00	\N	t	202510	6536	205	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.944296+00	https://t.me/@business_ru/24630
5bdb67f0-dfba-452b-84b6-522a65c6449f	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	24616	**Украденные из Лувра драгоценности всплыли на продаже в Москве — за 250 миллионов рублей**. На одной из онлайн-площадок появилось объявление с диадемой, серьгами и ожерельем из сапфиров и бриллиантов. Грабителей до сих пор не нашли, а следствие проверяет, не связаны ли продавцы с кражей.	["photo:5456582618029094736"]	2025-10-21 13:01:32+00	t	2025-10-21 13:01:32+00	\N	t	202510	6818	133	0	0	f	f	\N	\N	\N	\N	\N	\N	t	f	f	f	2025-10-24 20:13:22.974049+00	https://t.me/@business_ru/24616
8bf6b6c3-5c5e-4c56-af7c-9b307fb40a5c	7f194a2a-5206-4348-b42d-1b3976ec7d43	1218	Предположительно забрали 9 предметов из коллекции драгоценностей Наполеона и императрицы.	[]	2025-10-19 10:25:29+00	t	2025-10-19 10:25:29+00	\N	f	202510	44	1	0	0	f	t	2025-10-19 10:54:27+00	\N	1217	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.414049+00	https://t.me/@okolo_art/1218
2db5b6e3-1653-41e3-b285-0693d722560b	7f194a2a-5206-4348-b42d-1b3976ec7d43	1217	Лувр ограбили	[]	2025-10-19 10:07:11+00	t	2025-10-19 10:07:11+00	\N	f	202510	42	1	0	0	f	t	2025-10-24 07:45:35+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.416099+00	https://t.me/@okolo_art/1217
6045f651-d186-442c-b50f-5f151f35eafa	7f194a2a-5206-4348-b42d-1b3976ec7d43	1215	Устаю так сильно, что к вечеру еле ноги волочу. А в голове дымка. \n\nТут уж не до постов. И, честно говоря, вообще не до чего. \nГреет мысль, что осталось недолго терпеть)). \n\nА пока смотрим балет Щелкунчик. Поднимаем себе настроение к Новому году. \nГлавное при просмотре не думать о страданиях людей, которые ежегодно стояли в очередях при минусовой температуре или покупали поддельные билеты). \n\nА постановка хорошая, правда.\n\nhttps://youtu.be/TlVz_gqnyTA?si=KPZUHz4xKg-ikLvi	["photo:6031817015435572933"]	2025-10-16 08:54:06+00	t	2025-10-16 08:54:06+00	https://youtu.be/TlVz_gqnyTA?si=KPZUHz4xKg-ikLvi	t	202510	64	1	0	0	f	t	2025-10-16 11:58:19+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.420414+00	https://t.me/@okolo_art/1215
65a7354c-e521-4dea-9176-e2ce3b5f8fc9	7f194a2a-5206-4348-b42d-1b3976ec7d43	1187	А когда-то телохранители выглядели так.\n\nНа картинке: Фигура Фаберже, изображающая личного казачьего телохранителя императрицы Александры (1912).	["photo:5408972102394050594"]	2025-10-05 10:26:44+00	t	2025-10-05 10:26:44+00	\N	t	202510	62	1	0	0	f	t	2025-10-06 20:27:53+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.467964+00	https://t.me/@okolo_art/1187
d46f8531-3ba2-444b-b741-4c25565d41db	7f194a2a-5206-4348-b42d-1b3976ec7d43	1186	Про Маурицио Каттелана, если кто не помнит, писала тут https://telegra.ph/Mauricio-Kattelan-Fenomen-v-mire-iskusstva-02-14	["photo:5975524579893949283"]	2025-09-29 20:21:31+00	t	2025-09-29 20:21:31+00	https://telegra.ph/Mauricio-Kattelan-Fenomen-v-mire-iskusstva-02-14	t	202509	69	0	0	0	f	t	2025-10-06 20:28:09+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.470382+00	https://t.me/@okolo_art/1186
f322adad-b783-4263-abcf-c60df661519d	7f194a2a-5206-4348-b42d-1b3976ec7d43	1185	Отсылка к Дюшану?)	["video:5393269911957044245", "document:5393269911957044245"]	2025-09-29 20:06:27+00	t	2025-09-29 20:06:27+00	\N	t	202509	64	1	0	1	f	t	2025-10-06 20:28:20+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.472947+00	https://t.me/@okolo_art/1185
72be8a5e-75be-4efb-805b-1f12fc38ede8	7f194a2a-5206-4348-b42d-1b3976ec7d43	1184	Опять тырю мемы у Админушки. Что поделать, если у него исключительный вкус	["photo:5393269912413272197"]	2025-09-29 20:06:09+00	t	2025-09-29 20:06:09+00	\N	t	202509	63	1	0	1	f	t	2025-10-06 20:28:29+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.47484+00	https://t.me/@okolo_art/1184
38fb4a0b-8d1e-4708-b6f3-cd8219cd6969	7f194a2a-5206-4348-b42d-1b3976ec7d43	1183		["video:5993283401623274233", "document:5993283401623274233"]	2025-09-29 10:55:39+00	t	2025-09-29 10:55:39+00	\N	t	202509	50	1	0	0	f	t	2025-09-29 10:55:40+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.476808+00	https://t.me/@okolo_art/1183
509e4005-0afa-4586-b830-482d399abfb7	7f194a2a-5206-4348-b42d-1b3976ec7d43	1180		["photo:5379802471226014871"]	2025-09-27 09:51:24+00	t	2025-09-27 09:51:24+00	\N	t	202509	57	1	0	0	f	t	2025-09-27 09:51:27+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.481052+00	https://t.me/@okolo_art/1180
b948f580-4833-49ff-9d4f-e3b11700d861	7f194a2a-5206-4348-b42d-1b3976ec7d43	1179		["photo:5379802471226014858"]	2025-09-27 09:51:24+00	t	2025-09-27 09:51:24+00	\N	t	202509	63	1	0	0	f	t	2025-09-27 09:51:27+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.482947+00	https://t.me/@okolo_art/1179
703c6d71-f286-457a-b04a-808096b8b117	7f194a2a-5206-4348-b42d-1b3976ec7d43	1178	Играем. \n\nНайдите общее слово, которое объединяет эти три картинки	["photo:5379802471226014859"]	2025-09-27 09:51:24+00	t	2025-09-27 09:51:24+00	\N	t	202509	54	1	0	4	f	t	2025-09-27 10:25:58+00	\N	\N	\N	\N	\N	f	f	f	f	2025-10-24 20:13:23.484797+00	https://t.me/@okolo_art/1178
\.


--
-- TOC entry 3850 (class 0 OID 24577)
-- Dependencies: 237
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.schema_migrations (version) FROM stdin;
\.


--
-- TOC entry 3855 (class 0 OID 24767)
-- Dependencies: 245
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
-- TOC entry 3854 (class 0 OID 24747)
-- Dependencies: 244
-- Data for Name: telegram_auth_logs; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.telegram_auth_logs (id, session_id, event, reason, error_code, ip, user_agent, latency_ms, at, meta) FROM stdin;
\.


--
-- TOC entry 3853 (class 0 OID 24732)
-- Dependencies: 243
-- Data for Name: telegram_sessions; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.telegram_sessions (id, tenant_id, user_id, session_string_enc, key_id, status, created_at, updated_at, auth_error, error_details) FROM stdin;
013b2e7b-77ba-48e6-8e8c-c408a97e1b67	test-tenant	139883458	QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB	test-key-long	authorized	2025-10-24 18:47:32.71607+00	2025-10-24 19:28:02.772044+00	\N	\N
\.


--
-- TOC entry 3845 (class 0 OID 16384)
-- Dependencies: 232
-- Data for Name: tenants; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.tenants (id, name, created_at, settings) FROM stdin;
22222222-2222-2222-2222-222222222222	Test Tenant	2025-10-21 22:49:37.271912+00	{}
e70c43b0-e11d-45a8-8e51-f0ead91fb126	Tenant 139883458	2025-10-22 17:21:01.161759+00	{}
\.


--
-- TOC entry 3857 (class 0 OID 24822)
-- Dependencies: 247
-- Data for Name: user_channel; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.user_channel (user_id, channel_id, subscribed_at, is_active, settings) FROM stdin;
cc1e70c9-9058-4fd0-9b52-94012623f0e0	11c77f6b-2a54-4139-a20b-43d8a7950f34	2025-10-24 17:07:49.723702+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	630bbcf5-a6ad-43ab-a18e-be91cb3fef1b	2025-10-24 17:14:55.381414+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	7f194a2a-5206-4348-b42d-1b3976ec7d43	2025-10-24 17:15:32.402264+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	8b917088-703b-4c7e-978a-f27f7f2af34e	2025-10-26 13:33:56.514139+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	ca114e40-5c61-409d-8e27-433d7675cf22	2025-10-26 13:34:04.707604+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	66a20b7c-23f9-4c8f-899a-8b9f570a39db	2025-10-26 13:34:11.830817+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	042611a1-78e1-45d8-8994-0853d6ebd2f3	2025-10-26 13:34:22.331586+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	c47eb63a-c9ad-4c90-820b-9f1156c9c2a8	2025-10-26 13:34:30.939925+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	2a832a81-87e4-442d-ab1e-0f502029b733	2025-10-26 13:34:36.539883+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	8e2cdd08-8ea0-4fdf-9bfb-04d3f132b5b7	2025-10-26 13:34:42.378464+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	81324c8d-75a1-4ea7-9093-38e19d51c261	2025-10-26 13:35:01.794695+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	95c7210e-619d-403b-9044-54377b18ce8c	2025-10-26 13:35:10.582448+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	d5a9a167-f073-401a-a725-69e68cbfa463	2025-10-26 13:35:20.780386+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	fdb3a110-d9ed-4a84-a901-2258cb6f94fa	2025-10-26 13:35:27.668423+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	817dc325-c569-4c68-aa1d-58f2f276619c	2025-10-26 13:35:34.01059+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	5223b6df-58f9-4758-b7a9-b49bb87b72a0	2025-10-26 13:35:39.8695+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	3abb63cd-e950-42ae-8711-ce5cd4ee5f19	2025-10-26 13:35:46.001302+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	93ba043b-23b7-4549-963c-2df2d2a1974b	2025-10-26 13:35:51.599338+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	884a410c-118b-434c-bbfd-22251bbab33a	2025-10-26 13:36:00.035607+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	f58d7b92-c4a3-4ead-9e51-d12bb3527755	2025-10-26 13:36:05.783481+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	6dd6c4d1-7bdd-4b99-8584-732168095141	2025-10-26 13:36:11.727915+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	99913aea-4b0d-4d1a-85ab-2f7d4f1f673b	2025-10-26 13:36:17.483877+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	e6abff7e-a499-49c4-8402-c67aff9e1ed8	2025-10-26 13:36:35.585235+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	2bda56ff-6bfd-49a0-b70f-a1336befa800	2025-10-26 13:36:42.952609+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	8726a621-ef23-4ee1-9fe2-d91c36d4bfb4	2025-10-26 13:36:50.626196+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	b3557bfd-ef2d-40af-b4d5-7e56ce1b9b4a	2025-10-26 13:36:56.069714+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	f65dc96b-509b-4b3b-b10c-4e3aca335927	2025-10-26 13:37:02.201622+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	b279b366-77ce-44f7-90e8-95792b8fb0ec	2025-10-26 13:37:07.596082+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	b6e39529-0f33-4839-8849-d4c2f51aa086	2025-10-26 13:37:13.695924+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	b4ac44ac-e0c6-4481-b9ca-10f12752fe25	2025-10-26 13:37:19.39447+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	ce64a6a4-082e-4865-a0e6-dc38ee810c25	2025-10-26 13:37:26.042081+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	d10a6208-fc12-4953-9beb-15e080ae31a0	2025-10-26 13:37:31.486043+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	59e1cd47-5dcb-45fa-b0bf-d4f482fa8d78	2025-10-26 13:37:37.307142+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	9f9651e7-5c0b-451a-91ab-df79dda08059	2025-10-26 13:37:43.007363+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	c5fbe0ca-91bc-4589-b999-798f2003c285	2025-10-26 13:37:48.402079+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	720da7af-a794-4c93-851b-cc376f20b6c8	2025-10-26 13:37:57.076076+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	bbc2b864-d004-4e08-b08b-35ef06094344	2025-10-26 13:38:04.029883+00	t	{}
cc1e70c9-9058-4fd0-9b52-94012623f0e0	e9c05cee-e202-4c84-9f99-64c16f88fdbb	2025-10-26 13:38:09.141979+00	t	{}
\.


--
-- TOC entry 3861 (class 0 OID 24892)
-- Dependencies: 251
-- Data for Name: user_group; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.user_group (user_id, group_id, monitor_mentions, subscribed_at, is_active, settings) FROM stdin;
\.


--
-- TOC entry 3846 (class 0 OID 16395)
-- Dependencies: 233
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.users (id, tenant_id, telegram_id, username, created_at, last_active_at, settings, telegram_session_enc, telegram_session_key_id, telegram_auth_status, telegram_auth_created_at, telegram_auth_updated_at, telegram_auth_error, first_name, last_name, role, tier) FROM stdin;
cc1e70c9-9058-4fd0-9b52-94012623f0e0	e70c43b0-e11d-45a8-8e51-f0ead91fb126	139883458	ilyasni	2025-10-22 11:35:51.066755+00	\N	{}	gAAAAABo-9GZ27jYivFHrW9WOwXKssV9K5DyFz6BuFWkNZJ8m_VTE0xyQxmt-vVW2G6Nz0pjLR-gL1L8Pt3pN69Kj6LBlgNK_zyR_SFQgpMoZNqFvBK9aEH93gDL9W8sJmIYTsP_UV-yQCp_iAsqgLa_EGrw-3ogzwCLHGg1T4zG59crWQqB7z6-2hSrctI2ohQuqc1ULF1Ukj85jIeRfufVXq9bPVAK0--Th2g2aDdTKlsD0L00cef6JchCZCQxtiwSEZRXDCIlEZwpnpGJGQEPNR1-kXueVMUjyJCjjzeaoh7XO4PGblIYVqg042Ygq3DDp_ju9y2ByRl3V4LlLp6kxYrsj9gG3dm9_1zyLkigy8Qtnx5fsu3y24lz8izc-GxTxkBfvH-ik6eyLFwmOw910gSfmQfgUl7oypXGLYQuAabiRxe7yooFT8Tn3QZE0HmOp_T8YhUbsj5VD_c_TpqLQxLsJ9lUc7T_cYUEfRfEWObuMv2mN5hKNSDUc-ndSeW8dYyMZy1-hEz6k4pk7bqPYMomxl8Prza2nX9EUrXpen0mSp1scB0=	default_key_1761146055.690612	authorized	2025-10-24 19:20:51.8344+00	2025-10-24 19:20:51.8344+00	\N	Ilya	Kozlov	admin	premium
\.


--
-- TOC entry 3559 (class 2606 OID 24641)
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- TOC entry 3525 (class 2606 OID 16424)
-- Name: channels channels_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT channels_pkey PRIMARY KEY (id);


--
-- TOC entry 3561 (class 2606 OID 24731)
-- Name: encryption_keys encryption_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.encryption_keys
    ADD CONSTRAINT encryption_keys_pkey PRIMARY KEY (key_id);


--
-- TOC entry 3617 (class 2606 OID 24938)
-- Name: group_mentions group_mentions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_mentions
    ADD CONSTRAINT group_mentions_pkey PRIMARY KEY (id);


--
-- TOC entry 3610 (class 2606 OID 24923)
-- Name: group_messages group_messages_group_id_tg_message_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_group_id_tg_message_id_key UNIQUE (group_id, tg_message_id);


--
-- TOC entry 3612 (class 2606 OID 24921)
-- Name: group_messages group_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_pkey PRIMARY KEY (id);


--
-- TOC entry 3600 (class 2606 OID 24884)
-- Name: groups groups_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_pkey PRIMARY KEY (id);


--
-- TOC entry 3602 (class 2606 OID 24886)
-- Name: groups groups_tenant_id_tg_chat_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_tenant_id_tg_chat_id_key UNIQUE (tenant_id, tg_chat_id);


--
-- TOC entry 3553 (class 2606 OID 16468)
-- Name: indexing_status indexing_status_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.indexing_status
    ADD CONSTRAINT indexing_status_pkey PRIMARY KEY (id);


--
-- TOC entry 3581 (class 2606 OID 24803)
-- Name: invite_codes invite_codes_code_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_code_key UNIQUE (code);


--
-- TOC entry 3583 (class 2606 OID 24801)
-- Name: invite_codes invite_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 3644 (class 2606 OID 25317)
-- Name: outbox_events outbox_events_aggregate_id_event_type_content_hash_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_aggregate_id_event_type_content_hash_key UNIQUE (aggregate_id, event_type, content_hash);


--
-- TOC entry 3646 (class 2606 OID 25315)
-- Name: outbox_events outbox_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.outbox_events
    ADD CONSTRAINT outbox_events_pkey PRIMARY KEY (id);


--
-- TOC entry 3591 (class 2606 OID 25385)
-- Name: post_enrichment post_enrichment_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_enrichment
    ADD CONSTRAINT post_enrichment_pkey PRIMARY KEY (post_id, kind);


--
-- TOC entry 3636 (class 2606 OID 25080)
-- Name: post_forwards post_forwards_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_forwards
    ADD CONSTRAINT post_forwards_pkey PRIMARY KEY (id);


--
-- TOC entry 3597 (class 2606 OID 24868)
-- Name: post_media post_media_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_media
    ADD CONSTRAINT post_media_pkey PRIMARY KEY (id);


--
-- TOC entry 3630 (class 2606 OID 25059)
-- Name: post_reactions post_reactions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_reactions
    ADD CONSTRAINT post_reactions_pkey PRIMARY KEY (id);


--
-- TOC entry 3642 (class 2606 OID 25097)
-- Name: post_replies post_replies_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_replies
    ADD CONSTRAINT post_replies_pkey PRIMARY KEY (id);


--
-- TOC entry 3546 (class 2606 OID 16443)
-- Name: posts posts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_pkey PRIMARY KEY (id);


--
-- TOC entry 3557 (class 2606 OID 24581)
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- TOC entry 3577 (class 2606 OID 24776)
-- Name: telegram_auth_events telegram_auth_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_auth_events
    ADD CONSTRAINT telegram_auth_events_pkey PRIMARY KEY (id);


--
-- TOC entry 3572 (class 2606 OID 24756)
-- Name: telegram_auth_logs telegram_auth_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_auth_logs
    ADD CONSTRAINT telegram_auth_logs_pkey PRIMARY KEY (id);


--
-- TOC entry 3567 (class 2606 OID 24742)
-- Name: telegram_sessions telegram_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_sessions
    ADD CONSTRAINT telegram_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 3512 (class 2606 OID 16394)
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- TOC entry 3587 (class 2606 OID 24831)
-- Name: user_channel user_channel_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_channel
    ADD CONSTRAINT user_channel_pkey PRIMARY KEY (user_id, channel_id);


--
-- TOC entry 3608 (class 2606 OID 24902)
-- Name: user_group user_group_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_group
    ADD CONSTRAINT user_group_pkey PRIMARY KEY (user_id, group_id);


--
-- TOC entry 3521 (class 2606 OID 16405)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 3523 (class 2606 OID 16407)
-- Name: users users_telegram_id_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_telegram_id_key UNIQUE (telegram_id);


--
-- TOC entry 3621 (class 1259 OID 25008)
-- Name: idx_channel_mapping_new; Type: INDEX; Schema: _shadow; Owner: postgres
--

CREATE INDEX idx_channel_mapping_new ON _shadow.channel_mapping USING btree (new_channel_id);


--
-- TOC entry 3622 (class 1259 OID 25007)
-- Name: idx_channel_mapping_old; Type: INDEX; Schema: _shadow; Owner: postgres
--

CREATE INDEX idx_channel_mapping_old ON _shadow.channel_mapping USING btree (old_channel_id);


--
-- TOC entry 3623 (class 1259 OID 25012)
-- Name: idx_post_mapping_old; Type: INDEX; Schema: _shadow; Owner: postgres
--

CREATE INDEX idx_post_mapping_old ON _shadow.post_mapping USING btree (old_post_id);


--
-- TOC entry 3526 (class 1259 OID 16479)
-- Name: idx_channels_is_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_channels_is_active ON public.channels USING btree (is_active);


--
-- TOC entry 3527 (class 1259 OID 16480)
-- Name: idx_channels_last_message_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_channels_last_message_at ON public.channels USING btree (last_message_at);


--
-- TOC entry 3548 (class 1259 OID 16486)
-- Name: idx_indexing_status_embedding_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_indexing_status_embedding_status ON public.indexing_status USING btree (embedding_status);


--
-- TOC entry 3549 (class 1259 OID 16487)
-- Name: idx_indexing_status_graph_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_indexing_status_graph_status ON public.indexing_status USING btree (graph_status);


--
-- TOC entry 3550 (class 1259 OID 16485)
-- Name: idx_indexing_status_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_indexing_status_post_id ON public.indexing_status USING btree (post_id);


--
-- TOC entry 3551 (class 1259 OID 25354)
-- Name: idx_indexing_status_post_id_unique; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX idx_indexing_status_post_id_unique ON public.indexing_status USING btree (post_id);


--
-- TOC entry 3578 (class 1259 OID 24820)
-- Name: idx_invite_codes_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_invite_codes_active ON public.invite_codes USING btree (active);


--
-- TOC entry 3579 (class 1259 OID 24819)
-- Name: idx_invite_codes_tenant; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_invite_codes_tenant ON public.invite_codes USING btree (tenant_id);


--
-- TOC entry 3529 (class 1259 OID 16482)
-- Name: idx_posts_channel_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_posts_channel_id ON public.posts USING btree (channel_id);


--
-- TOC entry 3530 (class 1259 OID 16483)
-- Name: idx_posts_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_posts_created_at ON public.posts USING btree (created_at);


--
-- TOC entry 3531 (class 1259 OID 16484)
-- Name: idx_posts_is_processed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_posts_is_processed ON public.posts USING btree (is_processed);


--
-- TOC entry 3532 (class 1259 OID 25330)
-- Name: idx_posts_telegram_url; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_posts_telegram_url ON public.posts USING btree (telegram_post_url);


--
-- TOC entry 3513 (class 1259 OID 16476)
-- Name: idx_users_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_users_created_at ON public.users USING btree (created_at);


--
-- TOC entry 3514 (class 1259 OID 16475)
-- Name: idx_users_telegram_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_users_telegram_id ON public.users USING btree (telegram_id);


--
-- TOC entry 3515 (class 1259 OID 16474)
-- Name: idx_users_tenant_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_users_tenant_id ON public.users USING btree (tenant_id);


--
-- TOC entry 3618 (class 1259 OID 24966)
-- Name: ix_group_mentions_message; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_mentions_message ON public.group_mentions USING btree (group_message_id);


--
-- TOC entry 3619 (class 1259 OID 24967)
-- Name: ix_group_mentions_processed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_mentions_processed ON public.group_mentions USING btree (is_processed) WHERE (is_processed = false);


--
-- TOC entry 3620 (class 1259 OID 24965)
-- Name: ix_group_mentions_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_mentions_user ON public.group_mentions USING btree (mentioned_user_tg_id);


--
-- TOC entry 3613 (class 1259 OID 24963)
-- Name: ix_group_messages_posted; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_messages_posted ON public.group_messages USING btree (group_id, posted_at DESC);


--
-- TOC entry 3614 (class 1259 OID 24964)
-- Name: ix_group_messages_sender; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_group_messages_sender ON public.group_messages USING btree (sender_tg_id);


--
-- TOC entry 3603 (class 1259 OID 24959)
-- Name: ix_groups_active; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_groups_active ON public.groups USING btree (is_active) WHERE (is_active = true);


--
-- TOC entry 3604 (class 1259 OID 24958)
-- Name: ix_groups_tenant; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_groups_tenant ON public.groups USING btree (tenant_id);


--
-- TOC entry 3554 (class 1259 OID 25020)
-- Name: ix_indexing_status_embed; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_indexing_status_embed ON public.indexing_status USING btree (embedding_status);


--
-- TOC entry 3555 (class 1259 OID 25021)
-- Name: ix_indexing_status_graph; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_indexing_status_graph ON public.indexing_status USING btree (graph_status);


--
-- TOC entry 3588 (class 1259 OID 24953)
-- Name: ix_post_enrichment_enriched_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_enrichment_enriched_at ON public.post_enrichment USING btree (enriched_at DESC);


--
-- TOC entry 3589 (class 1259 OID 24952)
-- Name: ix_post_enrichment_vision_gin; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_enrichment_vision_gin ON public.post_enrichment USING gin (vision_labels);


--
-- TOC entry 3632 (class 1259 OID 25088)
-- Name: ix_post_forwards_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_forwards_created ON public.post_forwards USING btree (created_at DESC);


--
-- TOC entry 3633 (class 1259 OID 25087)
-- Name: ix_post_forwards_from_chat; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_forwards_from_chat ON public.post_forwards USING btree (post_id);


--
-- TOC entry 3634 (class 1259 OID 25086)
-- Name: ix_post_forwards_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_forwards_post_id ON public.post_forwards USING btree (post_id);


--
-- TOC entry 3593 (class 1259 OID 24954)
-- Name: ix_post_media_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_media_post_id ON public.post_media USING btree (post_id);


--
-- TOC entry 3594 (class 1259 OID 24957)
-- Name: ix_post_media_sha256; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_media_sha256 ON public.post_media USING btree (sha256) WHERE (sha256 IS NOT NULL);


--
-- TOC entry 3595 (class 1259 OID 24955)
-- Name: ix_post_media_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_media_type ON public.post_media USING btree (media_type);


--
-- TOC entry 3624 (class 1259 OID 25069)
-- Name: ix_post_reactions_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_created ON public.post_reactions USING btree (created_at DESC);


--
-- TOC entry 3625 (class 1259 OID 25065)
-- Name: ix_post_reactions_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_post_id ON public.post_reactions USING btree (post_id);


--
-- TOC entry 3626 (class 1259 OID 25066)
-- Name: ix_post_reactions_type; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_type ON public.post_reactions USING btree (reaction_type);


--
-- TOC entry 3627 (class 1259 OID 25068)
-- Name: ix_post_reactions_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_user ON public.post_reactions USING btree (user_tg_id) WHERE (user_tg_id IS NOT NULL);


--
-- TOC entry 3628 (class 1259 OID 25067)
-- Name: ix_post_reactions_value; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_reactions_value ON public.post_reactions USING btree (reaction_value);


--
-- TOC entry 3637 (class 1259 OID 25110)
-- Name: ix_post_replies_author; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_author ON public.post_replies USING btree (reply_author_tg_id) WHERE (reply_author_tg_id IS NOT NULL);


--
-- TOC entry 3638 (class 1259 OID 25108)
-- Name: ix_post_replies_post_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_post_id ON public.post_replies USING btree (post_id);


--
-- TOC entry 3639 (class 1259 OID 25111)
-- Name: ix_post_replies_posted; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_posted ON public.post_replies USING btree (reply_posted_at DESC);


--
-- TOC entry 3640 (class 1259 OID 25109)
-- Name: ix_post_replies_reply_to; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_post_replies_reply_to ON public.post_replies USING btree (reply_to_post_id);


--
-- TOC entry 3533 (class 1259 OID 25047)
-- Name: ix_posts_author; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_author ON public.posts USING btree (post_author) WHERE (post_author IS NOT NULL);


--
-- TOC entry 3534 (class 1259 OID 24968)
-- Name: ix_posts_channel_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_channel_id ON public.posts USING btree (channel_id);


--
-- TOC entry 3535 (class 1259 OID 24998)
-- Name: ix_posts_channel_posted; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_channel_posted ON public.posts USING btree (channel_id, posted_at DESC);


--
-- TOC entry 3536 (class 1259 OID 25046)
-- Name: ix_posts_edited; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_edited ON public.posts USING btree (is_edited, edited_at DESC) WHERE (is_edited = true);


--
-- TOC entry 3537 (class 1259 OID 25042)
-- Name: ix_posts_forwards_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_forwards_count ON public.posts USING btree (forwards_count DESC) WHERE (forwards_count > 0);


--
-- TOC entry 3538 (class 1259 OID 25048)
-- Name: ix_posts_metrics_update; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_metrics_update ON public.posts USING btree (last_metrics_update DESC);


--
-- TOC entry 3539 (class 1259 OID 25045)
-- Name: ix_posts_pinned; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_pinned ON public.posts USING btree (is_pinned) WHERE (is_pinned = true);


--
-- TOC entry 3540 (class 1259 OID 24997)
-- Name: ix_posts_posted_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_posted_at ON public.posts USING btree (posted_at DESC);


--
-- TOC entry 3541 (class 1259 OID 25043)
-- Name: ix_posts_reactions_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_reactions_count ON public.posts USING btree (reactions_count DESC) WHERE (reactions_count > 0);


--
-- TOC entry 3542 (class 1259 OID 25044)
-- Name: ix_posts_replies_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_replies_count ON public.posts USING btree (replies_count DESC) WHERE (replies_count > 0);


--
-- TOC entry 3543 (class 1259 OID 25041)
-- Name: ix_posts_views_count; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_views_count ON public.posts USING btree (views_count DESC) WHERE (views_count > 0);


--
-- TOC entry 3544 (class 1259 OID 24999)
-- Name: ix_posts_yyyymm; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_posts_yyyymm ON public.posts USING btree (yyyymm);


--
-- TOC entry 3573 (class 1259 OID 24784)
-- Name: ix_telegram_auth_events_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_events_at ON public.telegram_auth_events USING btree (at);


--
-- TOC entry 3574 (class 1259 OID 24783)
-- Name: ix_telegram_auth_events_event; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_events_event ON public.telegram_auth_events USING btree (event);


--
-- TOC entry 3575 (class 1259 OID 24782)
-- Name: ix_telegram_auth_events_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_events_user ON public.telegram_auth_events USING btree (user_id);


--
-- TOC entry 3568 (class 1259 OID 24759)
-- Name: ix_telegram_auth_logs_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_logs_at ON public.telegram_auth_logs USING btree (at);


--
-- TOC entry 3569 (class 1259 OID 24758)
-- Name: ix_telegram_auth_logs_event; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_logs_event ON public.telegram_auth_logs USING btree (event);


--
-- TOC entry 3570 (class 1259 OID 24757)
-- Name: ix_telegram_auth_logs_session; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_auth_logs_session ON public.telegram_auth_logs USING btree (session_id);


--
-- TOC entry 3562 (class 1259 OID 24746)
-- Name: ix_telegram_sessions_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_created ON public.telegram_sessions USING btree (created_at);


--
-- TOC entry 3563 (class 1259 OID 24744)
-- Name: ix_telegram_sessions_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_status ON public.telegram_sessions USING btree (status);


--
-- TOC entry 3564 (class 1259 OID 24743)
-- Name: ix_telegram_sessions_tenant; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_tenant ON public.telegram_sessions USING btree (tenant_id);


--
-- TOC entry 3565 (class 1259 OID 24745)
-- Name: ix_telegram_sessions_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_telegram_sessions_user ON public.telegram_sessions USING btree (user_id);


--
-- TOC entry 3584 (class 1259 OID 24950)
-- Name: ix_user_channel_channel; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_channel_channel ON public.user_channel USING btree (channel_id) WHERE (is_active = true);


--
-- TOC entry 3585 (class 1259 OID 24949)
-- Name: ix_user_channel_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_channel_user ON public.user_channel USING btree (user_id) WHERE (is_active = true);


--
-- TOC entry 3605 (class 1259 OID 24961)
-- Name: ix_user_group_group; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_group_group ON public.user_group USING btree (group_id) WHERE (is_active = true);


--
-- TOC entry 3606 (class 1259 OID 24960)
-- Name: ix_user_group_user; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_user_group_user ON public.user_group USING btree (user_id) WHERE (is_active = true);


--
-- TOC entry 3516 (class 1259 OID 24785)
-- Name: ix_users_first_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_first_name ON public.users USING btree (first_name);


--
-- TOC entry 3517 (class 1259 OID 24786)
-- Name: ix_users_last_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_last_name ON public.users USING btree (last_name);


--
-- TOC entry 3518 (class 1259 OID 24764)
-- Name: ix_users_telegram_auth_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_telegram_auth_created ON public.users USING btree (telegram_auth_created_at);


--
-- TOC entry 3519 (class 1259 OID 24763)
-- Name: ix_users_telegram_auth_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_telegram_auth_status ON public.users USING btree (telegram_auth_status);


--
-- TOC entry 3528 (class 1259 OID 25013)
-- Name: ux_channels_tg_global; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_channels_tg_global ON public.channels USING btree (tg_channel_id);


--
-- TOC entry 3615 (class 1259 OID 24962)
-- Name: ux_group_messages; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_group_messages ON public.group_messages USING btree (group_id, tg_message_id);


--
-- TOC entry 3592 (class 1259 OID 25377)
-- Name: ux_post_enrichment_post_kind; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_post_enrichment_post_kind ON public.post_enrichment USING btree (post_id, kind);


--
-- TOC entry 3598 (class 1259 OID 24956)
-- Name: ux_post_media_dedup; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_post_media_dedup ON public.post_media USING btree (post_id, COALESCE(tg_file_unique_id, tg_file_id));


--
-- TOC entry 3631 (class 1259 OID 25070)
-- Name: ux_post_reactions_unique; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_post_reactions_unique ON public.post_reactions USING btree (post_id, reaction_type, reaction_value, user_tg_id);


--
-- TOC entry 3547 (class 1259 OID 24970)
-- Name: ux_posts_chan_msg; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX ux_posts_chan_msg ON public.posts USING btree (channel_id, telegram_message_id);


--
-- TOC entry 3672 (class 2620 OID 24976)
-- Name: post_enrichment trg_pe_updated; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_pe_updated BEFORE UPDATE ON public.post_enrichment FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- TOC entry 3677 (class 2620 OID 25114)
-- Name: post_forwards trg_post_forwards_metrics; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_forwards_metrics AFTER INSERT OR DELETE OR UPDATE ON public.post_forwards FOR EACH ROW EXECUTE FUNCTION public.update_post_metrics();


--
-- TOC entry 3673 (class 2620 OID 24973)
-- Name: post_media trg_post_media_sync_ad; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_media_sync_ad AFTER DELETE ON public.post_media FOR EACH ROW EXECUTE FUNCTION public.sync_post_has_media();


--
-- TOC entry 3674 (class 2620 OID 24972)
-- Name: post_media trg_post_media_sync_ai; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_media_sync_ai AFTER INSERT ON public.post_media FOR EACH ROW EXECUTE FUNCTION public.sync_post_has_media();


--
-- TOC entry 3675 (class 2620 OID 24974)
-- Name: post_media trg_post_media_sync_au; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_media_sync_au AFTER UPDATE ON public.post_media FOR EACH ROW WHEN ((old.post_id IS DISTINCT FROM new.post_id)) EXECUTE FUNCTION public.sync_post_has_media();


--
-- TOC entry 3676 (class 2620 OID 25113)
-- Name: post_reactions trg_post_reactions_metrics; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_reactions_metrics AFTER INSERT OR DELETE OR UPDATE ON public.post_reactions FOR EACH ROW EXECUTE FUNCTION public.update_post_metrics();


--
-- TOC entry 3678 (class 2620 OID 25115)
-- Name: post_replies trg_post_replies_metrics; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_post_replies_metrics AFTER INSERT OR DELETE OR UPDATE ON public.post_replies FOR EACH ROW EXECUTE FUNCTION public.update_post_metrics();


--
-- TOC entry 3669 (class 2620 OID 25332)
-- Name: posts trg_posts_telegram_url; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_posts_telegram_url BEFORE INSERT OR UPDATE ON public.posts FOR EACH ROW EXECUTE FUNCTION public.update_telegram_post_url();


--
-- TOC entry 3670 (class 2620 OID 24996)
-- Name: posts trg_posts_update_yyyymm; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trg_posts_update_yyyymm BEFORE INSERT OR UPDATE ON public.posts FOR EACH ROW EXECUTE FUNCTION public.update_yyyymm();


--
-- TOC entry 3671 (class 2620 OID 24761)
-- Name: telegram_sessions trigger_telegram_sessions_updated_at; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trigger_telegram_sessions_updated_at BEFORE UPDATE ON public.telegram_sessions FOR EACH ROW EXECUTE FUNCTION public.update_telegram_sessions_updated_at();


--
-- TOC entry 3668 (class 2620 OID 24766)
-- Name: users trigger_users_telegram_auth_updated_at; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER trigger_users_telegram_auth_updated_at BEFORE UPDATE ON public.users FOR EACH ROW WHEN (((old.telegram_session_enc IS DISTINCT FROM new.telegram_session_enc) OR ((old.telegram_auth_status)::text IS DISTINCT FROM (new.telegram_auth_status)::text))) EXECUTE FUNCTION public.update_users_telegram_auth_updated_at();


--
-- TOC entry 3662 (class 2606 OID 24939)
-- Name: group_mentions group_mentions_group_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_mentions
    ADD CONSTRAINT group_mentions_group_message_id_fkey FOREIGN KEY (group_message_id) REFERENCES public.group_messages(id) ON DELETE CASCADE;


--
-- TOC entry 3663 (class 2606 OID 24944)
-- Name: group_mentions group_mentions_mentioned_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_mentions
    ADD CONSTRAINT group_mentions_mentioned_user_id_fkey FOREIGN KEY (mentioned_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 3661 (class 2606 OID 24924)
-- Name: group_messages group_messages_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;


--
-- TOC entry 3658 (class 2606 OID 24887)
-- Name: groups groups_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- TOC entry 3649 (class 2606 OID 16469)
-- Name: indexing_status indexing_status_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.indexing_status
    ADD CONSTRAINT indexing_status_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- TOC entry 3651 (class 2606 OID 24809)
-- Name: invite_codes invite_codes_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- TOC entry 3652 (class 2606 OID 24814)
-- Name: invite_codes invite_codes_last_used_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_last_used_by_fkey FOREIGN KEY (last_used_by) REFERENCES public.users(id);


--
-- TOC entry 3653 (class 2606 OID 24804)
-- Name: invite_codes invite_codes_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.invite_codes
    ADD CONSTRAINT invite_codes_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- TOC entry 3656 (class 2606 OID 24854)
-- Name: post_enrichment post_enrichment_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_enrichment
    ADD CONSTRAINT post_enrichment_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- TOC entry 3665 (class 2606 OID 25081)
-- Name: post_forwards post_forwards_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_forwards
    ADD CONSTRAINT post_forwards_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- TOC entry 3657 (class 2606 OID 24869)
-- Name: post_media post_media_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_media
    ADD CONSTRAINT post_media_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- TOC entry 3664 (class 2606 OID 25060)
-- Name: post_reactions post_reactions_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_reactions
    ADD CONSTRAINT post_reactions_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- TOC entry 3666 (class 2606 OID 25098)
-- Name: post_replies post_replies_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_replies
    ADD CONSTRAINT post_replies_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- TOC entry 3667 (class 2606 OID 25103)
-- Name: post_replies post_replies_reply_to_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.post_replies
    ADD CONSTRAINT post_replies_reply_to_post_id_fkey FOREIGN KEY (reply_to_post_id) REFERENCES public.posts(id) ON DELETE CASCADE;


--
-- TOC entry 3648 (class 2606 OID 16451)
-- Name: posts posts_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.posts
    ADD CONSTRAINT posts_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id) ON DELETE CASCADE;


--
-- TOC entry 3650 (class 2606 OID 24777)
-- Name: telegram_auth_events telegram_auth_events_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.telegram_auth_events
    ADD CONSTRAINT telegram_auth_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- TOC entry 3654 (class 2606 OID 24837)
-- Name: user_channel user_channel_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_channel
    ADD CONSTRAINT user_channel_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id) ON DELETE CASCADE;


--
-- TOC entry 3655 (class 2606 OID 24832)
-- Name: user_channel user_channel_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_channel
    ADD CONSTRAINT user_channel_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 3659 (class 2606 OID 24908)
-- Name: user_group user_group_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_group
    ADD CONSTRAINT user_group_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(id) ON DELETE CASCADE;


--
-- TOC entry 3660 (class 2606 OID 24903)
-- Name: user_group user_group_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.user_group
    ADD CONSTRAINT user_group_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 3647 (class 2606 OID 16408)
-- Name: users users_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- TOC entry 3837 (class 3256 OID 25016)
-- Name: post_enrichment enrichment_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY enrichment_by_subscription ON public.post_enrichment FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_enrichment.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- TOC entry 3831 (class 3256 OID 24989)
-- Name: post_enrichment enrichment_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY enrichment_worker_bypass ON public.post_enrichment TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3835 (class 3256 OID 24993)
-- Name: group_mentions group_mentions_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY group_mentions_worker_bypass ON public.group_mentions TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3834 (class 3256 OID 24992)
-- Name: group_messages group_messages_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY group_messages_worker_bypass ON public.group_messages TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3828 (class 3256 OID 24983)
-- Name: groups groups_by_user; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY groups_by_user ON public.groups FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM (public.users u
     JOIN public.user_group ug ON ((ug.user_id = u.id)))
  WHERE ((u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint) AND (u.tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid) AND (ug.group_id = groups.id) AND (ug.is_active = true)))));


--
-- TOC entry 3833 (class 3256 OID 24991)
-- Name: groups groups_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY groups_worker_bypass ON public.groups TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3841 (class 3256 OID 25119)
-- Name: post_forwards post_forwards_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_forwards_by_subscription ON public.post_forwards FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_forwards.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- TOC entry 3842 (class 3256 OID 25121)
-- Name: post_forwards post_forwards_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_forwards_worker_bypass ON public.post_forwards TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3838 (class 3256 OID 25018)
-- Name: post_media post_media_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_media_by_subscription ON public.post_media FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_media.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- TOC entry 3832 (class 3256 OID 24990)
-- Name: post_media post_media_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_media_worker_bypass ON public.post_media TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3839 (class 3256 OID 25116)
-- Name: post_reactions post_reactions_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_reactions_by_subscription ON public.post_reactions FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_reactions.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- TOC entry 3840 (class 3256 OID 25118)
-- Name: post_reactions post_reactions_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_reactions_worker_bypass ON public.post_reactions TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3843 (class 3256 OID 25122)
-- Name: post_replies post_replies_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_replies_by_subscription ON public.post_replies FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM ((public.posts p
     JOIN public.user_channel uc ON ((uc.channel_id = p.channel_id)))
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((p.id = post_replies.post_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- TOC entry 3844 (class 3256 OID 25124)
-- Name: post_replies post_replies_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY post_replies_worker_bypass ON public.post_replies TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3836 (class 3256 OID 25014)
-- Name: posts posts_by_subscription; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY posts_by_subscription ON public.posts FOR SELECT TO authenticated USING ((EXISTS ( SELECT 1
   FROM (public.user_channel uc
     JOIN public.users u ON ((u.id = uc.user_id)))
  WHERE ((uc.channel_id = posts.channel_id) AND (uc.is_active = true) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint)))));


--
-- TOC entry 3830 (class 3256 OID 24988)
-- Name: posts posts_worker_bypass; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY posts_worker_bypass ON public.posts TO worker_role USING (true) WITH CHECK (true);


--
-- TOC entry 3829 (class 3256 OID 24985)
-- Name: user_channel uc_write_own; Type: POLICY; Schema: public; Owner: postgres
--

CREATE POLICY uc_write_own ON public.user_channel TO authenticated USING ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = user_channel.user_id) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint) AND (u.tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid))))) WITH CHECK ((EXISTS ( SELECT 1
   FROM public.users u
  WHERE ((u.id = user_channel.user_id) AND (u.telegram_id = (current_setting('app.current_user_tg_id'::text, true))::bigint) AND (u.tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)))));


--
-- TOC entry 3878 (class 0 OID 0)
-- Dependencies: 5
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: pg_database_owner
--

GRANT USAGE ON SCHEMA public TO anon;
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT USAGE ON SCHEMA public TO service_role;


--
-- TOC entry 3879 (class 0 OID 0)
-- Dependencies: 8
-- Name: SCHEMA telegram_bot; Type: ACL; Schema: -; Owner: postgres
--

GRANT USAGE ON SCHEMA telegram_bot TO anon;
GRANT USAGE ON SCHEMA telegram_bot TO authenticated;
GRANT USAGE ON SCHEMA telegram_bot TO service_role;


--
-- TOC entry 3880 (class 0 OID 0)
-- Dependencies: 268
-- Name: FUNCTION generate_telegram_post_url(p_channel_username text, p_message_id bigint); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.generate_telegram_post_url(p_channel_username text, p_message_id bigint) TO anon;
GRANT ALL ON FUNCTION public.generate_telegram_post_url(p_channel_username text, p_message_id bigint) TO authenticated;
GRANT ALL ON FUNCTION public.generate_telegram_post_url(p_channel_username text, p_message_id bigint) TO service_role;


--
-- TOC entry 3881 (class 0 OID 0)
-- Dependencies: 267
-- Name: FUNCTION set_updated_at(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.set_updated_at() TO anon;
GRANT ALL ON FUNCTION public.set_updated_at() TO authenticated;
GRANT ALL ON FUNCTION public.set_updated_at() TO service_role;


--
-- TOC entry 3882 (class 0 OID 0)
-- Dependencies: 266
-- Name: FUNCTION sync_post_has_media(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.sync_post_has_media() TO anon;
GRANT ALL ON FUNCTION public.sync_post_has_media() TO authenticated;
GRANT ALL ON FUNCTION public.sync_post_has_media() TO service_role;


--
-- TOC entry 3883 (class 0 OID 0)
-- Dependencies: 271
-- Name: FUNCTION update_post_metrics(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_post_metrics() TO anon;
GRANT ALL ON FUNCTION public.update_post_metrics() TO authenticated;
GRANT ALL ON FUNCTION public.update_post_metrics() TO service_role;


--
-- TOC entry 3884 (class 0 OID 0)
-- Dependencies: 270
-- Name: FUNCTION update_telegram_post_url(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_telegram_post_url() TO anon;
GRANT ALL ON FUNCTION public.update_telegram_post_url() TO authenticated;
GRANT ALL ON FUNCTION public.update_telegram_post_url() TO service_role;


--
-- TOC entry 3885 (class 0 OID 0)
-- Dependencies: 264
-- Name: FUNCTION update_telegram_sessions_updated_at(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_telegram_sessions_updated_at() TO anon;
GRANT ALL ON FUNCTION public.update_telegram_sessions_updated_at() TO authenticated;
GRANT ALL ON FUNCTION public.update_telegram_sessions_updated_at() TO service_role;


--
-- TOC entry 3886 (class 0 OID 0)
-- Dependencies: 265
-- Name: FUNCTION update_users_telegram_auth_updated_at(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_users_telegram_auth_updated_at() TO anon;
GRANT ALL ON FUNCTION public.update_users_telegram_auth_updated_at() TO authenticated;
GRANT ALL ON FUNCTION public.update_users_telegram_auth_updated_at() TO service_role;


--
-- TOC entry 3887 (class 0 OID 0)
-- Dependencies: 269
-- Name: FUNCTION update_yyyymm(); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.update_yyyymm() TO anon;
GRANT ALL ON FUNCTION public.update_yyyymm() TO authenticated;
GRANT ALL ON FUNCTION public.update_yyyymm() TO service_role;


--
-- TOC entry 3888 (class 0 OID 0)
-- Dependencies: 283
-- Name: FUNCTION upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text); Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text) TO anon;
GRANT ALL ON FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text) TO authenticated;
GRANT ALL ON FUNCTION public.upsert_telegram_session(p_tenant_id character varying, p_user_id character varying, p_session_string_enc text, p_key_id character varying, p_status character varying, p_auth_error text, p_error_details text) TO service_role;


--
-- TOC entry 3889 (class 0 OID 0)
-- Dependencies: 241
-- Name: TABLE alembic_version; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.alembic_version TO anon;
GRANT ALL ON TABLE public.alembic_version TO authenticated;
GRANT ALL ON TABLE public.alembic_version TO service_role;


--
-- TOC entry 3891 (class 0 OID 0)
-- Dependencies: 234
-- Name: TABLE channels; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.channels TO anon;
GRANT ALL ON TABLE public.channels TO authenticated;
GRANT ALL ON TABLE public.channels TO service_role;


--
-- TOC entry 3893 (class 0 OID 0)
-- Dependencies: 242
-- Name: TABLE encryption_keys; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.encryption_keys TO anon;
GRANT ALL ON TABLE public.encryption_keys TO authenticated;
GRANT ALL ON TABLE public.encryption_keys TO service_role;


--
-- TOC entry 3895 (class 0 OID 0)
-- Dependencies: 253
-- Name: TABLE group_mentions; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.group_mentions TO anon;
GRANT ALL ON TABLE public.group_mentions TO authenticated;
GRANT ALL ON TABLE public.group_mentions TO service_role;


--
-- TOC entry 3897 (class 0 OID 0)
-- Dependencies: 252
-- Name: TABLE group_messages; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.group_messages TO anon;
GRANT ALL ON TABLE public.group_messages TO authenticated;
GRANT ALL ON TABLE public.group_messages TO service_role;


--
-- TOC entry 3899 (class 0 OID 0)
-- Dependencies: 250
-- Name: TABLE groups; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.groups TO anon;
GRANT ALL ON TABLE public.groups TO authenticated;
GRANT ALL ON TABLE public.groups TO service_role;


--
-- TOC entry 3900 (class 0 OID 0)
-- Dependencies: 236
-- Name: TABLE indexing_status; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.indexing_status TO anon;
GRANT ALL ON TABLE public.indexing_status TO authenticated;
GRANT ALL ON TABLE public.indexing_status TO service_role;


--
-- TOC entry 3901 (class 0 OID 0)
-- Dependencies: 246
-- Name: TABLE invite_codes; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.invite_codes TO anon;
GRANT ALL ON TABLE public.invite_codes TO authenticated;
GRANT ALL ON TABLE public.invite_codes TO service_role;


--
-- TOC entry 3902 (class 0 OID 0)
-- Dependencies: 261
-- Name: TABLE outbox_events; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.outbox_events TO anon;
GRANT ALL ON TABLE public.outbox_events TO authenticated;
GRANT ALL ON TABLE public.outbox_events TO service_role;


--
-- TOC entry 3904 (class 0 OID 0)
-- Dependencies: 248
-- Name: TABLE post_enrichment; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_enrichment TO anon;
GRANT ALL ON TABLE public.post_enrichment TO authenticated;
GRANT ALL ON TABLE public.post_enrichment TO service_role;


--
-- TOC entry 3906 (class 0 OID 0)
-- Dependencies: 259
-- Name: TABLE post_forwards; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_forwards TO anon;
GRANT ALL ON TABLE public.post_forwards TO authenticated;
GRANT ALL ON TABLE public.post_forwards TO service_role;


--
-- TOC entry 3908 (class 0 OID 0)
-- Dependencies: 249
-- Name: TABLE post_media; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_media TO anon;
GRANT ALL ON TABLE public.post_media TO authenticated;
GRANT ALL ON TABLE public.post_media TO service_role;


--
-- TOC entry 3910 (class 0 OID 0)
-- Dependencies: 258
-- Name: TABLE post_reactions; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_reactions TO anon;
GRANT ALL ON TABLE public.post_reactions TO authenticated;
GRANT ALL ON TABLE public.post_reactions TO service_role;


--
-- TOC entry 3912 (class 0 OID 0)
-- Dependencies: 260
-- Name: TABLE post_replies; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.post_replies TO anon;
GRANT ALL ON TABLE public.post_replies TO authenticated;
GRANT ALL ON TABLE public.post_replies TO service_role;


--
-- TOC entry 3924 (class 0 OID 0)
-- Dependencies: 235
-- Name: TABLE posts; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.posts TO anon;
GRANT ALL ON TABLE public.posts TO authenticated;
GRANT ALL ON TABLE public.posts TO service_role;


--
-- TOC entry 3925 (class 0 OID 0)
-- Dependencies: 263
-- Name: TABLE posts_legacy; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.posts_legacy TO anon;
GRANT ALL ON TABLE public.posts_legacy TO authenticated;
GRANT ALL ON TABLE public.posts_legacy TO service_role;


--
-- TOC entry 3926 (class 0 OID 0)
-- Dependencies: 262
-- Name: TABLE posts_with_telegram_links; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.posts_with_telegram_links TO anon;
GRANT ALL ON TABLE public.posts_with_telegram_links TO authenticated;
GRANT ALL ON TABLE public.posts_with_telegram_links TO service_role;


--
-- TOC entry 3927 (class 0 OID 0)
-- Dependencies: 237
-- Name: TABLE schema_migrations; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.schema_migrations TO anon;
GRANT ALL ON TABLE public.schema_migrations TO authenticated;
GRANT ALL ON TABLE public.schema_migrations TO service_role;


--
-- TOC entry 3929 (class 0 OID 0)
-- Dependencies: 245
-- Name: TABLE telegram_auth_events; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.telegram_auth_events TO anon;
GRANT ALL ON TABLE public.telegram_auth_events TO authenticated;
GRANT ALL ON TABLE public.telegram_auth_events TO service_role;


--
-- TOC entry 3931 (class 0 OID 0)
-- Dependencies: 244
-- Name: TABLE telegram_auth_logs; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.telegram_auth_logs TO anon;
GRANT ALL ON TABLE public.telegram_auth_logs TO authenticated;
GRANT ALL ON TABLE public.telegram_auth_logs TO service_role;


--
-- TOC entry 3936 (class 0 OID 0)
-- Dependencies: 243
-- Name: TABLE telegram_sessions; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.telegram_sessions TO anon;
GRANT ALL ON TABLE public.telegram_sessions TO authenticated;
GRANT ALL ON TABLE public.telegram_sessions TO service_role;


--
-- TOC entry 3937 (class 0 OID 0)
-- Dependencies: 232
-- Name: TABLE tenants; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.tenants TO anon;
GRANT ALL ON TABLE public.tenants TO authenticated;
GRANT ALL ON TABLE public.tenants TO service_role;


--
-- TOC entry 3939 (class 0 OID 0)
-- Dependencies: 247
-- Name: TABLE user_channel; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.user_channel TO anon;
GRANT ALL ON TABLE public.user_channel TO authenticated;
GRANT ALL ON TABLE public.user_channel TO service_role;


--
-- TOC entry 3941 (class 0 OID 0)
-- Dependencies: 251
-- Name: TABLE user_group; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.user_group TO anon;
GRANT ALL ON TABLE public.user_group TO authenticated;
GRANT ALL ON TABLE public.user_group TO service_role;


--
-- TOC entry 3948 (class 0 OID 0)
-- Dependencies: 233
-- Name: TABLE users; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.users TO anon;
GRANT ALL ON TABLE public.users TO authenticated;
GRANT ALL ON TABLE public.users TO service_role;


--
-- TOC entry 3949 (class 0 OID 0)
-- Dependencies: 240
-- Name: TABLE indexing_status; Type: ACL; Schema: telegram_bot; Owner: postgres
--

GRANT SELECT ON TABLE telegram_bot.indexing_status TO anon;
GRANT SELECT ON TABLE telegram_bot.indexing_status TO authenticated;
GRANT SELECT ON TABLE telegram_bot.indexing_status TO service_role;


--
-- TOC entry 3950 (class 0 OID 0)
-- Dependencies: 238
-- Name: TABLE tenants; Type: ACL; Schema: telegram_bot; Owner: postgres
--

GRANT SELECT ON TABLE telegram_bot.tenants TO anon;
GRANT SELECT ON TABLE telegram_bot.tenants TO authenticated;
GRANT SELECT ON TABLE telegram_bot.tenants TO service_role;


--
-- TOC entry 3951 (class 0 OID 0)
-- Dependencies: 239
-- Name: TABLE users; Type: ACL; Schema: telegram_bot; Owner: postgres
--

GRANT SELECT ON TABLE telegram_bot.users TO anon;
GRANT SELECT ON TABLE telegram_bot.users TO authenticated;
GRANT SELECT ON TABLE telegram_bot.users TO service_role;


--
-- TOC entry 2186 (class 826 OID 24587)
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES  TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES  TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES  TO service_role;


--
-- TOC entry 2187 (class 826 OID 24588)
-- Name: DEFAULT PRIVILEGES FOR FUNCTIONS; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS  TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS  TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON FUNCTIONS  TO service_role;


--
-- TOC entry 2185 (class 826 OID 24586)
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES  TO anon;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES  TO authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES  TO service_role;


-- Completed on 2025-10-26 18:32:36 UTC

--
-- PostgreSQL database dump complete
--

