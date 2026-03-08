create table if not exists agent_academy_progress (
  id bigint generated always as identity primary key,
  agent_id text not null,
  agent_email text,
  agent_name text,
  module_key text not null,
  passed boolean default false,
  score integer default 0,
  created_at timestamptz default now()
);

create unique index if not exists agent_academy_progress_agent_module_idx
on agent_academy_progress(agent_id, module_key);
