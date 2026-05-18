#!/usr/bin/env python3
"""
Home Care Post Blog — Live Link Writer
Runs every Monday at 9:15 AM PDT via GitHub Actions.
Pulls live post links from ContentStudio (Home Care Post workspace)
and writes them to the Home Care Post Google Sheet tab.
Post slot: 9 AM PDT (16:00 UTC).
"""

import sys
import json
import os
import urllib.request
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# ── Config ─────────────────────────────────────────────────────────────────────
CS_API_KEY  = "cs_bd757a81fb4869ba556922297a3601033b337f8feb97421acad22a3ca70f3369"
CS_API_BASE = "https://api.contentstudio.io/api/v1"
WS_ID       = "6871327b366a03cd260d3441"  # Home Care Post

SHEET_ID    = "1Kslp63_DcckDguJW_ABipaC-Vdl5FzFVhGRUWYd6tSc"
SHEET_GID   = "1930398898"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CHANNEL_MAP = {
    "HCP FB":  ("facebook",  "Home Care Post"),
    "HCP LI":  ("linkedin",  "Home Care Post"),
    "HCP X":   ("twitter",   "homecarepost"),
    "HCP IG":  ("instagram", "Home Care Post"),
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


def get_hcp_posts(date_str, all_posts):
    """Return posts at 9 AM PDT (16:00 UTC) on date_str."""
    return [
        p for p in all_posts
        if p.get("scheduling", {}).get("execute_time", "").startswith(f"{date_str}T16:00")
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
    """Find the 1-based row number for date_str in column A."""
    sheet_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m/%d")
    rows = worksheet.get_all_values()
    for i, row in enumerate(rows):
        if row[0].strip() == sheet_date:
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
        "F": [("HCP FB", links.get("HCP FB"))],
        "G": [("HCP LI", links.get("HCP LI"))],
        "H": [("HCP X",  links.get("HCP X"))],
        "I": [("HCP IG", links.get("HCP IG"))],
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
    print(f"  Home Care Post Blog — Live Link Writer")
    print(f"  Date: {date_str}")
    print("=" * 58)

    print("\nFetching posts from ContentStudio...")
    all_posts = fetch_all_posts()
    hcp_posts = get_hcp_posts(date_str, all_posts)
    print(f"Found {len(hcp_posts)} Home Care Post(s) at 9 AM PDT")

    if not hcp_posts:
        print("[STOP] No Home Care Post found for this date.")
        sys.exit(0)

    links = extract_links(hcp_posts)
    print("\nLinks from API:")
    for label, url in links.items():
        print(f"  {label}: {url}")

    print("\nConnecting to Google Sheet...")
    spreadsheet, worksheet = get_worksheet()

    print("\nFinding row in Google Sheet...")
    row_num = find_row_number(worksheet, date_str)
    if not row_num:
        print(f"[STOP] Could not find row for {date_str} in sheet.")
        sys.exit(1)
    print(f"Found at row {row_num}")

    print("\nWriting links to sheet...")
    write_links_to_sheet(spreadsheet, row_num, links)

    print("\n" + "=" * 58)
    print("  DONE — Links written to sheet.")
    print("=" * 58)


if __name__ == "__main__":
    main()
