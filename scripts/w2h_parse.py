#!/usr/bin/env python3
"""
Parse the two W2H exports into the sign-in/out app's model.

Inputs
  shift report  (REPORTS -> custom / Assigned Shift Details)  -- the shifts
  helper export (Helpers / Employees list)                    -- people + email

It joins them on `Helper Number` and emits a normalized structure:
  people, positions, categories, shifts
which maps 1:1 onto the database tables we designed. The Category code drives
how each shift is signed into (kiosk on site vs emailed magic link remotely).

Usage
  python w2h_parse.py SHIFTS.csv HELPERS.csv [-o model.json]

Notes
  * The helper export is Windows-1252 encoded (accented names); the reader
    handles that automatically.
  * `Helper Number` and `Shift ID` are stable ids -> use them as upsert keys.
"""

import argparse
import csv
import json
from datetime import datetime

# Category code (the "Cat" column) -> how that shift is signed into.
#   "kiosk"  : on-site, pick your name, no login   (the Batawa hill shifts)
#   "remote" : emailed magic link, sign in on phone (Special Events)
#   "ignore" : informational calendar markers, not a real sign-in shift
# Batawa categories only appear in winter exports; add their codes here as you
# see them, e.g. "BSL": "kiosk".  Anything unmapped is flagged for review
# rather than silently routed the wrong way.
CATEGORY_MODE = {
    "SE": "remote",
    "EVENTS": "ignore",
}
DEFAULT_MODE = "review"


def read_csv(path):
    """W2H exports vary between UTF-8 and Windows-1252; try both."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    with open(path, encoding="cp1252", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


def to_iso(date_s, time_s):
    date_s, time_s = (date_s or "").strip(), (time_s or "").strip()
    if not date_s or not time_s:
        return None
    return datetime.strptime(f"{date_s} {time_s}", "%m/%d/%Y %I:%M %p").isoformat()


# Only the columns the app genuinely can't work without. Everything else is
# read with .get() so a different export column selection won't break parsing.
SHIFT_REQUIRED = ["Shift ID", "Helper Number", "Cat", "Category",
                  "Date", "Start Time", "End Time"]
HELPER_REQUIRED = ["Helper Number", "Helper Name", "Email"]


def _require(rows, needed, label):
    have = set(rows[0].keys()) if rows else set()
    missing = [c for c in needed if c not in have]
    if missing:
        raise ValueError(
            f"{label} export is missing columns {missing}. "
            f"Got: {sorted(have)}. Adjust the column checkboxes in w2h_fetch.py.")


def parse(shift_path, helper_path):
    shift_rows = read_csv(shift_path)
    helper_rows = read_csv(helper_path)
    _require(shift_rows, SHIFT_REQUIRED, "shift")
    _require(helper_rows, HELPER_REQUIRED, "helper")

    # People come from the helper export (it has the email the magic link needs).
    people = {}
    for h in helper_rows:
        name = h["Helper Name"].strip()
        first = (h.get("First Name") or name.split(" ")[0]).strip()
        last = (h.get("Last Name") or " ".join(name.split(" ")[1:])).strip()
        people[h["Helper Number"]] = {
            "helper_number": h["Helper Number"],
            "name": name,
            "first_name": first,
            "last_name": last,
            "email": h["Email"].strip(),
            "phone": (h.get("Phone") or "").strip(),
            "cell": (h.get("Cell") or "").strip(),
            "city": (h.get("City") or "").strip(),
        }

    positions, categories, shifts = {}, {}, []
    unknown_categories = set()

    for s in shift_rows:
        cat = (s.get("Cat") or "").strip()
        catname = (s.get("Category") or "").strip()
        mode = CATEGORY_MODE.get(cat)
        if mode is None:
            # Batawa categories are on-site -> kiosk; anything else unknown -> review.
            mode = "kiosk" if catname.lower().startswith("batawa") else DEFAULT_MODE
        if cat not in CATEGORY_MODE and not catname.lower().startswith("batawa"):
            unknown_categories.add((cat, catname))

        pos_id = (s.get("Position ID") or "").strip()
        pos_name = (s.get("Position Name") or "").strip()
        if pos_id:
            positions.setdefault(pos_id, {"position_id": pos_id, "name": pos_name})
        categories.setdefault(cat, {
            "cat": cat,
            "name": (s.get("Category") or "").strip(),
            "mode": mode,
        })

        hn = s["Helper Number"]
        try:
            duration = float((s.get("Duration") or "0").strip() or 0)
        except ValueError:
            duration = None
        shifts.append({
            "shift_id": s["Shift ID"],
            "schedule_id": (s.get("Schedule ID") or "").strip(),
            "helper_number": hn,
            "helper_name": people.get(hn, {}).get("name", (s.get("Helper Name") or "").strip()),
            "email": people.get(hn, {}).get("email", ""),
            "position_id": pos_id,
            "position_name": pos_name,
            "cat": cat,
            "category": (s.get("Category") or "").strip(),
            "signin_mode": mode,
            "description": (s.get("Shift Description") or "").strip(),
            "date": s["Date"].strip(),
            "start": to_iso(s["Date"], s["Start Time"]),
            "end": to_iso(s["Date"], s["End Time"]),
            "duration_hours": duration,
            "helper_found": hn in people,
        })

    return {
        "people": list(people.values()),
        "positions": list(positions.values()),
        "categories": list(categories.values()),
        "shifts": shifts,
        "_warnings": {
            "unknown_categories": sorted(unknown_categories),
            "shifts_with_missing_helper": [s["shift_id"] for s in shifts if not s["helper_found"]],
            "remote_shifts_without_email": [
                s["shift_id"] for s in shifts
                if s["signin_mode"] == "remote" and not s["email"]
            ],
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shifts")
    ap.add_argument("helpers")
    ap.add_argument("-o", "--out", default="model.json")
    args = ap.parse_args()

    model = parse(args.shifts, args.helpers)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, ensure_ascii=False)

    print(f"people     : {len(model['people'])}")
    print(f"positions  : {len(model['positions'])}")
    print(f"categories : {len(model['categories'])}")
    for c in model["categories"]:
        print(f"   {c['cat']:8} {c['name']:20} -> {c['mode']}")
    print(f"shifts     : {len(model['shifts'])}")
    w = model["_warnings"]
    if w["unknown_categories"]:
        print("\n  REVIEW -- categories with no sign-in mode set:")
        for cat, name in w["unknown_categories"]:
            print(f"    {cat!r} ({name}) -- add to CATEGORY_MODE as kiosk/remote/ignore")
    if w["shifts_with_missing_helper"]:
        print(f"\n  WARNING -- shifts whose helper isn't in the helper export: {w['shifts_with_missing_helper']}")
    if w["remote_shifts_without_email"]:
        print(f"\n  WARNING -- remote shifts with no email (can't send a link): {w['remote_shifts_without_email']}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
