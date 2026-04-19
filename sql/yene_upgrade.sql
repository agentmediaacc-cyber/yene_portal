-- YENE platform upgrade foundations.
-- Safe to run more than once in Supabase SQL Editor.

create table if not exists public.remote_jobs (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text not null,
  town text,
  region text,
  target_count integer not null default 0,
  status text not null default 'ACTIVE',
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_remote_jobs_status_created
  on public.remote_jobs (status, created_at desc);

create table if not exists public.agent_group_messages (
  id uuid primary key default gen_random_uuid(),
  author_agent_id text,
  author_name text,
  author_email text,
  profile_picture_url text,
  title text,
  message text not null,
  audience text not null default 'agents',
  status text not null default 'ACTIVE',
  created_at timestamptz not null default now()
);

create index if not exists idx_agent_group_messages_created
  on public.agent_group_messages (created_at desc);

alter table if exists public.agent_profiles
  add column if not exists must_change_password boolean not null default false,
  add column if not exists temp_password text,
  add column if not exists last_reset_at timestamptz,
  add column if not exists reset_by_admin text,
  add column if not exists residential_address text,
  add column if not exists profile_picture_url text,
  add column if not exists payment_method text,
  add column if not exists payment_account text,
  add column if not exists referral_code text,
  add column if not exists team_leader_id text,
  add column if not exists team_leader_name text,
  add column if not exists referred_by text,
  add column if not exists referred_by_code text;

alter table if exists public.drivers
  add column if not exists approval_state text,
  add column if not exists approved_at timestamptz,
  add column if not exists rejected_at timestamptz,
  add column if not exists rejection_reason text,
  add column if not exists admin_approved boolean,
  add column if not exists par_number text,
  add column if not exists driver_code text;

alter table if exists public.clients
  add column if not exists approval_state text,
  add column if not exists approved_at timestamptz,
  add column if not exists rejected_at timestamptz,
  add column if not exists rejection_reason text,
  add column if not exists admin_approved boolean,
  add column if not exists customer_code text,
  add column if not exists client_code text;

alter table if exists public.agent_withdraw_requests
  add column if not exists payment_method text,
  add column if not exists payment_account text;

alter table if exists public.weekly_payment_settings
  add column if not exists daily_5_drivers_bonus numeric default 0,
  add column if not exists daily_5_clients_bonus numeric default 0,
  add column if not exists weekly_30_activations_bonus numeric default 0,
  add column if not exists first_trip_bonus numeric default 0;

alter table if exists public.payment_rules
  add column if not exists daily_5_drivers_bonus numeric default 0,
  add column if not exists daily_5_clients_bonus numeric default 0,
  add column if not exists weekly_30_activations_bonus numeric default 0,
  add column if not exists first_trip_bonus numeric default 0;
