#!/usr/bin/env python3
"""
w2h_fetch.py -- log into W2H and download the shift + helper CSV exports,
then run them through w2h_parse.py to produce model.json.

Each export opens a popup where columns are chosen, the file is named, and
"Create Export File" triggers the download. The shift export may also show a
"What timeframe?" confirm dialog (when your date window differs from the
report's saved range) -- the script picks your window when that appears.

Runs HEADED by default so you can watch. Set HEADLESS = True for the scheduled
run (on the small always-on worker; a headless browser won't run on most
serverless crons).

Setup (once):
  python -m pip install playwright
  python -m playwright install chromium

Run:
  $env:W2H_USER='...'   ; $env:W2H_PASS='...'   (PowerShell)
  python w2h_fetch.py
"""

import json
import os
import re
import sys
import pathlib
import argparse
from datetime import date, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DOWNLOAD_DIR = pathlib.Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

USER = os.environ.get("W2H_USER")
PASS = os.environ.get("W2H_PASS")
HEADLESS = False      # watch first; flip to True for the scheduled run
WINDOW_DAYS = 60      # how far ahead to pull shifts

# Column checkboxes selected in the export popups (from the recording). They
# decide which columns the CSV has, so they must cover what w2h_parse.py needs.
SHIFT_COLS = ["F7", "F8", "F16", "F17", "F19", "F20", "F21"]
HELPER_COLS = ["F17"]


def login(page):
    page.goto("https://whentohelp.com/logins.htm")
    page.get_by_role("textbox", name="username").fill(USER)
    page.get_by_role("textbox", name="password").fill(PASS)
    page.get_by_role("button", name="SIGN IN").click()
    page.wait_for_load_state("networkidle")
    if "logins.htm" in page.url.lower():
        raise SystemExit("Login failed -- still on the sign-in page. Check W2H_USER / W2H_PASS.")
    print("logged in:", page.url)


def open_popup(ctx, page, trigger, confirm_text=None, timeout_ms=10000):
    """Run `trigger` (a click) and return the export popup window. Handles two
    cases: the popup opens directly, or an in-page confirm dialog appears first
    (click the button whose text contains confirm_text, then the popup opens)."""
    before = set(ctx.pages)

    def new_page():
        extra = [p for p in ctx.pages if p not in before]
        return extra[-1] if extra else None

    trigger()
    # Popup may open immediately...
    for _ in range(5):
        if new_page():
            break
        page.wait_for_timeout(200)
    # ...otherwise a confirm dialog may be intercepting -- choose our option.
    # The page has 3 dialog widgets (#customalert/#customconfirm/#customprompt),
    # each with a .button-done; the timeframe confirm is the one in #customconfirm.
    if not new_page():
        clicked = False
        try:
            btn = page.locator("#customconfirm button.button-done")
            btn.wait_for(state="visible", timeout=3000)
            btn.click(); clicked = True
        except PWTimeout:
            pass
        if not clicked:
            try:
                page.locator("button.button-done:visible").first.click(); clicked = True
            except Exception:
                pass
        if not clicked and confirm_text is not None:
            try:
                page.get_by_role("button", name=confirm_text).first.click()
            except Exception:
                pass
    waited = 0
    while waited < timeout_ms:
        p = new_page()
        if p:
            p.wait_for_load_state()
            return p
        page.wait_for_timeout(200)
        waited += 200
    raise RuntimeError("export popup did not open")


def create_export(pop, columns, filename):
    for col in columns:
        pop.locator(f'input[name="{col}"]').check()
    box = pop.get_by_role("textbox")
    box.click(); box.press("Home"); box.fill(filename)
    with pop.expect_download() as dl_info:
        pop.get_by_role("button", name="Create Export File").click()
    return dl_info.value


