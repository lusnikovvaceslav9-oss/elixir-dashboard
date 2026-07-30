-- Elixir Library — migration 003: FB account money movement + payments ledger.
-- Run once in the same Supabase project's SQL Editor, after migrate_002.sql.

alter table fb_accounts add column if not exists contractor_id uuid references contractors(id) on delete set null;
alter table fb_accounts add column if not exists deposit numeric;
alter table fb_accounts add column if not exists spend_total numeric;
alter table fb_accounts add column if not exists balance numeric;
alter table fb_accounts add column if not exists buyer text;
alter table fb_accounts add column if not exists farm_profile text;
alter table fb_accounts add column if not exists profile_owner text;
alter table fb_accounts add column if not exists profile_url text;

-- Top-up / funding ledger — money moved to a contractor to fund their pool of
-- agency ad accounts. Separate from fb_accounts.deposit (per-account funding)
-- since one payment can fund many accounts.
create table if not exists payments (
  id uuid primary key default gen_random_uuid(),
  contractor_id uuid references contractors(id) on delete set null,
  date date,
  reference text,
  amount numeric,
  commission_pct numeric,
  commission_amount numeric,
  net_amount numeric,
  comment text,
  sort_order integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists payments_contractor_id_idx on payments (contractor_id);
drop trigger if exists set_updated_at on payments;
create trigger set_updated_at before update on payments
  for each row execute function set_updated_at();
