-- YENE upgrade phase 2
-- Additive schema support for homepage, agent account completion, account help,
-- remote jobs, group notices, approval metadata, payout controls, and team links.

create extension if not exists pgcrypto;

alter table if exists agent_profiles
  add column if not exists profile_picture_url text,
  add column if not exists profile_photo_url text,
  add column if not exists residential_address text,
  add column if not exists operation_region text,
  add column if not exists date_of_birth date,
  add column if not exists id_number text,
  add column if not exists next_of_kin_name text,
  add column if not exists next_of_kin_phone text,
  add column if not exists bio text,
  add column if not exists about text,
  add column if not exists must_change_password boolean default false,
  add column if not exists temp_password text,
  add column if not exists last_reset_at timestamptz,
  add column if not exists reset_by_admin text,
  add column if not exists approved_at timestamptz,
  add column if not exists rejected_at timestamptz,
  add column if not exists approval_status text,
  add column if not exists rejection_reason text,
  add column if not exists team_leader_id uuid,
  add column if not exists team_leader_name text,
  add column if not exists referred_by uuid,
  add column if not exists referred_by_code text,
  add column if not exists referral_code text,
  add column if not exists payout_method text,
  add column if not exists payout_account text;

alter table if exists agents
  add column if not exists auth_id uuid,
  add column if not exists must_change_password boolean default false,
  add column if not exists temp_password text,
  add column if not exists last_reset_at timestamptz,
  add column if not exists reset_by_admin text,
  add column if not exists approval_status text,
  add column if not exists rejection_reason text;

alter table if exists drivers
  add column if not exists phone text,
  add column if not exists par_number text,
  add column if not exists external_code text,
  add column if not exists recruiter_agent_id uuid,
  add column if not exists recruiter_auth_id uuid,
  add column if not exists recruiter_email text,
  add column if not exists recruiter_name text,
  add column if not exists approved_at timestamptz,
  add column if not exists rejected_at timestamptz,
  add column if not exists approval_status text,
  add column if not exists rejection_reason text,
  add column if not exists admin_approved boolean default false,
  add column if not exists payout_excluded boolean default false,
  add column if not exists payout_note text;

alter table if exists clients
  add column if not exists phone text,
  add column if not exists customer_code text,
  add column if not exists external_code text,
  add column if not exists recruiter_agent_id uuid,
  add column if not exists recruiter_auth_id uuid,
  add column if not exists recruiter_email text,
  add column if not exists recruiter_name text,
  add column if not exists approved_at timestamptz,
  add column if not exists rejected_at timestamptz,
  add column if not exists approval_status text,
  add column if not exists rejection_reason text,
  add column if not exists admin_approved boolean default false,
  add column if not exists payout_excluded boolean default false,
  add column if not exists payout_note text;

create table if not exists remote_jobs (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text not null,
  town text,
  region text,
  target_count integer default 0,
  status text default 'ACTIVE',
  created_by text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists agent_group_messages (
  id uuid primary key default gen_random_uuid(),
  author_id uuid,
  author_email text,
  author_name text default 'YENE Admin',
  title text,
  message text not null,
  audience text default 'agents',
  status text default 'ACTIVE',
  created_at timestamptz default now()
);

create table if not exists agent_messages (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid,
  agent_auth_id uuid,
  agent_email text,
  agent_name text,
  registration_type text,
  registration_id uuid,
  subject text,
  message text not null,
  status text default 'unread',
  read_at timestamptz,
  created_at timestamptz default now()
);

create table if not exists agent_wallet_ledger (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid,
  agent_auth_id uuid,
  agent_email text,
  agent_name text,
  entry_type text not null default 'credit',
  amount numeric(12,2) not null default 0,
  reference text unique,
  note text,
  status text default 'approved',
  week_start date,
  week_end date,
  created_at timestamptz default now()
);

create table if not exists payment_rules (
  id uuid primary key default gen_random_uuid(),
  region text,
  town text,
  driver_reg numeric(12,2) default 0,
  client_reg numeric(12,2) default 0,
  daily_5_drivers_bonus numeric(12,2) default 0,
  daily_5_clients_bonus numeric(12,2) default 0,
  weekly_30_activations_bonus numeric(12,2) default 0,
  first_trip_bonus numeric(12,2) default 0,
  status text default 'Active',
  updated_at timestamptz default now()
);

create table if not exists weekly_payment_settings (
  id uuid primary key default gen_random_uuid(),
  region text,
  town text,
  driver_register_amount numeric(12,2) default 0,
  client_register_amount numeric(12,2) default 0,
  daily_5_drivers_bonus numeric(12,2) default 0,
  daily_5_clients_bonus numeric(12,2) default 0,
  weekly_30_activations_bonus numeric(12,2) default 0,
  first_trip_bonus numeric(12,2) default 0,
  status text default 'Active',
  updated_at timestamptz default now()
);

create index if not exists idx_drivers_recruiter_agent_id on drivers(recruiter_agent_id);
create index if not exists idx_clients_recruiter_agent_id on clients(recruiter_agent_id);
create index if not exists idx_agent_profiles_email on agent_profiles(email);
create index if not exists idx_remote_jobs_status on remote_jobs(status);
create index if not exists idx_agent_group_messages_created on agent_group_messages(created_at desc);
create index if not exists idx_agent_wallet_ledger_agent_id on agent_wallet_ledger(agent_id);

-- Supabase Storage requirement:
-- Create a public bucket named agent-profile-photos for browser-captured agent photos.
-- In Supabase Dashboard: Storage -> New bucket -> agent-profile-photos -> Public.
-- If your project uses private buckets, create signed URL policies and adjust
-- /api/agent/profile_photo_v4 accordingly.
