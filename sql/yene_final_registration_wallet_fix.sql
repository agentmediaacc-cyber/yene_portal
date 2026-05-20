-- YENE final registration and wallet fix
-- Safe additive changes only.

alter table if exists public.drivers
  add column if not exists normalized_phone text,
  add column if not exists external_code text,
  add column if not exists region text,
  add column if not exists approval_status text default 'pending_approval',
  add column if not exists approved_at timestamptz,
  add column if not exists approved_by text;

alter table if exists public.clients
  add column if not exists normalized_phone text,
  add column if not exists external_code text,
  add column if not exists region text,
  add column if not exists approval_status text default 'pending_approval',
  add column if not exists approved_at timestamptz,
  add column if not exists approved_by text;

alter table if exists public.wallet_ledger
  add column if not exists source_type text,
  add column if not exists source_id text,
  add column if not exists status text default 'posted',
  add column if not exists description text;

alter table if exists public.agent_wallet_ledger
  add column if not exists source_type text,
  add column if not exists source_id text,
  add column if not exists status text default 'posted',
  add column if not exists description text;

create index if not exists idx_drivers_external_code on public.drivers(external_code);
create index if not exists idx_clients_external_code on public.clients(external_code);
create index if not exists idx_drivers_normalized_phone on public.drivers(normalized_phone);
create index if not exists idx_clients_normalized_phone on public.clients(normalized_phone);
create index if not exists idx_drivers_recruiter_agent_id on public.drivers(recruiter_agent_id);
create index if not exists idx_clients_recruiter_agent_id on public.clients(recruiter_agent_id);
create index if not exists idx_drivers_recruiter_auth_id on public.drivers(recruiter_auth_id);
create index if not exists idx_clients_recruiter_auth_id on public.clients(recruiter_auth_id);
create index if not exists idx_drivers_recruiter_email on public.drivers(recruiter_email);
create index if not exists idx_clients_recruiter_email on public.clients(recruiter_email);
create index if not exists idx_agent_wallet_ledger_source_type_id on public.agent_wallet_ledger(source_type, source_id);
create index if not exists idx_wallet_ledger_source_type_id on public.wallet_ledger(source_type, source_id);
