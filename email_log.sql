-- Email log: one row per send attempt (success or failure), written by
-- send_invites.py. Admins can read it; the worker writes it via the privileged
-- pooler connection (bypasses RLS). Mirrors how sync_runs is exposed.

create table if not exists email_log (
  id         bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  to_email   text,
  subject    text,
  shift_id   text,
  kind       text default 'invite',
  status     text not null,          -- 'sent' | 'failed'
  error      text
);

create index if not exists email_log_created_idx on email_log (created_at desc);

alter table email_log enable row level security;

drop policy if exists admin_all_email_log on email_log;
create policy admin_all_email_log on email_log
  for all to authenticated
  using (is_admin()) with check (is_admin());

grant select on email_log to authenticated;
