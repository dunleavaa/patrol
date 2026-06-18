-- Kiosk API + security
-- Run in the Supabase SQL Editor after schema.sql (and after the attendance
-- ALTERs for eligible_hours / hours_recorded / auto_closed / position_id /
-- shift_label).
--
-- The kiosk is login-less: it talks to Supabase with the public publishable
-- key. So we lock every base table behind Row Level Security (no direct access
-- for the anon role) and expose ONLY these functions, which run as the owner
-- and do exactly what the kiosk needs -- nothing more.
--
-- ACCESS CODE: every kiosk function takes p_code and checks it against the
-- 'kiosk_code' app setting. While that setting is empty the kiosk is OPEN
-- (handy for setup). Set a code to lock it down -- the bare URL and any direct
-- API call then return nothing without the code:
--   update app_settings set value = 'pick-something' where key = 'kiosk_code';

alter table people     enable row level security;
alter table positions  enable row level security;
alter table categories enable row level security;
alter table shifts     enable row level security;
alter table attendance enable row level security;
alter table sync_runs  enable row level security;

alter table attendance add column if not exists presumed_out boolean not null default false;

-- Settings table may not exist yet on a fresh DB; create it so the gate works.
create table if not exists app_settings (key text primary key, value text);
insert into app_settings(key, value) values ('kiosk_code', '')
  on conflict (key) do nothing;

-- Code check. Empty/unset 'kiosk_code' => open. Otherwise p_code must match.
create or replace function kiosk_code_ok(p_code text) returns boolean
language sql stable security definer set search_path = public as $$
  select case
    when (select nullif(value, '') from app_settings where key = 'kiosk_code') is null
      then true
    else p_code is not null
         and p_code = (select value from app_settings where key = 'kiosk_code')
  end
$$;

-- The page calls this to decide whether to show the access-code prompt.
create or replace function kiosk_unlock(p_code text) returns boolean
language sql stable security definer set search_path = public as $$
  select kiosk_code_ok(p_code)
$$;

-- "Today" in local (Eastern) time, so a shift doesn't flip dates at UTC midnight.
create or replace function today_local() returns date
language sql stable as $$ select (now() at time zone 'America/Toronto')::date $$;

-- Roster for the name picker -- names only.
drop function if exists kiosk_roster();
drop function if exists kiosk_roster(text);
create or replace function kiosk_roster(p_code text default null)
returns table(helper_number text, name text)
language sql security definer set search_path = public stable as $$
  select helper_number, name from people where kiosk_code_ok(p_code) order by name
$$;

-- Roles for the role dropdown.
drop function if exists kiosk_positions();
drop function if exists kiosk_positions(text);
create or replace function kiosk_positions(p_code text default null)
returns table(position_id text, name text)
language sql security definer set search_path = public stable as $$
  select position_id, name from positions where kiosk_code_ok(p_code) order by name
$$;

-- Today's kiosk-mode shifts with person + role + shift label, for prefilling.
drop function if exists kiosk_today_shifts();
drop function if exists kiosk_today_shifts(text);
create or replace function kiosk_today_shifts(p_code text default null)
returns table(shift_id text, helper_number text, helper_name text,
              position_id text, position_name text, shift_label text,
              start_ts timestamp, end_ts timestamp)
language sql security definer set search_path = public stable as $$
  select s.shift_id, s.helper_number, p.name,
         s.position_id, pos.name, s.description, s.start_ts, s.end_ts
  from shifts s
  join categories c on c.cat = s.cat
  left join people p on p.helper_number = s.helper_number
  left join positions pos on pos.position_id = s.position_id
  where kiosk_code_ok(p_code)
    and s.shift_date = today_local()
    and not s.cancelled
    and c.signin_mode = 'kiosk'
$$;

-- Who's currently signed in (open kiosk records), for the sign-out list.
drop function if exists kiosk_signed_in();
drop function if exists kiosk_signed_in(text);
create or replace function kiosk_signed_in(p_code text default null)
returns table(id bigint, helper_number text, name text,
              position_id text, shift_label text, signed_in_at timestamptz)
language sql security definer set search_path = public stable as $$
  select a.id, a.helper_number, p.name, a.position_id, a.shift_label, a.signed_in_at
  from attendance a
  left join people p on p.helper_number = a.helper_number
  where kiosk_code_ok(p_code) and a.method = 'kiosk' and a.signed_out_at is null
  order by a.signed_in_at
$$;

-- The day's board: every scheduled kiosk shift today, annotated with its
-- current status (open / in / done), plus any walk-ins signed in today that
-- aren't tied to a scheduled shift. This is what the kiosk's main screen shows.
-- presumed_out = true means the patroller is still on shift (signed_out_at is a
-- pre-filled scheduled-end fallback shown only after they confirm).
drop function if exists kiosk_board();
drop function if exists kiosk_board(date);
drop function if exists kiosk_board(date, text);
create or replace function kiosk_board(p_date date default null, p_code text default null)
returns table(row_key text, scheduled boolean, shift_id text, attendance_id bigint,
              helper_number text, name text, position_id text, position_name text,
              shift_label text, start_ts timestamp, end_ts timestamp,
              status text, signed_in_at timestamptz, eligible_hours numeric,
              email text, phone text, cell text)
