#!/usr/bin/env python3
"""
send_invites.py -- email Special Events sign-in links ~10 min before start.

Run on a schedule (every ~5 min) on the same worker as the W2H sync, e.g.:
    python send_invites.py

Environment variables:
    DATABASE_URL         Supabase Session-pooler connection string
    GMAIL_USER           e.g. frontenaczonecsp@gmail.com
    GMAIL_APP_PASSWORD   16-char app password (account needs 2-Step Verification)
    SITE_URL             base URL of the deployed site, e.g. https://your-site.netlify.app
    FROM_NAME            (optional) display name; default "Frontenac Zone Ski Patrol"

Options:
    --shift SHIFT_ID     force-send the invite for one shift now (ignores timing) -- for testing
    --dry-run            list who would be emailed, send nothing, write nothing
    --lead N             minutes before start to send (default 15)
    --grace N            also catch shifts that started up to N minutes ago (default 30)

Install once:  python -m pip install "psycopg[binary]"
"""
import os
import sys
import argparse
import smtplib
import ssl
from email.message import EmailMessage

import psycopg

DB        = os.environ.get("DATABASE_URL")
GUSER     = os.environ.get("GMAIL_USER")
GPASS     = os.environ.get("GMAIL_APP_PASSWORD")
SITE      = (os.environ.get("SITE_URL") or "").rstrip("/")
FROM_NAME = os.environ.get("FROM_NAME", "Frontenac Zone Ski Patrol")


def fmt_dt(dt):
    """Portable 'Saturday, Feb 14 at 6:00 PM' (no platform-specific strftime codes)."""
    if not dt:
        return ""
    h = dt.hour % 12 or 12
    ap = "AM" if dt.hour < 12 else "PM"
    return f"{dt.strftime('%A, %b')} {dt.day} at {h}:{dt.minute:02d} {ap}"


def load_settings(cur):
    cur.execute("select key, value from app_settings")
    return {k: v for k, v in cur.fetchall()}


def candidates(cur, shift_id, lead, grace):
    if shift_id:
        cur.execute(
            """select s.shift_id, s.helper_number, p.name, p.email, s.description,
                      s.start_ts, s.end_ts
                 from shifts s
                 left join people p on p.helper_number = s.helper_number
                where s.shift_id = %s""",
            (shift_id,),
        )
    else:
        cur.execute(
            """select s.shift_id, s.helper_number, p.name, p.email, s.description,
                      s.start_ts, s.end_ts
                 from shifts s
                 join categories c on c.cat = s.cat and c.signin_mode = 'remote'
                 left join people p on p.helper_number = s.helper_number
                where not s.cancelled
                  and (s.start_ts at time zone 'America/Toronto') <= now() + make_interval(mins => %s)
                  and (s.start_ts at time zone 'America/Toronto') >= now() - make_interval(mins => %s)
                  and not exists (
                        select 1 from remote_invites ri
                         where ri.shift_id = s.shift_id
                           and ri.helper_number = s.helper_number
                           and ri.sent_at is not null)""",
            (lead, grace),
        )
    return cur.fetchall()


def upsert_token(cur, shift_id, helper, email):
    cur.execute(
        """insert into remote_invites (shift_id, helper_number, email)
           values (%s, %s, %s)
           on conflict (shift_id, helper_number) do update set email = excluded.email
           returning token""",
        (shift_id, helper, email),
    )
    return cur.fetchone()[0]


