#!/usr/bin/env python3
"""
Article QA Checker — Home Care Post Workspace (HCP)

Phase 1 — Google Sheet  : find what's scheduled for the given date
Phase 2 — Folder + Site : verify SharePoint folder exists, title and image match website
Phase 3 — ContentStudio : verify each channel has correct copy, link, hashtags, image

Usage:
    python qa_workflow_article_hcp.py 2026-05-21
"""

import sys
import re
import json
import os
import io
import csv
import unicodedata
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

# ── Config ─────────────────────────────────────────────────────────────────────
CS_API_KEY  = "cs_bd757a81fb4869ba556922297a3601033b337f8feb97421acad22a3ca70f3369"
CS_API_BASE = "https://api.contentstudio.io/api/v1"
WS_ID       = "6871327b366a03cd260d3441"  # Home Care Post workspace

# Google Sheet — open the sheet, go to the HCP tab, File > Share > Publish to web
# to get the gid, look at the URL: ...#gid=XXXXXXX while on that tab
SHEET_ID  = "1Kslp63_DcckDguJW_ABipaC-Vdl5FzFVhGRUWYd6tSc"
SHEET_GID = "1930398898"  # Home Care Post 2026 tab
SHEET_TAB = "Post 2026"   # Fallback tab name (gid takes priority)

HCP_FOLDER = (
    r"D:\Ring Ring Marketing\Ring Ring Marketing"
    r"\Messaging & Content Team - Personal Branding Videos\Home Care Post"
)

PST_OFFSET = timedelta(hours=7)  # UTC-7 (PDT); change to 8 during standard time

EXPECTED_CHANNELS = {
    "HCP Facebook":  ("facebook",  "Home Care Post", "09:00 AM"),
    "HCP LinkedIn":  ("linkedin",  "Home Care Post", "09:00 AM"),
    "HCP Instagram": ("instagram", "Home Care Post", "09:00 AM"),
    "HCP X":         ("twitter",   "homecarepost",   "09:00 AM"),
}


