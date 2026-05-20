alter table if exists weekly_payment_settings add column if not exists region text;
alter table if exists weekly_payment_settings add column if not exists town text;
alter table if exists weekly_payment_settings add column if not exists driver_reward numeric default 0;
alter table if exists weekly_payment_settings add column if not exists client_reward numeric default 0;
alter table if exists weekly_payment_settings add column if not exists activation_bonus numeric default 0;
alter table if exists weekly_payment_settings add column if not exists first_trip_bonus numeric default 0;
alter table if exists weekly_payment_settings add column if not exists status text default 'active';
alter table if exists weekly_payment_settings add column if not exists week_start date;
alter table if exists weekly_payment_settings add column if not exists week_end date;
alter table if exists weekly_payment_settings add column if not exists effective_from date;
alter table if exists weekly_payment_settings add column if not exists effective_to date;

alter table if exists wallet_ledger add column if not exists source_type text;
alter table if exists wallet_ledger add column if not exists source_id text;
alter table if exists wallet_ledger add column if not exists status text default 'posted';
alter table if exists wallet_ledger add column if not exists description text;
alter table if exists wallet_ledger add column if not exists approved_by text;
alter table if exists wallet_ledger add column if not exists payment_rule_id text;
alter table if exists wallet_ledger add column if not exists statement_number text;

alter table if exists agent_wallet_ledger add column if not exists source_type text;
alter table if exists agent_wallet_ledger add column if not exists source_id text;
alter table if exists agent_wallet_ledger add column if not exists status text default 'posted';
alter table if exists agent_wallet_ledger add column if not exists description text;
alter table if exists agent_wallet_ledger add column if not exists approved_by text;
alter table if exists agent_wallet_ledger add column if not exists payment_rule_id text;
alter table if exists agent_wallet_ledger add column if not exists statement_number text;

alter table if exists agent_profiles add column if not exists region_locked boolean default false;
alter table if exists agent_profiles add column if not exists region_locked_at timestamptz;
alter table if exists agent_profiles add column if not exists region_locked_by text;
alter table if exists agent_profiles add column if not exists current_working_town text;
alter table if exists agent_profiles add column if not exists operation_region text;
alter table if exists agent_profiles add column if not exists account_status text default 'active';
alter table if exists agent_profiles add column if not exists login_allowed boolean default true;
alter table if exists agent_profiles add column if not exists registration_allowed boolean default true;
alter table if exists agent_profiles add column if not exists allow_driver_registration boolean default true;
alter table if exists agent_profiles add column if not exists allow_client_registration boolean default true;
alter table if exists agent_profiles add column if not exists access_note text;
alter table if exists agent_profiles add column if not exists access_updated_by text;
alter table if exists agent_profiles add column if not exists access_updated_at timestamptz;

alter table if exists region_access_settings add column if not exists allow_driver_registration boolean default true;
alter table if exists region_access_settings add column if not exists allow_client_registration boolean default true;
alter table if exists region_access_settings add column if not exists allow_agent_login boolean default true;
alter table if exists region_access_settings add column if not exists allow_agent_activation boolean default true;

create table if not exists finance_statements (
  id text primary key,
  statement_number text unique,
  agent_id text,
  agent_email text,
  amount numeric default 0,
  period_start date,
  period_end date,
  status text default 'paid',
  executed_by text,
  executed_at timestamptz,
  note text,
  created_at timestamptz default timezone('utc', now())
);

create table if not exists admin_audit_log (
  id text primary key,
  action text not null,
  target_type text,
  target_id text,
  admin_email text,
  amount numeric,
  metadata jsonb,
  created_at timestamptz default timezone('utc', now())
);

create table if not exists agent_withdraw_requests (
  id text primary key,
  agent_id text,
  agent_email text,
  amount numeric default 0,
  request_amount numeric default 0,
  status text default 'pending',
  note text,
  admin_response text,
  responded_by text,
  responded_at timestamptz,
  created_at timestamptz default timezone('utc', now())
);

alter table if exists agent_withdraw_requests add column if not exists admin_response text;
alter table if exists agent_withdraw_requests add column if not exists responded_by text;
alter table if exists agent_withdraw_requests add column if not exists responded_at timestamptz;
alter table if exists agent_withdraw_requests add column if not exists request_amount numeric default 0;

create index if not exists idx_weekly_payment_settings_region_town_week
  on weekly_payment_settings (region, town, week_start, week_end);
create index if not exists idx_agent_wallet_ledger_source
  on agent_wallet_ledger (source_type, source_id);
create index if not exists idx_agent_wallet_ledger_agent
  on agent_wallet_ledger (agent_id, agent_email);
create index if not exists idx_agent_profiles_region_lock
  on agent_profiles (region_locked, region, town);
create index if not exists idx_agent_profiles_account_status
  on agent_profiles (account_status);
create index if not exists idx_agent_profiles_login_allowed
  on agent_profiles (login_allowed);
create index if not exists idx_finance_statements_agent
  on finance_statements (agent_id, agent_email, executed_at);
create index if not exists idx_admin_audit_log_action_time
  on admin_audit_log (action, created_at);
create index if not exists idx_withdraw_requests_status_time
  on agent_withdraw_requests (status, created_at);
