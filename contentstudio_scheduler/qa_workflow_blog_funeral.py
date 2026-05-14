#!/usr/bin/env python3
"""
Blog QA Checker — Funeral Home — Ring Ring Marketing Workspace
Runs 3-phase QA on Funeral Home blog posts scheduled for a given date.
Funeral Home posts publish on the SAME DATE (not one week behind).

Usage:
    python qa_workflow_blog_funeral.py 2026-05-18
"""

import sys
import re
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# -- Config ---------------------------------------------------------------------
CS_API_KEY  = "cs_bd757a81fb4869ba556922297a3601033b337f8feb97421acad22a3ca70f3369"
CS_API_BASE = "https://api.contentstudio.io/api/v1"
WS_ID       = "66be2a6a2c16646ddc0d01c7"

PST_OFFSET  = timedelta(hours=7)  # UTC-7
FUNERAL_TIME = "T15:00:00"        # 8 AM PST = 15:00 UTC

FUNERAL_HASHTAGS = "#funeralhomemarketing #ringringmarketing #funeralservice"

EXPECTED_CHANNELS = {
    "Welton Facebook":  ("facebook",  "Welton Hong"),
    "RRM Facebook":     ("facebook",  "Ring Ring Marketing"),
    "Welton LinkedIn":  ("linkedin",  "Welton Hong"),
    "RRM LinkedIn":     ("linkedin",  "Ring Ring Marketing"),
    "Welton X":         ("twitter",   "weltonhong"),
    "RRM X":            ("twitter",   "RingMarketing"),
    "Welton Instagram": ("instagram", "Welton Hong"),
}

