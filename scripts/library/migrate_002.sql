-- Elixir Library — migration 002: ad_network + fb_accounts extra fields + manual sort order.
-- Run once in the same Supabase project's SQL Editor, after schema.sql.

alter table contractors add column if not exists ad_network text;

alter table fb_accounts add column if not exists bm_id text;
alter table fb_accounts add column if not exists profile_number text;

alter table fb_accounts add column if not exists sort_order integer;
alter table pixels add column if not exists sort_order integer;
alter table creatives add column if not exists sort_order integer;
alter table insights add column if not exists sort_order integer;
alter table contractors add column if not exists sort_order integer;

-- Seed sort_order from creation order so existing rows get a stable initial position.
with ranked as (
  select id, row_number() over (order by created_at) as rn from fb_accounts
)
update fb_accounts set sort_order = ranked.rn from ranked where fb_accounts.id = ranked.id and fb_accounts.sort_order is null;

with ranked as (
  select id, row_number() over (order by created_at) as rn from pixels
)
update pixels set sort_order = ranked.rn from ranked where pixels.id = ranked.id and pixels.sort_order is null;

with ranked as (
  select id, row_number() over (order by created_at) as rn from creatives
)
update creatives set sort_order = ranked.rn from ranked where creatives.id = ranked.id and creatives.sort_order is null;

with ranked as (
  select id, row_number() over (order by created_at) as rn from insights
)
update insights set sort_order = ranked.rn from ranked where insights.id = ranked.id and insights.sort_order is null;

with ranked as (
  select id, row_number() over (order by created_at) as rn from contractors
)
update contractors set sort_order = ranked.rn from ranked where contractors.id = ranked.id and contractors.sort_order is null;
