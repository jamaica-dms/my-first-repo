#!/usr/bin/env python3
"""
Funeral Home Blog — Live Link Writer
Runs every Monday at 8:15 AM PST.
Pulls live post links from ContentStudio and writes them to the PB Blogs 2026 Google Sheet.
Skips Welton PFB — that channel is handled manually.
"""

import sys
import json
import csv
import io
import time
import urllib.request
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────────────────────
CS_API_KEY  = "cs_bd757a81fb4869ba556922297a3601033b337f8feb97421acad22a3ca70f3369"
CS_API_BASE = "https://api.contentstudio.io/api/v1"
WS_ID       = "66be2a6a2c16646ddc0d01c7"  # Ring Ring Marketing

SHEET_ID    = "1Kslp63_DcckDguJW_ABipaC-Vdl5FzFVhGRUWYd6tSc"
SHEET_GID   = "42049175"
SHEET_URL   = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid={SHEET_GID}#{SHEET_GID}"

CS_EMAIL    = "jhammy@ringringmarketing.com"
CS_PASSWORD = "RRM2023"

EDGE_PROFILE = r"C:\Users\Jhammy\AppData\Local\Microsoft\Edge\User Data"

PST_OFFSET  = timedelta(hours=7)  # UTC-7 (PDT); change to 8 in standard time

# Channel → (platform, name fragment) — Welton PFB intentionally excluded
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


def get_funeral_posts(date_str, all_posts):
    """Return posts scheduled at 8 AM PST (15:00 UTC) on date_str."""
    return [
        p for p in all_posts
        if p.get("scheduling", {}).get("execute_time", "").startswith(f"{date_str}T15:00")
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
def get_rrm_linkedin_via_playwright(post_id, welton_li_url, post_text=""):
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

        # Try clicking via post text first (most reliable), then fall back to CSS selectors
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
            for selector in [
                "td .post-content",
                "tbody tr td:nth-child(2)",
                "tbody tr",
                ".social-post-row",
            ]:
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

    # Return the link that isn't Welton's
    for link in li_links:
        if link != welton_li_url:
            return link
    return li_links[0] if li_links else None


# ── Google Sheet helpers ───────────────────────────────────────────────────────
def find_row_number(date_str):
    """Find the 1-based row number and existing cell values for the Funeral Homes row on date_str."""
    sheet_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m/%d")
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        content = r.read().decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(content)))

    # Find the date row first, then look for Funeral Homes in subsequent rows
    date_row_idx = None
    for i, row in enumerate(rows):
        if row[0].strip() == sheet_date:
            date_row_idx = i
            break

    if date_row_idx is None:
        return None, {}

    # Search from date row onward for Funeral Homes
    for i in range(date_row_idx, min(date_row_idx + 15, len(rows))):
        if "funeral" in rows[i][2].lower():
            row_num = i + 1  # 1-based
            row_data = rows[i]
            # Map column letters to existing text (F=5, G=6, H=7, I=8, 0-indexed)
            existing = {}
            col_map = {"F": 5, "G": 6, "H": 7, "I": 8}
            for col, idx in col_map.items():
                if idx < len(row_data):
                    existing[f"{col}{row_num}"] = row_data[idx].strip()
            return row_num, existing

    return None, {}


def write_links_to_sheet(row_num, links, existing=None):
    from playwright.sync_api import sync_playwright

    if existing is None:
        existing = {}

    columns = {
        f"F{row_num}": [("Welton FB", links.get("Welton FB")), ("RRM FB", links.get("RRM FB"))],
        f"G{row_num}": [("Welton LI", links.get("Welton LI")), ("RRM LI", links.get("RRM LI"))],
        f"H{row_num}": [("Welton X",  links.get("Welton X")),  ("RRM X",  links.get("RRM X"))],
        f"I{row_num}": [("Welton IG", links.get("Welton IG"))],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=EDGE_PROFILE,
            channel="msedge",
            headless=True,
            args=["--profile-directory=Default"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.set_viewport_size({"width": 1800, "height": 900})
        page.goto(SHEET_URL, timeout=60000)
        time.sleep(6)

        for cell_ref, channel_links in columns.items():
            valid = [(text, url) for text, url in channel_links if url]
            if not valid:
                print(f"  [SKIP] {cell_ref} — no links available")
                continue

            has_existing = bool(existing.get(cell_ref, "").strip())
            mode = "append" if has_existing else "replace"
            print(f"  Writing {cell_ref} [{mode}]: {[t for t, _ in valid]}")

            name_box = page.locator('[id="t-name-box"], .name-box, [aria-label*="Name Box"]').first
            name_box.click(timeout=5000)
            page.keyboard.press("Control+a")
            page.keyboard.type(cell_ref)
            page.keyboard.press("Enter")
            time.sleep(1)

            page.keyboard.press("F2")
            time.sleep(0.5)
            if has_existing:
                # Move to end of existing content and add separator
                page.keyboard.press("End")
                time.sleep(0.2)
                page.keyboard.type(", ")
            else:
                page.keyboard.press("Control+a")
            time.sleep(0.3)

            for i, (text, url) in enumerate(valid):
                if i > 0:
                    page.keyboard.type(", ")
                page.keyboard.type(text)
                for _ in range(len(text)):
                    page.keyboard.press("Shift+ArrowLeft")
                time.sleep(0.3)
                page.keyboard.press("Control+k")
                time.sleep(2)
                page.keyboard.type(url)
                page.keyboard.press("Enter")
                time.sleep(1)

            page.keyboard.press("Enter")
            time.sleep(1)

        browser.close()
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
    print(f"  Funeral Home Blog — Live Link Writer")
    print(f"  Date: {date_str}")
    print("=" * 58)

    print("\nFetching posts from ContentStudio...")
    all_posts = fetch_all_posts()
    funeral_posts = get_funeral_posts(date_str, all_posts)
    print(f"Found {len(funeral_posts)} funeral home post(s) at 8 AM PST")

    if not funeral_posts:
        print("[STOP] No funeral home posts found for this date.")
        sys.exit(0)

    links = extract_links(funeral_posts)
    print("\nLinks from API:")
    for label, url in links.items():
        print(f"  {label}: {url}")

    if "RRM LI" not in links:
        print("\nRRM LI not in API — fetching via Playwright...")
        for post in funeral_posts:
            platforms = [a["platform"] for a in post.get("accounts", [])]
            if "linkedin" in platforms and "facebook" in platforms:
                post_id  = post["id"]
                welton_li = links.get("Welton LI", "")
                post_text = post.get("common", {}).get("content", {}).get("text", "")
                candidate = get_rrm_linkedin_via_playwright(post_id, welton_li, post_text)
                if candidate:
                    links["RRM LI"] = candidate
                print(f"  RRM LI: {links.get('RRM LI', 'NOT FOUND')}")
                break
        time.sleep(5)  # Let Edge release the profile lock before sheet writing

    print("\nFinding row in Google Sheet...")
    row_num, existing = find_row_number(date_str)
    if not row_num:
        print(f"[STOP] Could not find Funeral Homes row for {date_str} in sheet.")
        sys.exit(1)
    print(f"Found at row {row_num}")
    if existing:
        for cell, val in existing.items():
            if val:
                print(f"  Existing content in {cell}: {val[:60]}")

    print("\nWriting links to sheet...")
    write_links_to_sheet(row_num, links, existing)

    print("\n" + "=" * 58)
    print("  DONE — Links written to sheet.")
    print("=" * 58)


if __name__ == "__main__":
    main()
