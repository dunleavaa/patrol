-- =====================================================================
-- remote_api.sql  --  Special Events remote sign-in via emailed link
-- Run once in the Supabase SQL Editor (after admin_api.sql). Re-runnable.
--
-- The token in the emailed link IS the credential -- only the person who
-- got the email has it. No Supabase Auth on the remote side. Functions are
-- SECURITY DEFINER + granted to anon, but each one is gated by the token.
-- =====================================================================

create extension if not exists pgcrypto;   -- gen_random_uuid()

create table if not exists remote_invites (
  token         uuid primary key default gen_random_uuid(),
  shift_id      text not null,
  helper_number text not null,
  email         text,                       -- where the link was sent
  created_at    timestamptz not null default now(),
  sent_at       timestamptz,                -- when the email actually went out
  used_at       timestamptz,                -- when they confirmed
  attendance_id bigint,                     -- the resulting attendance row
  unique (shift_id, helper_number)          -- one invite per person per shift
);

alter table remote_invites enable row level security;
drop policy if exists admin_all_remote_invites on remote_invites;
create policy admin_all_remote_invites on remote_invites for all to authenticated
  using (is_admin()) with check (is_admin());


-- Resolve a token -> the event details the remote page shows.
-- Returns no rows if the token is unknown (page treats that as invalid link).
create or replace function remote_resolve(p_token uuid)
returns table(name text, shift_label text, position_name text, shift_date date,
              start_ts timestamp, end_ts timestamp, hours numeric, confirmed boolean)
language sql security definer set search_path = public stable as $$
  select p.name, s.description, pos.name, s.shift_date, s.start_ts, s.end_ts,
         coalesce(s.duration_hours,
                  round(extract(epoch from (s.end_ts - s.start_ts)) / 3600.0, 2)),
         (ri.used_at is not null)
  from remote_invites ri
  join shifts s on s.shift_id = ri.shift_id
  left join people p on p.helper_number = ri.helper_number
  left join positions pos on pos.position_id = s.position_id
  where ri.token = p_token;
$$;


-- Confirm presence -> records remote attendance crediting the SCHEDULED hours.
-- Idempotent: confirming an already-used link just returns the hours again.
create or replace function remote_confirm(p_token uuid)
returns numeric
language plpgsql security definer set search_path = public as $$
declare v_inv remote_invites; v_shift shifts; v_hours numeric; v_att bigint;
begin
  select * into v_inv from remote_invites where token = p_token;
  if v_inv.token is null then raise exception 'invalid link'; end if;

  if v_inv.used_at is not null then
    select eligible_hours into v_hours from attendance where id = v_inv.attendance_id;
    return v_hours;
  end if;

  select * into v_shift from shifts where shift_id = v_inv.shift_id;
  if v_shift.shift_id is null then raise exception 'shift not found'; end if;

  v_hours := coalesce(v_shift.duration_hours,
                      round(extract(epoch from (v_shift.end_ts - v_shift.start_ts)) / 3600.0, 2));

  insert into attendance (shift_id, helper_number, position_id, shift_label, method,
                          signed_in_at, signed_out_at, eligible_hours, presumed_out)
  values (v_inv.shift_id, v_inv.helper_number, v_shift.position_id, v_shift.description, 'remote',
          now(), (v_shift.end_ts at time zone 'America/Toronto'), v_hours, false)
  returning id into v_att;

  update remote_invites set used_at = now(), attendance_id = v_att where token = p_token;
  return v_hours;
end $$;

grant execute on function remote_resolve(uuid) to anon, authenticated;
grant execute on function remote_confirm(uuid) to anon, authenticated;