# -- Helpers --------------------------------------------------------------------
def cs_get(path):
    req = urllib.request.Request(
        f"{CS_API_BASE}{path}",
        headers={"X-API-Key": CS_API_KEY, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8"), r.status
    except urllib.error.HTTPError as e:
        return None, e.code


def fetch_image_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def compare_images(url1, url2):
    """Visually compare two images using PIL. Falls back to size ratio if PIL not available."""
    try:
        from PIL import Image
        import io
        img1 = Image.open(io.BytesIO(fetch_image_bytes(url1))).convert("RGB").resize((100, 100))
        img2 = Image.open(io.BytesIO(fetch_image_bytes(url2))).convert("RGB").resize((100, 100))
        diffs = sum(
            abs(a - b)
            for p1, p2 in zip(img1.getdata(), img2.getdata())
            for a, b in zip(p1, p2)
        )
        similarity = 1 - (diffs / (100 * 100 * 3 * 255))
        return similarity >= 0.85, f"{similarity:.0%} similar"
    except ImportError:
        b1 = fetch_image_bytes(url1)
        b2 = fetch_image_bytes(url2)
        ratio = min(len(b1), len(b2)) / max(len(b1), len(b2))
        return ratio >= 0.5, f"Size ratio {ratio:.0%} (install Pillow for visual comparison)"


def parse_post_fields(text):
    """Split post text into copy, hashtags, and URL."""
    lines         = text.strip().split("\n")
    copy_lines    = [l for l in lines if l.strip() and not l.startswith("#") and not l.startswith("http")]
    hashtag_lines = [l for l in lines if l.strip().startswith("#")]
    url_match     = re.search(r"https?://\S+", text)
    return (
        " ".join(copy_lines).strip(),
        " ".join(hashtag_lines).strip(),
        url_match.group(0) if url_match else None,
    )


def log(passed, label, detail=""):
    icon = "[PASS] PASS" if passed else "[FAIL] FAIL"
    line = f"  {icon} | {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return passed


# -- Phase 1: Website QA --------------------------------------------------------
def phase1_website_qa(blog_url):
    print("\n-- PHASE 1: WEBSITE QA " + "-" * 36)
    print(f"  URL: {blog_url}")

    html, status = fetch_html(blog_url)
    if not html:
        log(False, "Article live", f"HTTP {status}")
        return None
    log(True, "Article live")

    title     = re.search(r"<title>(.*?)</title>", html)
    meta_desc = re.search(r'meta name="description" content="(.*?)"', html)
    og_image  = re.search(r'og:image" content="(.*?)"', html)
    pub_date  = re.search(r'article:published_time" content="(.*?)"', html)
    h1        = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)

    results = {
        "title":     title.group(1)                                 if title     else None,
        "meta_desc": meta_desc.group(1)                             if meta_desc else None,
        "image_url": og_image.group(1)                              if og_image  else None,
        "pub_date":  pub_date.group(1)[:10]                         if pub_date  else None,
        "h1":        re.sub("<[^>]+>", "", h1.group(1)).strip()     if h1        else None,
    }

    log(bool(results["title"]),     "Title present",    results["title"]     or "MISSING")
    log(bool(results["meta_desc"]), "Meta description", results["meta_desc"] or "MISSING")
    log(bool(results["image_url"]), "OG image",         results["image_url"].split("/")[-1] if results["image_url"] else "MISSING")
    log(bool(results["pub_date"]),  "Published date",   results["pub_date"]  or "MISSING")

    # Punctuation check
    article = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL)
    body    = article.group(1) if article else html
    paras   = re.findall(r"<p[^>]*>(.*?)</p>", body, re.DOTALL)
    missing_punct = [
        re.sub("<[^>]+>", "", p).strip() for p in paras
        if len(re.sub("<[^>]+>", "", p).strip()) > 30
        and not re.sub("<[^>]+>", "", p).strip().endswith((".", "!", "?", ":"))
    ]
    log(not missing_punct, "Punctuation",
        f"{len(missing_punct)} paragraph(s) missing end punctuation" if missing_punct else "")
    for m in missing_punct:
        print(f"    -> \"{m[:120]}\"")

    # Italic check
    italics      = re.findall(r"<em>(.*?)</em>|<i>(.*?)</i>", body, re.DOTALL)
    italic_texts = [(i[0] or i[1]).strip() for i in italics if (i[0] or i[1]).strip()]
    log(not italic_texts, "No unintended italics",
        f"{len(italic_texts)} italic instance(s) found" if italic_texts else "")
    for it in italic_texts[:3]:
        print(f"    -> \"{it[:100]}\"")

    # Bold check
    bolds      = re.findall(r"<strong>(.*?)</strong>", body, re.DOTALL)
    bold_texts = [re.sub("<[^>]+>", "", b).strip() for b in bolds if re.sub("<[^>]+>", "", b).strip()]
    log(bool(bold_texts), "Bold formatting present", f"{len(bold_texts)} bold instance(s)")

    return results


# -- Phase 2: ContentStudio QA --------------------------------------------------
def phase2_contentstudio_qa(posts, phase1):
    print("\n-- PHASE 2: CONTENTSTUDIO QA (FUNERAL HOME) " + "-" * 15)

    expected_meta     = phase1["meta_desc"]
    expected_image    = phase1["image_url"]

    for post in posts:
        platforms = ", ".join(f"{a['platform']}({a['name']})" for a in post["accounts"])
        print(f"\n  [{platforms}]")

        text    = post["common"]["content"]["text"]
        images  = post["common"]["content"]["media"]["images"]
        img_url = images[0]["url"] if images else None
        is_ig   = any(a["platform"] == "instagram" for a in post["accounts"])
        is_x    = any(a["platform"] == "twitter"   for a in post["accounts"])

        copy, post_hashtags, link_in_copy = parse_post_fields(text)

        # Copy
        copy_ok = copy.strip() == expected_meta.strip()
        log(copy_ok, "Copy matches meta description")
        if not copy_ok:
            print(f"    Expected: {expected_meta}")
            print(f"    Got:      {copy}")

        # Hashtags
        if is_x:
            has_tags = any(h in post_hashtags for h in FUNERAL_HASHTAGS.split())
            log(has_tags, "Hashtags present (trimming allowed on X)", post_hashtags or "NONE FOUND")
        else:
            tags_ok = post_hashtags == FUNERAL_HASHTAGS
            log(tags_ok, "Hashtags correct")
            if not tags_ok:
                print(f"    Expected: {FUNERAL_HASHTAGS}")
                print(f"    Got:      {post_hashtags}")

        # Image (visual comparison)
        if img_url and expected_image:
            passed, detail = compare_images(expected_image, img_url)
            log(passed, "Image matches website", detail)
        else:
            log(False, "Image", "NO IMAGE in post" if not img_url else "NO REFERENCE IMAGE from Phase 1")

        # Instagram extras
        if is_ig:
            log(bool(link_in_copy), "Blog link in copy", link_in_copy or "MISSING")


