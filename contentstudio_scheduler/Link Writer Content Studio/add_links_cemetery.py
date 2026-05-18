#!/usr/bin/env python3
"""
Cemetery Blog — Live Link Writer
Runs every Monday at 7:15 AM PST via GitHub Actions.
Pulls live post links from ContentStudio and writes them to the PB Blogs 2026 Google Sheet.
"""

import sys
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta

import gspread
from google.oauth2.service_account import Credentials

# ── Config ─────────────────────────────────────────────────────────────────────
CS_API_KEY  = "cs_bd757a81fb4869ba556922297a3601033b337f8feb97421acad22a3ca70f3369"
CS_API_BASE = "https://api.contentstudio.io/api/v1"
WS_ID       = "66be2a6a2c16646ddc0d01c7"  # Ring Ring Marketing

SHEET_ID    = "1Kslp63_DcckDguJW_ABipaC-Vdl5FzFVhGRUWYd6tSc"
SHEET_GID   = "42049175"

CS_EMAIL    = "jhammy@ringringmarketing.com"
CS_PASSWORD = "RRM2023"

PST_OFFSET  = timedelta(hours=7)  # UTC-7 (PDT); change to 8 in standard time

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CHANNEL_MAP = {
    "Welton FB": ("facebook", "Welton Hong"),
    "RRM FB":    ("facebook", "Ring Ring Marketing"),
    "Welton LI": ("linkedin", "Welton Hong"),
    "RRM LI":    ("linkedin", "Ring Ring Marketing"),
    "Welton X":  ("twitter",  "weltonhong"),
    "RRM X":     ("twitter",  "RingMarketing"),
    "Welton IG": ("instagram","Welton Hong"),
}


