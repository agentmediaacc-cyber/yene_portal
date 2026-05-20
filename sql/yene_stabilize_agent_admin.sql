-- YENE Stabilization Migration
-- Safe to run multiple times in Supabase SQL Editor

-- Ensure agent_profiles has all needed fields
alter table public.agent_profiles
  add column if not exists current_working_town text,
  add column if not exists current_location text,
  add column if not exists status_message text,
  add column if not exists last_active_at timestamptz default now();

-- Ensure drivers has region
alter table public.drivers
  add column if not exists region text;

-- Ensure clients has region
alter table public.clients
  add column if not exists region text;

-- Create agent_registrations index for faster dashboard loading if not exists
create index if not exists idx_drivers_recruiter_agent_id on public.drivers(recruiter_agent_id);
create index if not exists idx_clients_recruiter_agent_id on public.clients(recruiter_agent_id);
create index if not exists idx_drivers_agent_id on public.drivers(agent_id);
create index if not exists idx_clients_agent_id on public.clients(agent_id);