# -- Phase 3: Schedule & Channel Verification -----------------------------------
def phase3_schedule_channels(posts):
    print("\n-- PHASE 3: SCHEDULE & CHANNEL VERIFICATION (FUNERAL HOME) " + "-" * 1)

    found = {}
    for post in posts:
        dt  = datetime.fromisoformat(post["scheduling"]["execute_time"].replace("Z", "+00:00"))
        pst = dt - PST_OFFSET
        for label, (platform, name) in EXPECTED_CHANNELS.items():
            for account in post["accounts"]:
                if account["platform"] == platform and name.lower() in account["name"].lower():
                    found[label] = {
                        "day":  pst.strftime("%A"),
                        "time": pst.strftime("%I:%M %p"),
                    }

    for label in EXPECTED_CHANNELS:
        if label in found:
            ch      = found[label]
            time_ok = ch["time"] == "08:00 AM"
            log(time_ok, label, f"{ch['day']} {ch['time']}")
        else:
            log(False, label, "MISSING — no post found for this channel")


# -- Main -----------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage:   python qa_workflow_blog_funeral.py <date>")
        print("Example: python qa_workflow_blog_funeral.py 2026-05-18")
        sys.exit(1)

    date_str = sys.argv[1]
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"ERROR: Invalid date '{date_str}'. Use YYYY-MM-DD format.")
        sys.exit(1)

    print("=" * 60)
    print(f"  Funeral Home Blog QA — Ring Ring Marketing")
    print(f"  Date: {date_str}")
    print("=" * 60)

    # Fetch all posts (all statuses) for flexibility
    print("\nFetching posts from ContentStudio...")
    all_posts = []
    for page in range(1, 10):
        data = cs_get(f"/workspaces/{WS_ID}/posts?page={page}")
        all_posts.extend(data["data"])
        if page >= data["last_page"]:
            break

    date_posts = [p for p in all_posts if p["scheduling"]["execute_time"].startswith(date_str)]
    fh_posts   = [p for p in date_posts if FUNERAL_TIME in p["scheduling"]["execute_time"]]

    print(f"Found {len(date_posts)} post(s) for {date_str}, {len(fh_posts)} at 8 AM (Funeral Home slot)")

    if not fh_posts:
        print("\n[WARN] No Funeral Home posts found at 8 AM for this date.")
        sys.exit(0)

    print(f"\n{'='*60}")
    print("  FUNERAL HOME BLOG")
    print(f"{'='*60}")

    ig_post = next(
        (p for p in fh_posts if any(a["platform"] == "instagram" for a in p["accounts"])),
        None
    )

    if not ig_post:
        print("[FAIL] No Instagram post found — cannot get blog URL for Phase 1.")
        sys.exit(1)

    _, _, blog_url = parse_post_fields(ig_post["common"]["content"]["text"])
    if not blog_url:
        print("[FAIL] No link found in Instagram post copy.")
        sys.exit(1)

    p1 = phase1_website_qa(blog_url)
    if p1:
        phase2_contentstudio_qa(fh_posts, p1)
        phase3_schedule_channels(fh_posts)

    print(f"\n{'='*60}")
    print("  QA COMPLETE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
