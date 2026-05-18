#!/usr/bin/env python3
"""
Windows & Doors / Window Covering Blog — Live Link Writer
Runs every Monday at 2:15 PM PDT via GitHub Actions.
Pulls live post links from ContentStudio (RRMathome workspace) and writes
them to the PB Blogs 2026 Google Sheet.
Two post slots: 1 PM PDT (20:00 UTC) and 2 PM PDT (21:00 UTC) — same row.
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
WS_ID       = "66be5e278c3e19aae4051bc5"  # RRMathome

SHEET_ID    = "1Kslp63_DcckDguJW_ABipaC-Vdl5FzFVhGRUWYd6tSc"
SHEET_GID   = "42049175"

CS_EMAIL    = "jhammy@ringringmarketing.com"
CS_PASSWORD = "RRM2023"

PST_OFFSET  = timedelta(hours=7)  # UTC-7 (PDT)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CHANNEL_MAP = {
    "Welton FB":     ("facebook",  "Welton Hong"),
    "RRMathome FB":  ("facebook",  "RRM At Home"),
    "Welton LI":     ("linkedin",  "Welton Hong"),
    "RRMathome LI":  ("linkedin",  "RRM@home"),
    "Welton X":      ("twitter",   "weltonhong"),
    "RRMathome X":   ("twitter",   "rrmathome"),
    "Welton IG":     ("instagram", "Welton Hong"),
    "RRMathome IG":  ("instagram", "RRM@home"),
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
    seen = {}
    for status in ("scheduled", "published", "review", "draft"):
        for page in range(1, 20):
            try:
                data = cs_get(f"/workspaces/{WS_ID}/posts?status={status}&page={page}")
                for p in data.get("data", []):
                    pid = p.get("id")
                    has_link = any(a.get("post_link") for a in p.get("accounts", []))
                    if pid not in seen or has_link:
                        seen[pid] = p
                if page >= data.get("last_page", 1):
                    break
            except Exception:
                break
    return list(seen.values())


def get_wd_wc_posts(date_str, all_posts):
    """Return WD/WC posts at 12 PM PDT (19:00 UTC), 1 PM PDT (20:00 UTC), or 2 PM PDT (21:00 UTC) on date_str."""
    return [
        p for p in all_posts
        if (p.get("scheduling", {}).get("execute_time", "").startswith(f"{date_str}T19:00")
            or p.get("scheduling", {}).get("execute_time", "").startswith(f"{date_str}T20:00")
            or p.get("scheduling", {}).get("execute_time", "").startswith(f"{date_str}T21:00"))
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


# ── Playwright — get RRMathome LinkedIn link ───────────────────────────────────
def get_rrm_linkedin_via_playwright(post_id, welton_li_url, post_text=""):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [WARN] Playwright not installed — RRMathome LI link skipped.")
        return None

    post_url = f"https://app.contentstudio.io/rrmathome/publisher/planner/list-view?plan_ids={post_id}"

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

        clicked = False
        if post_text:
            snippet = post_text.strip()[:60]
            for attempt_text in [snippet, snippet[:40], snippet[:25]]:
                try:
                    page.click(f'text={attempt_text}', timeout=4000)
                    time.sleep(4)
                    clicked = True
                    break
                except Exception:
                    continue

        if not clicked:
            for selector in ["td .post-content", "tbody tr td:nth-child(2)", "tbody tr"]:
                try:
                    page.click(selector, timeout=3000)
                    time.sleep(4)
                    break
                except Exception:
                    continue
        time.sleep(2)

        li_links = [
            a.get_attribute("href")
            for a in page.query_selector_all('a[href*="linkedin.com"]')
            if a.get_attribute("href")
        ]
        browser.close()

    for link in li_links:
        if link != welton_li_url:
            return link
    return li_links[0] if li_links else None


# ── Google Sheets helpers ──────────────────────────────────────────────────────
def _load_creds_info():
    return json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])


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
    """Find the 1-based row number for the WD/WC row on date_str."""
    sheet_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m/%d")
    rows = worksheet.get_all_values()

    date_row_idx = None
    for i, row in enumerate(rows):
        if row[0].strip() == sheet_date:
            date_row_idx = i
            break

    if date_row_idx is None:
        return None

    for i in range(date_row_idx, min(date_row_idx + 15, len(rows))):
        if len(rows[i]) > 2 and "wd/wc" in rows[i][2].lower():
            return i + 1

    return None


def write_hyperlink_cell(spreadsheet, row_num, col_letter, items):
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
        "F": [("Welton FB",    links.get("Welton FB")),    ("RRMathome FB",  links.get("RRMathome FB"))],
        "G": [("Welton LI",    links.get("Welton LI")),    ("RRMathome LI",  links.get("RRMathome LI"))],
        "H": [("Welton X",     links.get("Welton X")),     ("RRMathome X",   links.get("RRMathome X"))],
        "I": [("Welton IG",    links.get("Welton IG")),    ("RRMathome IG",  links.get("RRMathome IG"))],
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
    print(f"  WD & WC Blog — Live Link Writer")
    print(f"  Date: {date_str}")
    print("=" * 58)

    print("\nFetching posts from ContentStudio...")
    all_posts = fetch_all_posts()
    wd_wc_posts = get_wd_wc_posts(date_str, all_posts)
    print(f"Found {len(wd_wc_posts)} WD/WC post(s) at 1-2 PM PDT")

    if not wd_wc_posts:
        print("[STOP] No WD/WC posts found for this date.")
        sys.exit(0)

    links = extract_links(wd_wc_posts)
    print("\nLinks from API:")
    for label, url in links.items():
        print(f"  {label}: {url}")

    if "RRMathome LI" not in links:
        print("\nRRMathome LI not in API — fetching via Playwright...")
        for post in wd_wc_posts:
            platforms = [a["platform"] for a in post.get("accounts", [])]
            if "linkedin" in platforms and "facebook" in platforms:
                post_id   = post["id"]
                welton_li = links.get("Welton LI", "")
                post_text = post.get("common", {}).get("content", {}).get("text", "")
                candidate = get_rrm_linkedin_via_playwright(post_id, welton_li, post_text)
                if candidate:
                    links["RRMathome LI"] = candidate
                print(f"  RRMathome LI: {links.get('RRMathome LI', 'NOT FOUND')}")
                break
        time.sleep(5)

    print("\nConnecting to Google Sheet...")
    spreadsheet, worksheet = get_worksheet()

    print("\nFinding row in Google Sheet...")
    row_num = find_row_number(worksheet, date_str)
    if not row_num:
        print(f"[STOP] Could not find WD/WC row for {date_str} in sheet.")
        sys.exit(1)
    print(f"Found at row {row_num}")

    print("\nWriting links to sheet...")
    write_links_to_sheet(spreadsheet, row_num, links)

    print("\n" + "=" * 58)
    print("  DONE — Links written to sheet.")
    print("=" * 58)


if __name__ == "__main__":
    main()
