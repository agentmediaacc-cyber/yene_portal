create table if not exists agent_wallet_ledger (
  id bigint generated always as identity primary key,
  agent_id text not null,
  agent_email text,
  txn_type text not null, -- credit, debit
  amount numeric default 0,
  description text,
  reference_no text,
  status text default 'approved', -- approved, pending, cancelled
  created_at timestamptz default now()
);

create table if not exists agent_withdraw_requests (
  id bigint generated always as identity primary key,
  agent_id text not null,
  agent_email text,
  agent_name text,
  request_amount numeric default 0,
  request_note text,
  status text default 'pending', -- pending, approved, sent, rejected
  admin_note text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists agent_wallet_ledger_agent_id_idx
on agent_wallet_ledger(agent_id);

create index if not exists agent_withdraw_requests_agent_id_idx
on agent_withdraw_requests(agent_id);