# ── ContentStudio helpers ──────────────────────────────────────────────────────
def cs_get(path):
    req = urllib.request.Request(
        f"{CS_API_BASE}{path}",
        headers={"X-API-Key": CS_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_all_posts():
    all_posts = []
    for status in ("scheduled", "published", "review", "draft"):
        for page in range(1, 20):
            try:
                data = cs_get(f"/workspaces/{WS_ID}/posts?status={status}&page={page}")
                all_posts.extend(data.get("data", []))
                if page >= data.get("last_page", 1):
                    break
            except Exception:
                break
    return all_posts


def get_cemetery_posts(date_str, all_posts):
    """Return posts scheduled at 7 AM PST (14:00 UTC) on date_str."""
    return [
        p for p in all_posts
        if p.get("scheduling", {}).get("execute_time", "").startswith(f"{date_str}T14:00")
    ]


def extract_links(posts):
    """Build a dict: channel_label → live_url from ContentStudio API accounts."""
    links = {}
    for post in posts:
        for account in post.get("accounts", []):
            platform = account.get("platform", "")
            name     = account.get("name", "")
            url      = account.get("post_link", "")
            for label, (plat_frag, name_frag) in CHANNEL_MAP.items():
                if platform == plat_frag and name_frag.lower() in name.lower():
                    if url:
                        links[label] = url
    return links


# ── Playwright — get RRM LinkedIn link ────────────────────────────────────────
def get_rrm_linkedin_via_playwright(post_id):
    """Log into ContentStudio via Playwright and scrape the RRM LI live link."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [WARN] Playwright not installed — RRM LI link skipped.")
        return None

    post_url = f"https://app.contentstudio.io/ring-ring-marketing/publisher/planner/list-view?plan_ids={post_id}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto("https://app.contentstudio.io/login")
        time.sleep(3)
        page.keyboard.press("Escape")
        time.sleep(1)
        page.fill('input[placeholder="Email Address"]', CS_EMAIL)
        page.fill('input[placeholder="Password"]', CS_PASSWORD)
        page.press('input[placeholder="Password"]', "Enter")
        time.sleep(6)

        page.goto(post_url)
        time.sleep(6)
        try:
            page.click("text=See how local search", timeout=5000)
        except Exception:
            pass
        time.sleep(4)

        li_links = [
            a.get_attribute("href")
            for a in page.query_selector_all('a[href*="linkedin.com"]')
            if a.get_attribute("href")
        ]
        browser.close()

    return li_links[0] if li_links else None


# ── Google Sheets helpers ──────────────────────────────────────────────────────
def _load_creds_info():
    import re
    raw = os.environ["GOOGLE_CREDENTIALS_JSON"].lstrip('﻿')
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        fixed = re.sub(r'\\(\r?\n)', r'\1', raw)
        try:
            info = json.loads(fixed, strict=False)
        except json.JSONDecodeError:
            info = json.loads(fixed.replace('\r', ''), strict=False)
    if 'private_key' in info:
        pk = info['private_key']
        pk = pk.replace('\\n', '\n').replace('\r\n', '\n').replace('\r', '\n')
        info['private_key'] = pk
    return info


def get_worksheet():
    creds = Credentials.from_service_account_info(
        _load_creds_info(),
        scopes=SCOPES,
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    for ws in spreadsheet.worksheets():
        if str(ws.id) == SHEET_GID:
            return spreadsheet, ws
    raise ValueError(f"Worksheet with gid {SHEET_GID} not found")


def find_row_number(worksheet, date_str):
    """Find the 1-based row number for the Cemetery row on date_str."""
    sheet_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m/%d")
    rows = worksheet.get_all_values()
    for i, row in enumerate(rows):
        if row[0].strip() == sheet_date and "cemetery" in " ".join(row).lower():
            return i + 1
    return None


def write_hyperlink_cell(spreadsheet, row_num, col_letter, items):
    """Write items as rich text with hyperlinks into a single cell via Sheets API."""
    col_idx = ord(col_letter.upper()) - ord('A')
    row_idx = row_num - 1

    full_text = ""
    format_runs = []

    for i, (text, url) in enumerate(items):
        if i > 0:
            sep_start = len(full_text)
            full_text += ", "
            format_runs.append({"startIndex": sep_start, "format": {}})

        item_start = len(full_text)
        full_text += text

        if url:
            format_runs.append({
                "startIndex": item_start,
                "format": {
                    "link": {"uri": url},
                    "underline": True,
                    "foregroundColorStyle": {
                        "rgbColor": {"red": 0.067, "green": 0.329, "blue": 0.651}
                    }
                }
            })
        else:
            format_runs.append({"startIndex": item_start, "format": {}})

    spreadsheet.batch_update({
        "requests": [{
            "updateCells": {
                "rows": [{
                    "values": [{
                        "userEnteredValue": {"stringValue": full_text},
                        "textFormatRuns": format_runs
                    }]
                }],
                "fields": "userEnteredValue,textFormatRuns",
                "range": {
                    "sheetId": int(SHEET_GID),
                    "startRowIndex": row_idx,
                    "endRowIndex": row_idx + 1,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1
                }
            }
        }]
    })


def write_links_to_sheet(spreadsheet, row_num, links):
    columns = {
        "F": [("Welton FB", links.get("Welton FB")), ("RRM FB", links.get("RRM FB"))],
        "G": [("Welton LI", links.get("Welton LI")), ("RRM LI", links.get("RRM LI"))],
        "H": [("Welton X",  links.get("Welton X")),  ("RRM X",  links.get("RRM X"))],
        "I": [("Welton IG", links.get("Welton IG"))],
    }

    for col, channel_links in columns.items():
        valid = [(text, url) for text, url in channel_links if url]
        if not valid:
            print(f"  [SKIP] {col}{row_num} — no links available")
            continue
        print(f"  Writing {col}{row_num}: {[t for t, _ in valid]}")
        write_hyperlink_cell(spreadsheet, row_num, col, valid)

    print("  Sheet updated.")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) >= 2:
        date_str = sys.argv[1]
        if "/" in date_str:
            m, d = date_str.split("/")
            date_str = f"2026-{m.zfill(2)}-{d.zfill(2)}"
    else:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"ERROR: Invalid date '{date_str}'. Use YYYY-MM-DD or MM/DD.")
        sys.exit(1)

    print("=" * 58)
    print(f"  Cemetery Blog — Live Link Writer")
    print(f"  Date: {date_str}")
    print("=" * 58)

    print("\nFetching posts from ContentStudio...")
    all_posts = fetch_all_posts()
    cemetery_posts = get_cemetery_posts(date_str, all_posts)
    print(f"Found {len(cemetery_posts)} cemetery post(s) at 7 AM PST")

    if not cemetery_posts:
        print("[STOP] No cemetery posts found for this date.")
        sys.exit(0)

    links = extract_links(cemetery_posts)
    print("\nLinks from API:")
    for label, url in links.items():
        print(f"  {label}: {url}")

    if "RRM LI" not in links:
        print("\nRRM LI not in API — fetching via Playwright...")
        for post in cemetery_posts:
            platforms = [a["platform"] for a in post.get("accounts", [])]
            if "linkedin" in platforms and "facebook" in platforms:
                post_id   = post["id"]
                welton_li = links.get("Welton LI", "")
                candidate = get_rrm_linkedin_via_playwright(post_id)
                if candidate and candidate != welton_li:
                    links["RRM LI"] = candidate
                elif candidate:
                    links["RRM LI"] = candidate
                print(f"  RRM LI: {links.get('RRM LI', 'NOT FOUND')}")
                break

    print("\nConnecting to Google Sheet...")
    spreadsheet, worksheet = get_worksheet()

    print("\nFinding row in Google Sheet...")
    row_num = find_row_number(worksheet, date_str)
    if not row_num:
        print(f"[STOP] Could not find Cemetery row for {date_str} in sheet.")
        sys.exit(1)
    print(f"Found at row {row_num}")

    print("\nWriting links to sheet...")
    write_links_to_sheet(spreadsheet, row_num, links)

    print("\n" + "=" * 58)
    print("  DONE — Links written to sheet.")
    print("=" * 58)


if __name__ == "__main__":
    main()