# ── Utilities ──────────────────────────────────────────────────────────────────
def cs_get(path):
    req = urllib.request.Request(
        f"{CS_API_BASE}{path}",
        headers={"X-API-Key": CS_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace"), r.status
    except urllib.error.HTTPError as e:
        return None, e.code


def fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def compare_images(web_url, local_path):
    """Visually compare a web image against a local file."""
    try:
        from PIL import Image
        web_data   = fetch_bytes(web_url)
        with open(local_path, "rb") as f:
            local_data = f.read()
        img_web   = Image.open(io.BytesIO(web_data)).convert("RGB").resize((100, 100))
        img_local = Image.open(io.BytesIO(local_data)).convert("RGB").resize((100, 100))
        diffs = sum(
            abs(a - b)
            for p1, p2 in zip(list(img_web.getdata()), list(img_local.getdata()))
            for a, b in zip(p1, p2)
        )
        similarity = 1 - (diffs / (100 * 100 * 3 * 255))
        return similarity >= 0.80, f"{similarity:.0%} similar"
    except Exception as e:
        return False, f"Image comparison error: {e}"


# Global results tracker
_results = []

def log(passed, label, detail=""):
    icon = "[PASS]" if passed else "[FAIL]"
    line = f"  {icon} {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    _results.append((passed, label, detail))
    return passed


def normalize_text(text):
    """
    Strip ContentStudio mention syntax {(id)[Name]}, mathematical italic unicode,
    and excess whitespace for loose comparison.
    """
    # Remove ContentStudio mentions like {(57259033959)[AARP]} or {(urn:...)[Name]}
    text = re.sub(r"\{\([^)]+\)\[([^\]]+)\]\}", r"\1", text)
    # Convert mathematical italic/bold unicode back to ASCII
    result = []
    for ch in text:
        name = unicodedata.name(ch, "")
        if "MATHEMATICAL" in name:
            # Try to get the base letter
            decomposed = unicodedata.normalize("NFKD", ch)
            result.append(decomposed if decomposed.isascii() else ch)
        else:
            result.append(ch)
    text = "".join(result)
    return re.sub(r"\s+", " ", text).strip()


def strip_meta(text):
    """Remove URLs and hashtags from text for copy comparison."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"#\w+", "", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Phase 1: Google Sheet ──────────────────────────────────────────────────────
def phase1_read_sheet(date_str):
    print("\n" + "-" * 58)
    print("  PHASE 1 — GOOGLE SHEET")
    print("-" * 58)

    dt         = datetime.strptime(date_str, "%Y-%m-%d")
    sheet_date = dt.strftime("%m/%d")  # "05/21"

    if SHEET_GID:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"
    else:
        url = (
            f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
            f"/export?format=csv&sheet={SHEET_TAB.replace(' ', '+')}"
        )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [ERROR] Could not fetch Google Sheet: {e}")
        print("  → Check SHEET_GID or SHEET_TAB in the script config.")
        return None

    rows = list(csv.reader(io.StringIO(content)))

    # Validate this looks like the HCP sheet
    flat = " ".join(" ".join(r) for r in rows[:30])
    if "homecarepost.com" not in flat.lower():
        print("  [WARN] Sheet doesn't look like the HCP calendar (no homecarepost.com URLs found).")
        print("         Check SHEET_GID / SHEET_TAB — may be pointing at the wrong tab.")

    # Find the row matching our date (col B = index 1 has the date like "05/21")
    result = None
    for row in rows:
        if len(row) < 6:
            continue
        cell_date = row[1].strip().lstrip("0").replace("/0", "/")  # normalise "05/21" → "5/21"
        target    = sheet_date.lstrip("0").replace("/0", "/")
        if cell_date == target or row[1].strip() == sheet_date:
            title  = row[4].strip() if len(row) > 4 else ""
            url_   = row[5].strip() if len(row) > 5 else ""
            medium = row[3].strip() if len(row) > 3 else ""
            if title and url_ and "homecarepost.com" in url_:
                result = {"title": title, "url": url_.rstrip("/"), "medium": medium}
                break

    if result:
        print(f"  Date:    {date_str}")
        print(f"  Medium:  {result['medium']}")
        print(f"  Title:   {result['title']}")
        print(f"  URL:     {result['url']}")
    else:
        print(f"  [WARN] No HCP entry found for {date_str} in the sheet.")

    return result


# ── Phase 2: Folder + Website ──────────────────────────────────────────────────
def find_folder(title):
    """Match article title to a SharePoint folder (exact then fuzzy)."""
    if not os.path.isdir(HCP_FOLDER):
        return None

    def norm(s):
        return re.sub(r"[:\s\-–—]+", " ", s).strip().lower()

    exact = os.path.join(HCP_FOLDER, title)
    if os.path.isdir(exact):
        return exact

    norm_title = norm(title)
    for name in os.listdir(HCP_FOLDER):
        if norm(name) == norm_title:
            return os.path.join(HCP_FOLDER, name)

    # Partial match — folder name starts with most of the title
    for name in os.listdir(HCP_FOLDER):
        if norm_title[:60] in norm(name) or norm(name)[:60] in norm_title:
            return os.path.join(HCP_FOLDER, name)

    return None


def phase2_folder_and_website(sheet_data):
    print("\n" + "-" * 58)
    print("  PHASE 2 — FOLDER + WEBSITE")
    print("-" * 58)

    title  = sheet_data["title"]
    url    = sheet_data["url"]
    folder = find_folder(title)

    # 2A — Folder match
    folder_ok = log(folder is not None, "Folder found in SharePoint",
                    os.path.basename(folder) if folder else f"No match for: {title}")
    if not folder_ok:
        return None, None

    # 2B — Files inside folder
    files      = os.listdir(folder)
    img_files  = [f for f in files if f.lower().endswith((".webp", ".jpg", ".jpeg", ".png"))]
    docx_files = [f for f in files if f.lower().endswith(".docx")]

    log(bool(img_files),  "Image file (.webp) found", img_files[0]  if img_files  else "MISSING")
    log(bool(docx_files), "Word doc (.docx) found",   docx_files[0] if docx_files else "MISSING")

    img_path  = os.path.join(folder, img_files[0])  if img_files  else None
    docx_path = os.path.join(folder, docx_files[0]) if docx_files else None

    # 2C — Article live on homecarepost.com
    print()
    html, status = fetch_html(url)
    log(html is not None, "Article live (200 OK)", f"HTTP {status}" if not html else "")
    if not html:
        return img_path, docx_path

    # Title match
    page_title_m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
    page_title   = page_title_m.group(1).strip() if page_title_m else ""
    title_ok     = title.lower()[:60] in page_title.lower() or page_title.lower()[:60] in title.lower()
    log(title_ok, "Page title matches sheet", page_title or "NO TITLE FOUND")

    # Image — og:image on the page vs local .webp
    og_img_m = (
        re.search(r'property="og:image"\s+content="(.*?)"', html) or
        re.search(r'og:image["\s]+content="(.*?)"', html)
    )
    web_img_url = og_img_m.group(1) if og_img_m else None

    if web_img_url and img_path:
        passed, detail = compare_images(web_img_url, img_path)
        log(passed, "Article image matches folder .webp", detail)
    elif not web_img_url:
        log(False, "Article image", "og:image not found on page")
    else:
        log(False, "Article image", "No local .webp to compare")

    return img_path, docx_path


# ── Word Doc Parser ────────────────────────────────────────────────────────────
def parse_word_doc(docx_path):
    """
    Extract per-channel copy, hashtags, and article URL from the Word doc.
    Returns dict: fb_li_copy, x_copy, ig_copy, hashtags, article_url
    """
    try:
        import docx as docx_lib
        doc  = docx_lib.Document(docx_path)
        text = "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        print(f"  [ERROR] Could not read Word doc: {e}")
        return None

    # Section boundaries
    fb_li_m = re.search(
        r"Facebook and LinkedIn[:\s]*\n(.*?)(?=\nX\b|\nInstagram\b|\nTags\b)",
        text, re.DOTALL | re.IGNORECASE,
    )
    x_m = re.search(
        r"\nX[:\s]*\n(.*?)(?=\nInstagram\b|\nTags\b|\n\w+ on |\nPhoto\b|\nPicture\b|\Z)",
        text, re.DOTALL,
    )
    ig_m = re.search(
        r"\nInstagram[:\s]*\n(.*?)(?=\nTags\b|\n\w+ on |\nPhoto\b|\nPicture\b|\Z)",
        text, re.DOTALL,
    )

    fb_li_text = fb_li_m.group(1).strip() if fb_li_m else ""
    x_text     = x_m.group(1).strip()     if x_m     else ""
    ig_text    = ig_m.group(1).strip()    if ig_m    else fb_li_text  # fallback to FB/LI

    # Extract article URL (from the FB/LI section)
    url_m = re.search(r"https?://www\.homecarepost\.com/\S+", fb_li_text or x_text)
    article_url = url_m.group(0).rstrip(".,)") if url_m else ""

    # Hashtags from the copy (all sections combined)
    all_tags = re.findall(r"#\w+", fb_li_text + " " + x_text + " " + ig_text)
    hashtags = list(dict.fromkeys(all_tags))  # deduplicated, order preserved

    return {
        "fb_li_copy":  fb_li_text,
        "x_copy":      x_text,
        "ig_copy":     ig_text,
        "hashtags":    hashtags,
        "article_url": article_url,
    }


# ── Phase 3: ContentStudio QA ──────────────────────────────────────────────────
def phase3_contentstudio(date_str, sheet_data, img_path, docx_path):
    print("\n" + "-" * 58)
    print("  PHASE 3 — CONTENTSTUDIO QA")
    print("-" * 58)

    expected = parse_word_doc(docx_path) if docx_path else None
    if not expected:
        print("  [WARN] Could not parse Word doc — skipping copy/hashtag checks.")

    # Fetch posts for the date across all statuses
    print(f"\n  Fetching posts from ContentStudio for {date_str}...")
    all_posts = []
    for status in ("review", "draft", "scheduled", "published"):
        for page in range(1, 10):
            try:
                data = cs_get(f"/workspaces/{WS_ID}/posts?status={status}&page={page}")
                all_posts.extend(data.get("data", []))
                if page >= data.get("last_page", 1):
                    break
            except Exception:
                break

    date_posts = [
        p for p in all_posts
        if p.get("scheduling", {}).get("execute_time", "").startswith(date_str)
    ]
    print(f"  Found {len(date_posts)} post(s) scheduled on {date_str}")

    if not date_posts:
        print("  [WARN] No posts found in ContentStudio for this date.")
        return

    # Map posts to channels
    found = {}
    for post in date_posts:
        for label, (platform, name_frag, _) in EXPECTED_CHANNELS.items():
            for account in post.get("accounts", []):
                if (account["platform"] == platform and
                        name_frag.lower() in account["name"].lower()):
                    found[label] = post

    # Check each channel
    print()
    for label, (platform, name_frag, expected_time) in EXPECTED_CHANNELS.items():
        print(f"  -- {label}")

        if label not in found:
            log(False, "Post exists in ContentStudio", "MISSING")
            print()
            continue

        post = found[label]
        dt  = datetime.fromisoformat(post["scheduling"]["execute_time"].replace("Z", "+00:00"))
        pst = dt - PST_OFFSET

        # Time
        actual_time  = pst.strftime("%I:%M %p").lstrip("0")
        expect_time_ = expected_time.lstrip("0")
        log(actual_time == expect_time_, "Scheduled time",
            f"{actual_time} (expected {expected_time})" if actual_time != expect_time_ else "")

        # Post text
        post_text = post.get("common", {}).get("content", {}).get("text", "") or ""
        post_norm = normalize_text(post_text)

        if expected:
            # Select expected copy block for this platform
            if platform == "twitter":
                copy_block = expected["x_copy"]
            elif platform == "instagram":
                copy_block = expected["ig_copy"]
            else:
                copy_block = expected["fb_li_copy"]

            # URL present
            exp_url  = sheet_data["url"].rstrip("/")
            url_ok   = exp_url in post_text
            log(url_ok, "Article URL in post", exp_url if not url_ok else "")

            # Hashtags
            exp_tags  = expected["hashtags"]
            post_tags = re.findall(r"#\w+", post_text)
            if platform == "twitter":
                some_ok = any(t in post_tags for t in exp_tags)
                log(some_ok, "Hashtags present (trimming OK on X)",
                    f"found: {' '.join(post_tags)}" if post_tags else "NONE FOUND")
            else:
                missing_tags = [t for t in exp_tags if t not in post_tags]
                log(not missing_tags, "Hashtags match",
                    f"missing: {' '.join(missing_tags)}" if missing_tags else "")

            # Copy — check first meaningful sentence appears in post
            copy_clean = strip_meta(normalize_text(copy_block))
            # Replace Twitter link preambles "(1) Name (@handle) / X" with "@handle"
            copy_clean = re.sub(r"\(\d+\)\s+[^(]+\(@(\w+)\)\s*/\s*X", r"@\1", copy_clean)
            post_clean = strip_meta(post_norm)
            first_sent = copy_clean.split(".")[0].strip()[:80]
            copy_ok    = first_sent.lower() in post_clean.lower() if first_sent else False
            log(copy_ok, "Copy matches Word doc (key phrase check)",
                f"Expected: '{first_sent}'" if not copy_ok else "")

        # Image
        images = post.get("common", {}).get("content", {}).get("media", {}).get("images", [])
        post_img_url = images[0].get("url") if images else None

        if post_img_url and img_path:
            passed, detail = compare_images(post_img_url, img_path)
            log(passed, "Image matches folder .webp", detail)
        elif not post_img_url:
            log(False, "Image", "No image attached to post")
        else:
            log(False, "Image", "No local .webp to compare against")

        print()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Usage:   python qa_workflow_article_hcp.py <date>")
        print("Example: python qa_workflow_article_hcp.py 2026-05-21")
        sys.exit(1)

    date_str = sys.argv[1]
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"ERROR: Invalid date '{date_str}'. Use YYYY-MM-DD format.")
        sys.exit(1)

    print("-" * 58)
    print(f"  Home Care Post — Article QA")
    print(f"  Date: {date_str}")
    print("-" * 58)

    sheet_data = phase1_read_sheet(date_str)
    if not sheet_data:
        print("\n[STOP] No HCP article scheduled for this date in the sheet.")
        sys.exit(0)

    img_path, docx_path = phase2_folder_and_website(sheet_data)
    phase3_contentstudio(date_str, sheet_data, img_path, docx_path)

    # ── Final Report ─────────────────────────────────────────────────────────
    failures = [(label, detail) for passed, label, detail in _results if not passed]
    total    = len(_results)
    passed_n = total - len(failures)

    print("\n" + "=" * 58)
    print(f"  REPORT — {date_str}")
    print("=" * 58)
    print(f"  Article : {sheet_data['title'][:55]}...")
    print(f"  Checks  : {passed_n}/{total} passed")
    print()

    if not failures:
        print("  RESULT: POSTING IS CORRECT — all checks passed.")
    else:
        print("  RESULT: POSTING HAS ISSUES — fix before approving.")
        print()
        print("  What needs fixing:")
        for label, detail in failures:
            line = f"    - {label}"
            if detail:
                line += f": {detail}"
            print(line)

    print("=" * 58)


if __name__ == "__main__":
    main()
