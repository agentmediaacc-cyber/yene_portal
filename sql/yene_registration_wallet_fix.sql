-- YENE registration and wallet approval fix
-- Safe additive migration only.

alter table if exists public.drivers
  add column if not exists region text,
  add column if not exists external_code text,
  add column if not exists approval_status text default 'pending_approval',
  add column if not exists approved_at timestamptz,
  add column if not exists approved_by text;

alter table if exists public.clients
  add column if not exists region text,
  add column if not exists external_code text,
  add column if not exists approval_status text default 'pending_approval',
  add column if not exists approved_at timestamptz,
  add column if not exists approved_by text;

alter table if exists public.agent_profiles
  add column if not exists referral_code text;

alter table if exists public.wallet_ledger
  add column if not exists source_type text,
  add column if not exists source_id text,
  add column if not exists status text default 'posted';

create unique index if not exists idx_wallet_ledger_source_once
  on public.wallet_ledger(source_type, source_id)
  where source_id is not null;
