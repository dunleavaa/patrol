-- =====================================================================
-- admin_rpcs.sql  --  Admin-only data functions for the admin page
-- Run once in the Supabase SQL Editor (after admin_api.sql). Re-runnable.
-- Every function checks is_admin() first, so even though they're granted
-- to authenticated users, a non-admin just gets "not authorized".
-- =====================================================================

-- Attendance for a given day (defaults to today, America/Toronto),
-- joined to people + shift so the page can show names and times.
drop function if exists admin_attendance(date);
create or replace function admin_attendance(p_date date default null)
returns table(
  id bigint, shift_id text, helper_number text, name text, method text,
  position_id text, shift_label text,
  signed_in_at timestamptz, signed_out_at timestamptz,
  eligible_hours numeric, hours_recorded boolean, auto_closed boolean, presumed_out boolean,
  shift_date date, start_ts timestamp, end_ts timestamp, position_name text
)
language plpgsql stable security definer set search_path = public as $$
begin
  if not is_admin() then raise exception 'not authorized'; end if;
  return query
    select a.id, a.shift_id, a.helper_number, p.name, a.method,
           a.position_id, a.shift_label,
           a.signed_in_at, a.signed_out_at,
           a.eligible_hours, a.hours_recorded, a.auto_closed, a.presumed_out,
           s.shift_date, s.start_ts, s.end_ts, pos.name
    from attendance a
    left join people p on p.helper_number = a.helper_number
    left join shifts s on s.shift_id = a.shift_id
    left join positions pos on pos.position_id = a.position_id
    where (a.signed_in_at at time zone 'America/Toronto')::date
          = coalesce(p_date, today_local())
    order by a.signed_in_at;
end $$;

-- Fix or set a sign-out time. For kiosk records this recomputes actual
-- hours; remote (scheduled-hours) records keep their eligible_hours.
-- Clears the auto_closed flag since an admin has now set it deliberately.
create or replace function admin_fix_signout(p_id bigint, p_out timestamptz)
returns void
language plpgsql security definer set search_path = public as $$
begin
  if not is_admin() then raise exception 'not authorized'; end if;
  update attendance
     set signed_out_at = p_out,
         eligible_hours = case when method = 'kiosk'
                            then round(extract(epoch from (p_out - signed_in_at)) / 3600.0, 2)
                            else eligible_hours end,
         auto_closed = false,
         presumed_out = false
   where id = p_id;
end $$;

grant execute on function admin_attendance(date)               to authenticated;
grant execute on function admin_fix_signout(bigint, timestamptz) to authenticated;
