create table if not exists public.region_access_settings (
  id text primary key,
  region text,
  town text,
  is_active boolean default true,
  registration_open boolean default true,
  allow_driver_registration boolean default true,
  allow_client_registration boolean default true,
  allow_agent_login boolean default true,
  allow_agent_activation boolean default true,
  locked_by text,
  updated_by text,
  updated_at timestamptz default timezone('utc', now()),
  created_at timestamptz default timezone('utc', now()),
  note text
);

alter table public.agent_profiles add column if not exists region_locked boolean default false;
alter table public.agent_profiles add column if not exists region_locked_at timestamptz;
alter table public.agent_profiles add column if not exists region_locked_by text;
alter table public.agent_profiles add column if not exists current_working_town text;
alter table public.agent_profiles add column if not exists operation_region text;
alter table public.agent_profiles add column if not exists account_status text default 'active';
alter table public.agent_profiles add column if not exists login_allowed boolean default true;
alter table public.agent_profiles add column if not exists registration_allowed boolean default true;
alter table public.agent_profiles add column if not exists allow_driver_registration boolean default true;
alter table public.agent_profiles add column if not exists allow_client_registration boolean default true;
alter table public.agent_profiles add column if not exists access_note text;
alter table public.agent_profiles add column if not exists access_updated_by text;
alter table public.agent_profiles add column if not exists access_updated_at timestamptz;

alter table public.drivers add column if not exists approval_status text default 'pending_approval';
alter table public.drivers add column if not exists approved_at timestamptz;
alter table public.drivers add column if not exists approved_by text;
alter table public.drivers add column if not exists normalized_phone text;

alter table public.clients add column if not exists approval_status text default 'pending_approval';
alter table public.clients add column if not exists approved_at timestamptz;
alter table public.clients add column if not exists approved_by text;
alter table public.clients add column if not exists normalized_phone text;

alter table public.wallet_ledger add column if not exists source_type text;
alter table public.wallet_ledger add column if not exists source_id text;
alter table public.wallet_ledger add column if not exists status text default 'posted';
alter table public.wallet_ledger add column if not exists description text;
alter table public.wallet_ledger add column if not exists approved_by text;
alter table public.wallet_ledger add column if not exists payment_rule_id text;
alter table public.wallet_ledger add column if not exists statement_number text;

alter table public.agent_wallet_ledger add column if not exists source_type text;
alter table public.agent_wallet_ledger add column if not exists source_id text;
alter table public.agent_wallet_ledger add column if not exists status text default 'posted';
alter table public.agent_wallet_ledger add column if not exists description text;
alter table public.agent_wallet_ledger add column if not exists approved_by text;
alter table public.agent_wallet_ledger add column if not exists payment_rule_id text;
alter table public.agent_wallet_ledger add column if not exists statement_number text;

create index if not exists idx_region_access_settings_region_town
  on public.region_access_settings (region, town);
create index if not exists idx_agent_profiles_region_lock
  on public.agent_profiles (region_locked, region, town);
create index if not exists idx_agent_profiles_account_status
  on public.agent_profiles (account_status);
create index if not exists idx_agent_profiles_login_allowed
  on public.agent_profiles (login_allowed);
create index if not exists idx_drivers_approval_status
  on public.drivers (approval_status, recruiter_agent_id, region, town);
create index if not exists idx_clients_approval_status
  on public.clients (approval_status, recruiter_agent_id, region, town);
create index if not exists idx_wallet_ledger_source
  on public.wallet_ledger (source_type, source_id);
create index if not exists idx_agent_wallet_ledger_source
  on public.agent_wallet_ledger (source_type, source_id);
