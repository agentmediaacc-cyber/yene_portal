create table if not exists agent_wallet_transactions (
  id bigint generated always as identity primary key,
  agent_id text not null,
  agent_email text,
  txn_type text,
  amount numeric default 0,
  description text,
  created_at timestamptz default now()
);
