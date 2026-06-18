# Batawa Sign-In System

Web sign-in/out for the Canadian Ski Patrol – Frontenac Zone. Source of truth
for people and shifts is WhenToHelp (W2H). Data lives in Supabase (Montréal
region). This repo hosts everything else: the web pages (GitHub Pages) and the
scheduled scripts (GitHub Actions).

## What's in here

```
docs/                     the three web pages (served by GitHub Pages)
  index.html              kiosk board (tablet)
  admin.html              admin site
  signin.html             remote event sign-in (opened from emailed link)
scripts/                  the worker scripts (run by GitHub Actions)
  w2h_fetch_http.py       log in to W2H, download CSVs, build model.json (no browser)
  w2h_parse.py            CSVs -> model.json
  w2h_load.py             model.json -> Supabase
  send_invites.py         email remote sign-in links
  requirements.txt        python deps (requests, psycopg)
  _fallback/w2h_fetch.py  Playwright version, only if W2H ever adds a captcha
sql/                      database setup (run once in the Supabase SQL editor)
supabase/functions/set-password/index.ts   the password Edge Function
.github/workflows/        the two schedules (sync + invites)
```

## One-time setup

### 1. Create the GitHub repo (make it Public)

Public is recommended: Actions minutes are unlimited on public repos (private
repos have a monthly cap that the every-10-minutes invite job would exceed), and
Pages is free. There are **no secrets in the code** — every credential is read
from an environment variable — so public is safe. (The pages contain only the
Supabase *publishable* key, which is meant for browsers.)

Then push this folder:

```
git init
git add .
git commit -m "Batawa sign-in system"
git branch -M main
git remote add origin https://github.com/USERNAME/REPO.git
git push -u origin main
```

### 2. Add the Actions secrets

Repo **Settings → Secrets and variables → Actions → New repository secret**.
Add each of these:

| Secret | Value |
|---|---|
| `W2H_USER` | WhenToHelp coordinator login |
| `W2H_PASS` | WhenToHelp password |
| `DATABASE_URL` | `postgresql://postgres.mgnpfgziiwqhfcrkqkqa:DB_PASSWORD@aws-1-ca-central-1.pooler.supabase.com:5432/postgres` |
| `GMAIL_USER` | `frontenaczonecsp@gmail.com` |
| `GMAIL_APP_PASSWORD` | the 16-char Gmail app password |
| `SITE_URL` | your Pages URL incl. the repo path — see step 3 |

### 3. Turn on GitHub Pages

Repo **Settings → Pages → Build and deployment → Deploy from a branch →**
branch `main`, folder `/docs` → **Save**. After a minute you'll get a URL like:

```
https://USERNAME.github.io/REPO/
```

That makes:
- kiosk    `https://USERNAME.github.io/REPO/`
- admin    `https://USERNAME.github.io/REPO/admin.html`
- remote   `https://USERNAME.github.io/REPO/signin.html`

Now set the **`SITE_URL`** secret to the base **without** a trailing slash and
**including** the repo path:

```
https://USERNAME.github.io/REPO
```

(`send_invites.py` builds links as `SITE_URL + /signin.html?t=...`, so the repo
path must be included or the emailed links will 404.)

If you later add a custom domain or use a `USERNAME.github.io` repo (served at
the root), update `SITE_URL` to match.

### 4. Test before trusting the schedule

- **Actions** tab → **Sync W2H** → **Run workflow**. Watch it log in, save the
  CSVs, and load (it should report ~68 people / ~455 shifts). For a season
  backfill, run it again with the `start` box set to e.g. `2026-02-01`.
- **Actions** tab → **Send invites** → **Run workflow**. Leave the box blank to
  send anything currently due, or put a shift id in to force one.

## How the schedules run

- **Sync W2H** — every 3 hours. Pulls a rolling 60-day window and loads it.
- **Send invites** — every 10 minutes. Sends links for remote shifts starting
  within ~15 minutes that haven't been invited yet; it records each send, so
  repeated polling never double-sends.

Notes about GitHub's scheduler: cron times are best-effort and can drift a few
minutes under load (fine here). GitHub also **disables scheduled workflows after
60 days with no repo activity** — any commit re-enables them, so just push a
small change if it ever goes quiet for two months.

## Editing later

- **Pages**: edit files in `docs/` and push — Pages redeploys automatically.
- **Scripts/schedules**: edit `scripts/` or `.github/workflows/` and push.
- **Database / Edge Function**: `sql/` and `supabase/` are reference copies; run
  SQL in the Supabase SQL editor and deploy the function in the Supabase
  dashboard (Edge Functions) as before.

## Running locally (optional)

```
pip install -r scripts/requirements.txt
cd scripts
# set W2H_USER, W2H_PASS, DATABASE_URL, GMAIL_USER, GMAIL_APP_PASSWORD, SITE_URL
python w2h_fetch_http.py        # or: --start 2026-02-01
python w2h_load.py
python send_invites.py --dry-run
```
