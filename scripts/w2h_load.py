#!/usr/bin/env python3
"""
w2h_load.py -- push model.json into the Supabase (Postgres) tables.

Idempotent: every row is upserted on its stable W2H id (helper_number,
position_id, cat, shift_id), so running the sync every few hours updates in
place instead of duplicating. Shifts that have dropped out of W2H (and are
still in the future) get flagged cancelled rather than left as ghosts.

Run schema.sql once in the Supabase SQL editor first.

Setup:
  python -m pip install "psycopg[binary]"

Get your connection string from Supabase: Project Settings -> Database ->
Connection string (URI). Then:
  $env:DATABASE_URL='postgresql://...'      (PowerShell)
  python w2h_load.py                         # loads model.json

Preview without a database (reads model.json only):
  python w2h_load.py --dry-run
"""

import argparse
import json
import os
import sys
from datetime import datetime, date


def to_dt(iso):
    return datetime.fromisoformat(iso) if iso else None


def to_date(iso):
    return date.fromisoformat(iso[:10]) if iso else None


def build_rows(model):
    people = [(p["helper_number"], p["name"], p["first_name"], p["last_name"],
               p["email"], p["phone"], p["cell"], p["city"])
              for p in model["people"]]
    positions = [(p["position_id"], p["name"])
                 for p in model["positions"] if p["position_id"]]
    categories = [(c["cat"], c["name"], c["mode"]) for c in model["categories"]]
    shifts = [(s["shift_id"], s["schedule_id"], s["helper_number"],
               s["position_id"] or None, s["cat"] or None, s["description"],
               to_date(s["start"]) or to_date_us(s["date"]),
               to_dt(s["start"]), to_dt(s["end"]), s["duration_hours"])
              for s in model["shifts"]]
    return people, positions, categories, shifts


def to_date_us(d):
    # fallback for "M/D/YYYY" if no ISO start time was present
    try:
        return datetime.strptime(d.strip(), "%m/%d/%Y").date()
    except (ValueError, AttributeError):
        return None


PEOPLE_SQL = """
insert into people (helper_number, name, first_name, last_name, email, phone, cell, city, updated_at)
values (%s, %s, %s, %s, %s, %s, %s, %s, now())
on conflict (helper_number) do update set
  name=excluded.name, first_name=excluded.first_name, last_name=excluded.last_name,
  email=excluded.email, phone=excluded.phone, cell=excluded.cell, city=excluded.city,
  updated_at=now();
"""

POSITIONS_SQL = """
insert into positions (position_id, name, updated_at)
values (%s, %s, now())
on conflict (position_id) do update set name=excluded.name, updated_at=now();
"""

# On update, keep the existing signin_mode -- an admin may have set it (e.g.
# Batawa -> kiosk). Only the first insert uses the parser's suggested mode.
CATEGORIES_SQL = """
insert into categories (cat, name, signin_mode, updated_at)
values (%s, %s, %s, now())
on conflict (cat) do update set name=excluded.name, updated_at=now();
"""

SHIFTS_SQL = """
insert into shifts (shift_id, schedule_id, helper_number, position_id, cat, description,
                    shift_date, start_ts, end_ts, duration_hours, last_seen_at, cancelled, updated_at)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), false, now())
on conflict (shift_id) do update set
  schedule_id=excluded.schedule_id, helper_number=excluded.helper_number,
  position_id=excluded.position_id, cat=excluded.cat, description=excluded.description,
  shift_date=excluded.shift_date, start_ts=excluded.start_ts, end_ts=excluded.end_ts,
  duration_hours=excluded.duration_hours, last_seen_at=now(), cancelled=false, updated_at=now();
"""

# Future shifts not present in this pull have been removed in W2H -> cancel them.
CANCEL_SQL = """
update shifts set cancelled=true, updated_at=now()
where shift_date >= current_date and not (shift_id = any(%s));
"""


def dry_run(model):
    people, positions, categories, shifts = build_rows(model)
    print(f"people     : {len(people)}")
    print(f"positions  : {len(positions)}")
    print(f"categories : {len(categories)}")
    for cat, name, mode in categories:
        print(f"   {cat:8} {name:20} -> {mode}")
    print(f"shifts     : {len(shifts)}")
    if people:
        print("\nsample person row :", people[0])
    if shifts:
        print("sample shift row  :", shifts[0])
    print("\n(dry run -- nothing written)")


def load(model, dsn):
    import psycopg
    people, positions, categories, shifts = build_rows(model)
    seen_ids = [s[0] for s in shifts]
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(PEOPLE_SQL, people)
            cur.executemany(POSITIONS_SQL, positions)
            cur.executemany(CATEGORIES_SQL, categories)
            cur.executemany(SHIFTS_SQL, shifts)
            cur.execute(CANCEL_SQL, (seen_ids,))
            cancelled = cur.rowcount
            cur.execute(
                "insert into sync_runs (people_count, shift_count, ok, message) "
                "values (%s, %s, true, %s);",
                (len(people), len(shifts), f"{cancelled} future shift(s) cancelled"))
        conn.commit()
    print(f"loaded: {len(people)} people, {len(positions)} positions, "
          f"{len(categories)} categories, {len(shifts)} shifts "
          f"({cancelled} future shift(s) marked cancelled)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", nargs="?", default="model.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args()

    with open(args.model, encoding="utf-8") as f:
        model = json.load(f)

    if args.dry_run:
        dry_run(model)
        return
    if not args.database_url:
        sys.exit("Set DATABASE_URL (or pass --database-url), or use --dry-run.")
    load(model, args.database_url)


if __name__ == "__main__":
    main()