def fetch_helpers(ctx, page):
    page.get_by_role("link", name="HELPERS", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.locator("#myGrid-header-0-0-box-marker").click()          # select all rows
    pop = open_popup(ctx, page,
                     lambda: page.get_by_role("cell", name="Export", exact=True).click())
    download = create_export(pop, HELPER_COLS, "helpers.csv")
    dest = DOWNLOAD_DIR / "helpers.csv"
    download.save_as(dest)
    pop.close()
    print("saved", dest)
    return dest


def fetch_shifts(ctx, page, start, end):
    start_us = start.strftime("%m/%d/%Y")                          # for the confirm button
    page.get_by_role("link", name="REPORTS", exact=True).click()
    page.get_by_role("link", name="Custom Reports").click()
    page.wait_for_load_state("networkidle")
    # Select the custom report (by grid position -- re-record if you reorder reports)
    page.locator("#myGrid-cell-5-1").click()
    page.locator('input[name="StartDateOvr"]').fill(start.isoformat())
    page.locator('input[name="EndDateOvr"]').fill(end.isoformat())
    # Clicking the export icon may raise a "What timeframe?" dialog; pick ours.
    # (Only happens for non-default ranges, e.g. backfills. Long timeout lets you
    # click the override-range option by hand if the auto-match misses.)
    print(">>> If a 'What timeframe?' dialog appears, click the override-range "
          f"button (showing {start_us}) -- the script will continue automatically.")
    pop = open_popup(ctx, page,
                     lambda: page.locator("#myGrid-cell-3-1-box-image").click(),
                     confirm_text=start_us, timeout_ms=60000)
    pop.get_by_text("Category (Long)").click()
    download = create_export(pop, SHIFT_COLS, "shifts.csv")
    dest = DOWNLOAD_DIR / "shifts.csv"
    download.save_as(dest)
    pop.close()
    print(f"saved {dest}  (window {start.isoformat()} -> {end.isoformat()})")
    return dest


def main():
    print("=== w2h_fetch v3 (dialog-fix2 + --capture) ===")
    if not (USER and PASS):
        sys.exit("Set W2H_USER and W2H_PASS environment variables first.")

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="shift window start, YYYY-MM-DD (default: today)")
    ap.add_argument("--end", help="shift window end, YYYY-MM-DD (default: today + WINDOW_DAYS)")
    ap.add_argument("--capture", action="store_true",
                    help="log every w2h.dll request and save a HAR, to rebuild as plain HTTP")
    args = ap.parse_args()
    start = date.fromisoformat(args.start) if args.start else date.today()
    end = date.fromisoformat(args.end) if args.end else date.today() + timedelta(days=WINDOW_DAYS)

    SKIP = (".css", ".js", ".png", ".gif", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".svg")
    def log_req(req):
        u = req.url
        if any(u.split("?")[0].endswith(e) for e in SKIP):
            return
        print(f">> {req.method} {u}")
        if req.method == "POST":
            try:
                pd = req.post_data or ""
            except Exception:
                pd = ""
            pd = re.sub(r"(Password1=)[^&]*", r"\1REDACTED", pd)
            if pd:
                print("   data:", pd[:1500])
    def log_resp(resp):
        h = resp.headers
        ct, cd = h.get("content-type", ""), h.get("content-disposition", "")
        if "csv" in ct or "attachment" in cd or "octet-stream" in ct:
            print(f"<< {resp.status} {resp.url}  [{ct}] {cd}")
    def wire(pg):
        pg.on("request", log_req); pg.on("response", log_resp)

    shifts = helpers = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=400)
        ctx_kwargs = {"accept_downloads": True}
        if args.capture:
            ctx_kwargs["record_har_path"] = "w2h_capture.har"
        ctx = browser.new_context(**ctx_kwargs)
        if args.capture:
            ctx.on("page", wire)           # popups
        page = ctx.new_page()
        if args.capture:
            wire(page)
        login(page)
        try:
            helpers = fetch_helpers(ctx, page)
        except Exception as e:
            print("helper export failed:", e)
        try:
            shifts = fetch_shifts(ctx, page, start, end)
        except Exception as e:
            print("shift export failed:", e)
        browser.close()

    if shifts and helpers:
        try:
            from w2h_parse import parse
            model = parse(str(shifts), str(helpers))
            json.dump(model, open("model.json", "w", encoding="utf-8"),
                      indent=2, ensure_ascii=False)
            print(f"parsed -> model.json  "
                  f"({len(model['people'])} people, {len(model['shifts'])} shifts)")
            for key, items in model["_warnings"].items():
                if items:
                    print(f"  {key}: {items}")
        except Exception as e:
            print(f"downloads OK; parse step reported: {e}")


if __name__ == "__main__":
    main()
