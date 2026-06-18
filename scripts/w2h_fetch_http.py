#!/usr/bin/env python3
"""Browser-free WhenToHelp fetcher.

Logs in and downloads the helper + shift CSV exports using plain HTTP
(requests) -- no Playwright, no Chromium. Then runs the same parser as
w2h_fetch.py to produce model.json.

Discovered by capturing the real request sequence: W2H's "export" is just an
authenticated GET to w2h.dll/mgrexportemplist with a Template, a date range,
and a set of column flags. Both exports use the same endpoint:
  - helpers : Template=ExportEmpList  (no dates)
  - shifts  : Template=exporthistory  (StartDate/EndDate, MM/DD/YYYY)

Env vars:  W2H_USER, W2H_PASS
Usage:     python w2h_fetch_http.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
Then:      python w2h_load.py
"""

import os
import re
import sys
import uuid
import json
import pathlib
import argparse
from datetime import date, timedelta

import requests

BASE = "https://whentohelp.com/cgi-bin/w2h.dll"
LOGIN_PAGE = "https://whentohelp.com/logins.htm"
USER = os.environ.get("W2H_USER")
PASS = os.environ.get("W2H_PASS")
WINDOW_DAYS = 60
OUT = pathlib.Path("downloads")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Column flags, in the order W2H's own UI sent them (order isn't significant,
# but we keep it identical to what we captured for fidelity).
HELPER_FLAGS = ["F1", "F4", "F17", "F5", "F6", "F7", "F8", "F9",
                "F10", "F11", "F12", "F13"]
SHIFT_FLAGS = ["F1", "F13", "F2", "F14", "F3", "F15", "F4", "F16", "F5", "F17",
               "F6", "F7", "F19", "F8", "F20", "F9", "F21", "F10", "F11", "F12"]


def login(session):
    """POST the sign-in form and return the session id (SID)."""
    session.get(LOGIN_PAGE, timeout=30)  # establish any initial cookies
    r = session.post(
        f"{BASE}/login",
        data={
            "name": "signin", "Launch": "", "LaunchParams": "",
            "UserId1": USER, "Password1": PASS,
            "captcha_required": "false", "Submit1": "Please Wait...",
        },
        headers={"Referer": LOGIN_PAGE},
        timeout=30, allow_redirects=True,
    )
    # The SID comes back in the resulting URL (.../home?SID=...) or the body.
    m = re.search(r"SID=([0-9A-Za-z]+)", r.url) or re.search(r"SID=([0-9A-Za-z]+)", r.text)
    if not m:
        raise SystemExit("Login failed: no SID in response. "
                         "Check W2H_USER / W2H_PASS (or W2H changed the login).")
    return m.group(1)


def export(session, sid, template, filename, flags, start=None, end=None):
    """Hit the export endpoint and save the returned CSV. Returns the path."""
    params = [
        ("SID", sid),
        ("CallerId", str(uuid.uuid4())),   # client-generated; any uuid works
        ("Template", template),
        ("StartDate", start or ""),
        ("EndDate", end or ""),
        ("Classic", "N"),
        ("coids", ""),
    ]
    params += [(f, "Y") for f in flags]
    params += [("Filename", filename), ("Format", "CSV")]

    r = session.get(f"{BASE}/mgrexportemplist", params=params, timeout=180)
    ct = r.headers.get("content-type", "").lower()
    if "csv" not in ct:
        snippet = r.text[:200].replace("\n", " ")
        raise SystemExit(f"{filename}: expected CSV but got '{ct}'. "
                         f"Session/SID problem? First bytes: {snippet!r}")
    OUT.mkdir(exist_ok=True)
    path = OUT / filename
    path.write_bytes(r.content)  # keep raw bytes; the parser detects encoding
    print(f"saved {path}  ({len(r.content):,} bytes)")
    return path


def main():
    print("=== w2h_fetch_http (no browser) ===")
    if not (USER and PASS):
        sys.exit("Set W2H_USER and W2H_PASS environment variables first.")

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="shift window start, YYYY-MM-DD (default: today)")
    ap.add_argument("--end", help="shift window end, YYYY-MM-DD (default: today + 60)")
    args = ap.parse_args()
    start = date.fromisoformat(args.start) if args.start else date.today()
    end = date.fromisoformat(args.end) if args.end else date.today() + timedelta(days=WINDOW_DAYS)
    us = lambda d: d.strftime("%m/%d/%Y")  # W2H wants MM/DD/YYYY

    s = requests.Session()
    s.headers["User-Agent"] = UA
    sid = login(s)
    print(f"logged in (SID {sid})")

    helpers = export(s, sid, "ExportEmpList", "helpers.csv", HELPER_FLAGS)
    shifts = export(s, sid, "exporthistory", "shifts.csv", SHIFT_FLAGS,
                    start=us(start), end=us(end))
    print(f"shift window {start} -> {end}")

    try:
        from w2h_parse import parse
        model = parse(str(shifts), str(helpers))
        json.dump(model, open("model.json", "w", encoding="utf-8"),
                  indent=2, ensure_ascii=False)
        print(f"parsed -> model.json  "
              f"({len(model['people'])} people, {len(model['shifts'])} shifts)")
        for key, items in model.get("_warnings", {}).items():
            if items:
                print(f"  {key}: {items}")
    except Exception as e:
        print(f"downloads OK; parse step reported: {e}")


if __name__ == "__main__":
    main()
