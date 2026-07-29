-- Elixir Library: FB ad accounts / pixels / creatives / insights.
-- Run this once in the SQL editor of a dedicated Supabase project
-- (not the same project used by scripts/buyer-feed/ for Planto billing data).
--
-- No secrets are stored here by design — fb_accounts holds only
-- id/status/limits/notes, never logins/passwords/access tokens.

create extension if not exists pgcrypto;

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists fb_accounts (
  id uuid primary key default gen_random_uuid(),
  project_id text,
  fb_account_id text,
  name text,
  bm_id text,
  profile_number text,
  status text not null default 'active' check (status in ('active', 'banned', 'restricted', 'warming', 'other')),
  spend_limit numeric,
  owner text,
  notes text,
  sort_order integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists fb_accounts_project_id_idx on fb_accounts (project_id);
drop trigger if exists set_updated_at on fb_accounts;
create trigger set_updated_at before update on fb_accounts
  for each row execute function set_updated_at();

create table if not exists pixels (
  id uuid primary key default gen_random_uuid(),
  project_id text,
  pixel_id text,
  fb_account_id uuid references fb_accounts(id) on delete set null,
  domain text,
  status text not null default 'active' check (status in ('active', 'issue', 'unverified', 'other')),
  notes text,
  sort_order integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists pixels_project_id_idx on pixels (project_id);
create index if not exists pixels_fb_account_id_idx on pixels (fb_account_id);
drop trigger if exists set_updated_at on pixels;
create trigger set_updated_at before update on pixels
  for each row execute function set_updated_at();

create table if not exists creatives (
  id uuid primary key default gen_random_uuid(),
  project_id text,
  title text,
  link text not null,
  format text not null default 'other' check (format in ('image', 'video', 'carousel', 'other')),
  status text not null default 'testing' check (status in ('live', 'stopped', 'testing', 'archived')),
  tags text[] not null default '{}',
  notes text,
  sort_order integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists creatives_project_id_idx on creatives (project_id);
create index if not exists creatives_tags_idx on creatives using gin (tags);
drop trigger if exists set_updated_at on creatives;
create trigger set_updated_at before update on creatives
  for each row execute function set_updated_at();

create table if not exists insights (
  id uuid primary key default gen_random_uuid(),
  project_id text,
  title text,
  body text,
  tags text[] not null default '{}',
  author text,
  sort_order integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists insights_project_id_idx on insights (project_id);
create index if not exists insights_tags_idx on insights using gin (tags);
drop trigger if exists set_updated_at on insights;
create trigger set_updated_at before update on insights
  for each row execute function set_updated_at();

-- Contractors: people/teams agency ad accounts are bought from. Not tied to
-- a single dashboard project (a supplier can serve several). custom_fields
-- lets the UI add arbitrary extra key/value pairs beyond the fixed columns.
create table if not exists contractors (
  id uuid primary key default gen_random_uuid(),
  name text,
  contact text,
  rate text,
  ad_network text,
  payment_requisites text,
  terms text,
  status text not null default 'active' check (status in ('active', 'paused', 'inactive')),
  custom_fields jsonb not null default '{}'::jsonb,
  sort_order integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
drop trigger if exists set_updated_at on contractors;
create trigger set_updated_at before update on contractors
  for each row execute function set_updated_at();

-- RLS stays disabled: these tables are only ever reached through the
-- Cloudflare Worker's service-role key (worker/library.js), never
-- directly from the browser. If that changes, enable RLS + policies.