language sql security definer set search_path = public stable as $$
  select 'sched-' || s.shift_id, true, s.shift_id, a.id,
         s.helper_number, p.name, s.position_id, pos.name, s.description,
         s.start_ts, s.end_ts,
         case when a.id is null then 'open'
              when a.presumed_out then 'in' else 'done' end,
         a.signed_in_at, a.eligible_hours,
         p.email, p.phone, p.cell
  from shifts s
  join categories c on c.cat = s.cat and c.signin_mode = 'kiosk'
  left join people p on p.helper_number = s.helper_number
  left join positions pos on pos.position_id = s.position_id
  left join lateral (
      select aa.* from attendance aa
      where aa.method = 'kiosk' and aa.shift_id = s.shift_id
      order by aa.signed_in_at desc limit 1
  ) a on true
  where kiosk_code_ok(p_code)
    and s.shift_date = coalesce(p_date, today_local()) and not s.cancelled

  union all

  select 'walk-' || a.id, false, null::text, a.id,
         a.helper_number, p.name, a.position_id, pos.name, a.shift_label,
         null::timestamp, null::timestamp,
         case when a.presumed_out then 'in' else 'done' end,
         a.signed_in_at, a.eligible_hours,
         p.email, p.phone, p.cell
  from attendance a
  left join people p on p.helper_number = a.helper_number
  left join positions pos on pos.position_id = a.position_id
  where kiosk_code_ok(p_code)
    and a.method = 'kiosk' and a.shift_id is null
    and (a.signed_in_at at time zone 'America/Toronto')::date = coalesce(p_date, today_local())
  order by 6
$$;

-- Sign in. shift_id is the matched scheduled shift (or null for a walk-in /
-- override). position_id + shift_label capture what they actually signed in as.
drop function if exists kiosk_sign_in(text, text, text, text);
drop function if exists kiosk_sign_in(text, text, text, text, text);
create or replace function kiosk_sign_in(
    p_helper_number text, p_shift_id text, p_position_id text, p_shift_label text,
    p_code text default null)
returns bigint
language plpgsql security definer set search_path = public as $$
declare new_id bigint; sched_end timestamptz; in_at timestamptz := now();
begin
  if not kiosk_code_ok(p_code) then return null; end if;
  -- Already on shift for this scheduled slot? Reuse it rather than duplicating.
  if nullif(p_shift_id, '') is not null then
    select id into new_id from attendance
     where method = 'kiosk' and shift_id = p_shift_id and presumed_out
     order by signed_in_at desc limit 1;
    if new_id is not null then return new_id; end if;
    -- Pre-fill the sign-out with the shift's scheduled end (local time -> tstz).
    select (s.end_ts at time zone 'America/Toronto') into sched_end
      from shifts s where s.shift_id = p_shift_id;
  end if;

  insert into attendance (shift_id, helper_number, position_id, shift_label, method,
                          signed_in_at, signed_out_at, eligible_hours, presumed_out)
  values (nullif(p_shift_id, ''), p_helper_number, nullif(p_position_id, ''),
          nullif(p_shift_label, ''), 'kiosk', in_at,
          sched_end,
          case when sched_end is not null
               then greatest(0, round(extract(epoch from (sched_end - in_at)) / 3600.0, 2))
               else null end,
          true)
  returning id into new_id;
  return new_id;
end $$;

-- Sign out. Kiosk uses ACTUAL hours (signed-out minus signed-in).
drop function if exists kiosk_sign_out(bigint);
drop function if exists kiosk_sign_out(bigint, text);
create or replace function kiosk_sign_out(p_id bigint, p_code text default null)
returns numeric
language plpgsql security definer set search_path = public as $$
declare hrs numeric;
begin
  if not kiosk_code_ok(p_code) then return null; end if;
  update attendance
     set signed_out_at = now(),
         eligible_hours = round(extract(epoch from (now() - signed_in_at)) / 3600.0, 2),
         presumed_out = false
   where id = p_id and presumed_out
   returning eligible_hours into hrs;
  return hrs;
end $$;

drop function if exists kiosk_leave_at_end(bigint);
drop function if exists kiosk_leave_at_end(bigint, text);
create or replace function kiosk_leave_at_end(p_id bigint, p_code text default null)
returns numeric
language plpgsql security definer set search_path = public as $$
declare hrs numeric;
begin
  if not kiosk_code_ok(p_code) then return null; end if;
  -- "Left at end of shift": confirm the pre-filled scheduled end as the real
  -- sign-out. signed_out_at / eligible_hours were set at sign-in; just confirm.
  update attendance
     set presumed_out = false
   where id = p_id and presumed_out and signed_out_at is not null
   returning eligible_hours into hrs;
  return hrs;
end $$;

grant execute on function kiosk_code_ok(text)                            to anon;
grant execute on function kiosk_unlock(text)                             to anon;
grant execute on function kiosk_roster(text)                             to anon;
grant execute on function kiosk_positions(text)                          to anon;
grant execute on function kiosk_today_shifts(text)                       to anon;
grant execute on function kiosk_signed_in(text)                          to anon;
grant execute on function kiosk_board(date, text)                        to anon;
grant execute on function kiosk_sign_in(text, text, text, text, text)    to anon;
grant execute on function kiosk_sign_out(bigint, text)                   to anon;
grant execute on function kiosk_leave_at_end(bigint, text)               to anon;
