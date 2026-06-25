-- =====================================================================
-- invites_due.sql  --  selection + token-minting half of send_invites.py
-- Run once in the Supabase SQL Editor (after remote_api.sql + app_settings.sql).
-- Re-runnable.
--
-- This is the database half of the old send_invites.py. The send-invites Edge
-- Function calls it, gets back a ready-to-send list (token, recipient, event,
-- pre-formatted "when"), mails each one, then marks sent_at itself. Because the
-- function marks sent only AFTER a successful send, a failed send leaves the
-- token with sent_at = null and it is simply retried on the next run -- exactly
-- like the script behaved.
--
-- Modes (mirror the script's flags):
--   'day_of'  -> every remote shift happening today (local) not yet ended and
--                not yet invited. First run of the day sends them all; sent_at
--                dedup stops later runs resending. (matches --day-of)
--   'window'  -> remote shifts starting within p_lead minutes, back to p_grace
--                minutes ago, not yet invited. (matches the default lead/grace)
--   p_shift   -> force one shift id regardless of timing (testing; matches --shift)
--
-- p_mint = false performs a true dry run: it selects candidates but inserts
-- nothing and returns null tokens (matches --dry-run).
-- =====================================================================

create or replace function invites_due(
  p_mode  text    default 'day_of',
  p_shift text    default null,
  p_lead  int     default 15,
  p_grace int     default 30,
  p_mint  boolean default true
)
returns table(
  token      uuid,
  to_email   text,
  to_name    text,
  first_name text,
  event_name text,
  when_text  text,
  shift_id   text
)
language plpgsql
security definer
set search_path = public
as $$
declare
  r       record;
  v_token uuid;
begin
  for r in
    select s.shift_id      as sid,
           s.helper_number as helper,
           p.name          as pname,
           p.email         as pemail,
           s.description   as descr,
           -- Format the naive local (Eastern) wall-clock exactly like the
           -- script's fmt_dt: "Saturday, Feb 14 at 6:00 PM". to_char reads the
           -- stored wall-clock as-is (no timezone shift), which is what we want.
           to_char(s.start_ts, 'FMDay", "FMMon FMDD" at "FMHH12:MI AM') as whent
    from shifts s
    left join people p on p.helper_number = s.helper_number
    where
      case
        when p_shift is not null then
          s.shift_id = p_shift
        else
          exists (select 1 from categories c
                   where c.cat = s.cat and c.signin_mode = 'remote')
          and not s.cancelled
          and (
            case
              when p_mode = 'day_of' then
                s.shift_date = (now() at time zone 'America/Toronto')::date
                and (s.end_ts at time zone 'America/Toronto') >= now()
              else
                (s.start_ts at time zone 'America/Toronto') <= now() + make_interval(mins => p_lead)
                and (s.start_ts at time zone 'America/Toronto') >= now() - make_interval(mins => p_grace)
            end
          )
          and not exists (
            select 1 from remote_invites ri
             where ri.shift_id = s.shift_id
               and ri.helper_number = s.helper_number
               and ri.sent_at is not null)
      end
  loop
    -- Mint (or reuse) the token only when actually sending and we have an
    -- address. Mirrors upsert_token(): one invite per (shift, person).
    if p_mint and r.pemail is not null then
      insert into remote_invites (shift_id, helper_number, email)
      values (r.sid, r.helper, r.pemail)
      on conflict (shift_id, helper_number)
        do update set email = excluded.email
      returning remote_invites.token into v_token;
    else
      v_token := null;
    end if;

    token      := v_token;
    to_email   := r.pemail;
    to_name    := r.pname;
    first_name := split_part(coalesce(r.pname, ''), ' ', 1);
    event_name := coalesce(r.descr, 'Special Event');
    when_text  := r.whent;
    shift_id   := r.sid;
    return next;
  end loop;
end
$$;

-- The Edge Function calls this with the service-role client; an admin can also
-- call it (e.g. a dry-run) from the dashboard.
grant execute on function invites_due(text, text, int, int, boolean)
  to service_role, authenticated;
