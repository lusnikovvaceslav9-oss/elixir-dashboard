-- Elixir Dashboard core storage: projects[] + _csv_uploads + _worker.
-- Replaces the JSONBin bin the dashboard used to read/write directly from
-- the browser. Runs in the SAME Supabase project as scripts/library/schema.sql
-- (no need for a second project) — just a separate table.
--
-- Schema mirrors JSONBin on purpose: one JSON blob per record, no fixed
-- columns for project fields. Projects gain new ad-hoc fields often enough
-- that a rigid schema would mean a migration every time; `data jsonb` keeps
-- the same flexibility JSONBin already provided.

create extension if not exists pgcrypto;

create table if not exists dashboard_records (
  id text primary key,
  data jsonb not null,
  updated_at timestamptz not null default now()
);

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_updated_at on dashboard_records;
create trigger set_updated_at before update on dashboard_records
  for each row execute function set_updated_at();

-- RLS stays disabled: only reached through the Cloudflare Worker's
-- service-role key (worker/dashboard.js), never directly from the browser.