# Fallbacks used only if the matching app_settings row is blank/missing.
DEFAULT_SUBJECT = "Your patrol event starts soon — sign in"
DEFAULT_TEXT = (
    "Hi {first_name},\n\n"
    "Your Special Events shift is coming up:\n"
    "  {event}\n"
    "  {when}\n\n"
    "Tap to sign in (no password needed):\n{link}\n\n"
    "You don't need to sign out — your scheduled hours are recorded when you confirm.\n\n"
    "— {from_name}\n"
)
DEFAULT_HTML = """\
<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:480px;margin:auto;color:#16202b">
  <p style="font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#21506a;font-weight:700">
    Canadian Ski Patrol &middot; Frontenac Zone</p>
  <h2 style="margin:6px 0 2px">Your event starts soon</h2>
  <p style="color:#5c6b7a;margin:0 0 16px">Hi {first_name}, confirm you're on and we'll record your hours.</p>
  <div style="border:1px solid #dde4ea;border-radius:12px;padding:14px 16px;margin:0 0 18px">
    <div style="font-weight:700;font-size:18px">{event}</div>
    <div style="color:#5c6b7a;margin-top:4px">{when}</div>
  </div>
  <a href="{link}" style="display:block;text-align:center;background:#157a47;color:#fff;
     text-decoration:none;font-weight:700;font-size:17px;padding:14px;border-radius:11px">Sign me in</a>
  <p style="color:#5c6b7a;font-size:13px;margin-top:14px">
    You don't need to sign out. If the button doesn't work, paste this into your browser:<br>
    <span style="word-break:break-all">{link}</span></p>
</div>"""


def fill(template, repl):
    """Substitute {placeholder} tokens; leaves any unknown braces untouched."""
    out = template or ""
    for k, v in repl.items():
        out = out.replace("{" + k + "}", v if v is not None else "")
    return out


def send_email(to_email, to_name, subject, text, html, from_name, from_addr, reply_to):
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(GUSER, GPASS)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shift", help="force-send for one shift id (ignores timing)")
    ap.add_argument("--dry-run", action="store_true", help="list only; send and write nothing")
    ap.add_argument("--lead", type=int, default=15, help="minutes before start to send (default 15)")
    ap.add_argument("--grace", type=int, default=30, help="also catch starts up to N min ago (default 30)")
    args = ap.parse_args()

    if not DB:
        sys.exit("Set DATABASE_URL first.")
    if not args.dry_run and not (GUSER and GPASS):
        sys.exit("Set GMAIL_USER and GMAIL_APP_PASSWORD (or use --dry-run).")
    if not args.dry_run and not SITE:
        sys.exit("Set SITE_URL (the deployed site base URL) so links resolve.")

    sent = skipped = 0
    with psycopg.connect(DB) as conn:
        with conn.cursor() as cur:
            cfg = {} if args.dry_run else load_settings(cur)
            from_name = cfg.get("email_from_name") or FROM_NAME
            from_addr = cfg.get("email_from_address") or GUSER
            reply_to  = cfg.get("email_reply_to") or ""
            t_subject = cfg.get("email_subject") or DEFAULT_SUBJECT
            t_text    = cfg.get("email_body_text") or DEFAULT_TEXT
            t_html    = cfg.get("email_body_html") or DEFAULT_HTML

            rows = candidates(cur, args.shift, args.lead, args.grace)
            if not rows:
                print("No events to invite right now.")
                return
            for shift_id, helper, name, email, desc, start, end in rows:
                when = fmt_dt(start)
                if not email:
                    print(f"  skip (no email): {name or helper} · {desc}")
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"  would invite {name} <{email}> · {desc} · {when}")
                    continue
                token = upsert_token(cur, shift_id, helper, email)
                link = f"{SITE}/signin.html?t={token}"
                repl = {
                    "first_name": (name or "").split(" ")[0],
                    "name": name or "",
                    "event": desc or "Special Event",
                    "when": when,
                    "link": link,
                    "from_name": from_name,
                }
                try:
                    send_email(email, name,
                               fill(t_subject, repl), fill(t_text, repl), fill(t_html, repl),
                               from_name, from_addr, reply_to)
                except Exception as e:
                    print(f"  send FAILED for {name} <{email}>: {e}")
                    skipped += 1
                    continue
                cur.execute("update remote_invites set sent_at = now() where token = %s", (token,))
                conn.commit()
                print(f"  sent to {name} <{email}> · {desc}")
                sent += 1

    if args.dry_run:
        print(f"dry run: {len(rows)} candidate(s).")
    else:
        print(f"done: {sent} sent, {skipped} skipped.")


if __name__ == "__main__":
    main()
