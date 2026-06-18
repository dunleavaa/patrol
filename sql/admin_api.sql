-- =====================================================================
-- admin_api.sql  --  Admin gate for the Batawa sign-in system
-- Run once in the Supabase SQL Editor. Safe to re-run (idempotent).
--
-- WHAT THIS DOES
--   * Creates an `admins` allowlist keyed on EMAIL. A coordinator is an
--     admin if the email they log in with is on this list and active --
--     it does NOT matter whether they signed in with Google or with an
--     email + password. Both arrive as an authenticated user carrying an
--     email claim, and that's all we check.
--   * is_admin()      -> true/false for the current logged-in user
--   * current_admin() -> the caller's admins row (for the page to greet them)
--   * Row-Level-Security policies that give admins full read/write on the
--     real tables, while everyone else (the anonymous kiosk) stays limited
--     to the kiosk_* functions we already whitelisted.
--
-- SECURITY NOTE -- email is the trust anchor, so the email must be proven:
--   * Google logins are fine: Google verifies the address.
--   * For email + password, you MUST require email confirmation (or just
--     create the users yourself in Auth -> Users). Otherwise someone could
--     register an account claiming a coordinator's address. With "Confirm
--     email" on, they can't get a session until they click the link sent to
--     that address -- which proves they own it. Turn it on (see notes at end).
-- =====================================================================


-- 1. The allowlist -----------------------------------------------------
create table if not exists admins (
    email     text primary key,                 -- lowercase login email
    name      text,
    active    boolean not null default true,
    added_at  timestamptz not null default now()
);

alter table admins enable row level security;


-- 2. Who is asking? ----------------------------------------------------
-- SECURITY DEFINER so it can read `admins` regardless of RLS, and so it
-- never locks itself out. It still sees the *caller's* identity, because
-- auth.jwt() reads the request's token, not the function owner's.
create or replace function is_admin()
returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from admins
    where active
      and lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

create or replace function current_admin()
returns admins
language sql stable security definer set search_path = public as $$
  select * from admins
  where active
    and lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  limit 1;
$$;

grant execute on function is_admin()      to authenticated, anon;
grant execute on function current_admin() to authenticated;


-- 3. Admin policies on the real tables ---------------------------------
-- RLS is already enabled on these (from kiosk_api.sql); we re-assert it
-- to be safe, then give admins full access. The anonymous kiosk is
-- unaffected -- it never touches these tables directly, only the
-- SECURITY DEFINER kiosk_* functions, which bypass RLS.
--
-- drop-then-create keeps this script re-runnable (Postgres has no
-- CREATE POLICY IF NOT EXISTS).
do $$
declare t text;
begin
  foreach t in array array['people','positions','categories','shifts','attendance','sync_runs']
  loop
    execute format('alter table %I enable row level security;', t);
    execute format('drop policy if exists admin_all_%1$s on %1$s;', t);
    execute format(
      'create policy admin_all_%1$s on %1$s for all to authenticated '
      'using (is_admin()) with check (is_admin());', t);
  end loop;
end $$;


-- 4. Policies on the admins table itself -------------------------------
-- Admins can see and manage the allowlist (so the admin page can add /
-- deactivate coordinators later). Non-admins get nothing. Seeding below
-- runs as the postgres role in the SQL editor, which bypasses RLS, so
-- you can always add the first admin even when the list is empty.
drop policy if exists admin_read_admins  on admins;
drop policy if exists admin_write_admins on admins;
create policy admin_read_admins  on admins for select to authenticated using (is_admin());
create policy admin_write_admins on admins for all    to authenticated using (is_admin()) with check (is_admin());


-- 5. Seed your coordinators -------------------------------------------
-- EDIT THIS: one row per coordinator, lowercase login emails. Re-running
-- updates the name and re-activates. Use the exact address each person
-- will sign in with (their Google account email, or the email you create
-- an email+password user for).
insert into admins (email, name) values
  ('frontenaczonecsp@gmail.com', 'Zone Account')        -- replace / extend
  -- , ('aaron.dunleavy@example.com', 'Aaron Dunleavy')
  -- , ('coordinator2@example.com',  'Coordinator Two')
on conflict (email) do update
  set active = true,
      name   = excluded.name;
