-- =====================================================================
-- cron_invites.sql  --  schedule send-invites in Supabase (replaces the
--                       GitHub "Send invites" workflow).
-- Run once in the Supabase SQL Editor. Safe to re-run (unschedules first).
--
-- This mirrors the sync-w2h cron you already have: pg_cron fires every 10
-- minutes, pg_net POSTs to the Edge Function, and the service-role key is read
-- from Vault so it never sits in plaintext in the schedule.
--
-- PREREQUISITES (one-time, almost certainly already done for the sync cron):
--   * Extensions pg_cron and pg_net enabled (Database -> Extensions).
--   * A Vault secret holding the service-role key. The sync cron created one;
--     reuse the SAME name here. This file assumes it is called
--     'service_role_key' -- if yours differs, change the name in the SELECT
--     below to match (Project Settings -> Vault shows the name).
-- =====================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Remove any earlier copy so re-running is clean.
select cron.unschedule('send-invites-every-10-min')
where exists (select 1 from cron.job where jobname = 'send-invites-every-10-min');

-- Every 10 minutes, call the Edge Function in 'day_of' mode (matches the old
-- workflow's `send_invites.py --day-of`). The function itself dedupes via
-- sent_at, so polling never double-sends.
select cron.schedule(
  'send-invites-every-10-min',
  '*/10 * * * *',
  $cron$
  select net.http_post(
    url     := 'https://mgnpfgziiwqhfcrkqkqa.supabase.co/functions/v1/send-invites',
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'Authorization', 'Bearer ' || (
        select decrypted_secret from vault.decrypted_secrets
         where name = 'service_role_key' limit 1
      )
    ),
    body    := jsonb_build_object('mode', 'day_of')
  );
  $cron$
);

-- Verify:
--   select jobname, schedule, active from cron.job where jobname like 'send-invites%';
-- Recent runs:
--   select * from cron.job_run_details
--     where jobid = (select jobid from cron.job where jobname = 'send-invites-every-10-min')
--     order by start_time desc limit 10;
--
-- To send ~15 min BEFORE each start instead of once at the start of the day,
-- change the body to jsonb_build_object('mode','window') (optionally add
-- 'lead' / 'grace' integers).
