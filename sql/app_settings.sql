-- =====================================================================
-- app_settings.sql  --  Editable app configuration (key/value)
-- Run once in the Supabase SQL Editor (after admin_api.sql). Re-runnable.
-- The send job reads these; the Gmail login stays in env vars (never here).
-- =====================================================================

create table if not exists app_settings (
  key   text primary key,
  value text
);

alter table app_settings enable row level security;
drop policy if exists admin_all_app_settings on app_settings;
create policy admin_all_app_settings on app_settings for all to authenticated
  using (is_admin()) with check (is_admin());

-- Seed defaults (won't overwrite values you've already set).
-- Body placeholders the job fills in: {first_name} {name} {event} {when} {link} {from_name}
insert into app_settings (key, value) values
  ('email_from_name',    'Frontenac Zone Ski Patrol'),
  ('email_from_address', 'frontenaczonecsp@gmail.com'),
  ('email_reply_to',     ''),
  ('email_subject',      'Your patrol event starts soon — sign in'),
  ('email_body_text',
'Hi {first_name},

Your Special Events shift is coming up:
  {event}
  {when}

Tap to sign in (no password needed):
{link}

You don''t need to sign out — your scheduled hours are recorded when you confirm.

— {from_name}
'),
  ('email_body_html',
'<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:480px;margin:auto;color:#16202b">
  <p style="font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#21506a;font-weight:700">
    Canadian Ski Patrol &middot; Frontenac Zone</p>
  <h2 style="margin:6px 0 2px">Your event starts soon</h2>
  <p style="color:#5c6b7a;margin:0 0 16px">Hi {first_name}, confirm you''re on and we''ll record your hours.</p>
  <div style="border:1px solid #dde4ea;border-radius:12px;padding:14px 16px;margin:0 0 18px">
    <div style="font-weight:700;font-size:18px">{event}</div>
    <div style="color:#5c6b7a;margin-top:4px">{when}</div>
  </div>
  <a href="{link}" style="display:block;text-align:center;background:#157a47;color:#fff;text-decoration:none;font-weight:700;font-size:17px;padding:14px;border-radius:11px">Sign me in</a>
  <p style="color:#5c6b7a;font-size:13px;margin-top:14px">
    You don''t need to sign out. If the button doesn''t work, paste this into your browser:<br>
    <span style="word-break:break-all">{link}</span></p>
</div>')
on conflict (key) do nothing;

-- To change later:
--   update app_settings set value = 'New Name' where key = 'email_from_name';
