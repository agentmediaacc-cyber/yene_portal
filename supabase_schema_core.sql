-- =========================
-- CORE PORTAL TABLES (V1)
-- =========================

-- Agent profiles (you already have this table; we add fields if missing)
alter table if exists public.agent_profiles
  add column if not exists auth_id uuid,
  add column if not exists role text default 'AGENT',
  add column if not exists referral_code text,
  add column if not exists referred_by uuid,
  add column if not exists town text,
  add column if not exists region text,
  add column if not exists status text default 'ACTIVE';

create unique index if not exists agent_profiles_auth_id_uq on public.agent_profiles(auth_id);
create unique index if not exists agent_profiles_referral_code_uq on public.agent_profiles(referral_code);

-- Registrations table: every driver/client registered by an agent
create table if not exists public.agent_registrations (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_auth_id uuid not null,
  subject_type text not null check (subject_type in ('driver','client')),
  full_name text not null,
  phone text not null,
  town text,
  external_code text, -- driver code from app / client code
  notes text
);

create index if not exists agent_registrations_agent_idx on public.agent_registrations(agent_auth_id, created_at desc);
create index if not exists agent_registrations_type_idx on public.agent_registrations(subject_type, created_at desc);

-- Weekly invoices (Mon-Sun)
create table if not exists public.agent_weekly_invoices (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  week_start date not null,
  week_end date not null,
  agent_auth_id uuid not null,
  drivers_registered int not null default 0,
  clients_registered int not null default 0,
  amount numeric not null default 0,
  status text not null default 'PENDING', -- PENDING/APPROVED/PAID
  admin_note text
);

create index if not exists agent_weekly_invoices_agent_week_idx on public.agent_weekly_invoices(agent_auth_id, week_start desc);

-- Wallet ledger (admin controlled payouts + adjustments)
create table if not exists public.agent_wallet_ledger (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_auth_id uuid not null,
  entry_type text not null check (entry_type in ('credit','debit')),
  amount numeric not null,
  reference text,
  note text
);

create index if not exists agent_wallet_ledger_agent_idx on public.agent_wallet_ledger(agent_auth_id, created_at desc);

-- =========================
-- RLS POLICIES (READ OWN)
-- =========================
alter table public.agent_profiles enable row level security;
alter table public.agent_registrations enable row level security;
alter table public.agent_weekly_invoices enable row level security;
alter table public.agent_wallet_ledger enable row level security;

drop policy if exists "agents_read_own_profile" on public.agent_profiles;
create policy "agents_read_own_profile"
on public.agent_profiles
for select
to authenticated
using (auth_id = auth.uid());

drop policy if exists "agents_read_own_regs" on public.agent_registrations;
create policy "agents_read_own_regs"
on public.agent_registrations
for select
to authenticated
using (agent_auth_id = auth.uid());

drop policy if exists "agents_read_own_invoices" on public.agent_weekly_invoices;
create policy "agents_read_own_invoices"
on public.agent_weekly_invoices
for select
to authenticated
using (agent_auth_id = auth.uid());

drop policy if exists "agents_read_own_wallet" on public.agent_wallet_ledger;
create policy "agents_read_own_wallet"
on public.agent_wallet_ledger
for select
to authenticated
using (agent_auth_id = auth.uid());

-- NOTE:
-- We will NOT allow browser inserts to registrations (to avoid abuse).
-- Server inserts via SERVICE_ROLE key will bypass RLS.
