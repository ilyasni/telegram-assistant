-- Create telegram_bot schema and expose public tables via views
CREATE SCHEMA IF NOT EXISTS telegram_bot;

-- Grants
GRANT USAGE ON SCHEMA telegram_bot TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA telegram_bot
  GRANT SELECT ON TABLES TO anon, authenticated, service_role;

-- Views mapping to public schema
CREATE OR REPLACE VIEW telegram_bot.tenants AS
  SELECT * FROM public.tenants;

CREATE OR REPLACE VIEW telegram_bot.users AS
  SELECT * FROM public.users;

CREATE OR REPLACE VIEW telegram_bot.channels AS
  SELECT * FROM public.channels;

CREATE OR REPLACE VIEW telegram_bot.posts AS
  SELECT * FROM public.posts;

CREATE OR REPLACE VIEW telegram_bot.indexing_status AS
  SELECT * FROM public.indexing_status;


