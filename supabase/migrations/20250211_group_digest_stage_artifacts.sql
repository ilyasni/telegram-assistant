-- Migration: group digest stage artifacts (Context7 idempotency store)
-- Created: 2025-02-11

create table if not exists public.group_digest_stage_artifacts (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid,
    group_id uuid,
    window_id uuid not null,
    stage text not null,
    schema_version text not null,
    prompt_id text,
    prompt_version text,
    model_id text,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists ux_digest_stage_artifacts_stage
    on public.group_digest_stage_artifacts (tenant_id, group_id, window_id, stage, schema_version);

create index if not exists idx_digest_stage_window
    on public.group_digest_stage_artifacts (window_id);

create index if not exists idx_digest_stage_tenant_group
    on public.group_digest_stage_artifacts (tenant_id, group_id);

alter table public.group_digest_stage_artifacts enable row level security;

do $$
begin
    if not exists (
        select 1
        from pg_policies
        where schemaname = 'public'
          and tablename = 'group_digest_stage_artifacts'
          and policyname = 'stage_artifacts_tenant_isolation'
    ) then
        create policy "stage_artifacts_tenant_isolation"
            on public.group_digest_stage_artifacts
            using (
                coalesce(tenant_id::text, '') = coalesce(current_setting('app.current_tenant', true), '')
            );
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1
        from pg_policies
        where schemaname = 'public'
          and tablename = 'group_digest_stage_artifacts'
          and policyname = 'stage_artifacts_insert_worker'
    ) then
        create policy "stage_artifacts_insert_worker"
            on public.group_digest_stage_artifacts
            for insert
            with check (
                coalesce(tenant_id::text, '') = coalesce(current_setting('app.current_tenant', true), '')
            );
    end if;
end
$$;

-- обновление updated_at
create or replace function public.trigger_set_timestamp()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

do $$
begin
    if not exists (
        select 1 from pg_trigger
        where tgname = 'set_timestamp_group_digest_stage_artifacts'
    ) then
        create trigger set_timestamp_group_digest_stage_artifacts
            before update on public.group_digest_stage_artifacts
            for each row execute procedure trigger_set_timestamp();
    end if;
end
$$;

