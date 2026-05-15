#!/usr/bin/env python3
"""
Blog QA Checker — RRM@home Workspace (HVAC)
Runs 3-phase QA on HVAC blog posts scheduled for a given Monday.

Usage:
    python qa_workflow_blog_hvac_rrmathome.py 2026-05-18
"""

import sys
import re
import json
from html import unescape as html_unescape
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# -- Config ---------------------------------------------------------------------
CS_API_KEY  = "cs_bd757a81fb4869ba556922297a3601033b337f8feb97421acad22a3ca70f3369"
CS_API_BASE = "https://api.contentstudio.io/api/v1"
WS_ID       = "66be5e278c3e19aae4051bc5"  # RRM@home

PST_OFFSET = timedelta(hours=7)   # UTC-7 (PDT)
BLOG_DELAY = timedelta(days=7)    # article published 1 week before posting date

# 10 AM PST = 17:00 UTC, 11 AM PST = 18:00 UTC
TIME_10AM = "T17:00:00"
TIME_11AM = "T18:00:00"

HVAC_HASHTAGS = "#hvacmarketing #hvacbusinessgrowth #hvacdigitalmarketing #rrmathome"

# (platform, name_fragment, expected_pst_time)
EXPECTED_CHANNELS = {
    "RRM@home Facebook":        ("facebook",  "RRM At Home",  "10:00 AM"),
    "RRM@home LinkedIn":        ("linkedin",  "RRM@home",     "10:00 AM"),
    "Welton RRM@home Facebook": ("facebook",  "Welton Hong",  "10:00 AM"),
    "RRM@home Instagram":       ("instagram", "RRM@home",     "10:00 AM"),
    "RRM@home X":               ("twitter",   "rrmathome",    "10:00 AM"),
    "Welton LinkedIn":          ("linkedin",  "Welton Hong",  "11:00 AM"),
    "Welton Instagram":         ("instagram", "Welton Hong",  "11:00 AM"),
    "Welton X":                 ("twitter",   "weltonhong",   "11:00 AM"),
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
    lines = text.strip().split("\n")
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
def phase1_website_qa(blog_url, expected_article_date):
    print("\n-- PHASE 1: WEBSITE QA " + "-" * 36)
    print(f"  URL: {blog_url}")
    print(f"  Expected article date: {expected_article_date}")

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
        "title":     title.group(1)                             if title     else None,
        "meta_desc": html_unescape(meta_desc.group(1))          if meta_desc else None,
        "image_url": og_image.group(1)                          if og_image  else None,
        "pub_date":  pub_date.group(1)[:10]                     if pub_date  else None,
        "h1":        re.sub("<[^>]+>", "", h1.group(1)).strip() if h1        else None,
    }

    log(bool(results["title"]),     "Title present",    results["title"]     or "MISSING")
    log(bool(results["meta_desc"]), "Meta description", results["meta_desc"] or "MISSING")
    log(bool(results["image_url"]), "OG image",         results["image_url"].split("/")[-1] if results["image_url"] else "MISSING")

    if results["pub_date"]:
        date_ok = results["pub_date"] == expected_article_date
        log(date_ok, "Article date correct", f"{results['pub_date']} (expected {expected_article_date})")
    else:
        log(False, "Published date", "MISSING")

    article = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL)
    body = article.group(1) if article else html
    paras = re.findall(r"<p[^>]*>(.*?)</p>", body, re.DOTALL)
    missing_punct = [
        re.sub("<[^>]+>", "", p).strip() for p in paras
        if len(re.sub("<[^>]+>", "", p).strip()) > 30
        and not re.sub("<[^>]+>", "", p).strip().endswith((".", "!", "?", ":"))
    ]
    log(not missing_punct, "Punctuation",
        f"{len(missing_punct)} paragraph(s) missing end punctuation" if missing_punct else "")
    for m in missing_punct:
        print(f"    → \"{m[:120]}\"")

    italics = re.findall(r"<em>(.*?)</em>|<i>(.*?)</i>", body, re.DOTALL)
    italic_texts = [(i[0] or i[1]).strip() for i in italics if (i[0] or i[1]).strip()]
    log(not italic_texts, "No unintended italics",
        f"{len(italic_texts)} italic instance(s) found" if italic_texts else "")
    for it in italic_texts[:3]:
        print(f"    → \"{it[:100]}\"")

    bolds = re.findall(r"<strong>(.*?)</strong>", body, re.DOTALL)
    bold_texts = [re.sub("<[^>]+>", "", b).strip() for b in bolds if re.sub("<[^>]+>", "", b).strip()]
    log(bool(bold_texts), "Bold formatting present", f"{len(bold_texts)} bold instance(s)")

    return results


# -- Phase 2: ContentStudio QA --------------------------------------------------
def phase2_contentstudio_qa(posts, phase1):
    print("\n-- PHASE 2: CONTENTSTUDIO QA " + "-" * 30)

    expected_meta  = phase1["meta_desc"]
    expected_image = phase1["image_url"]

    for post in posts:
        accounts  = post["accounts"]
        platforms = ", ".join(f"{a['platform']}({a['name']})" for a in accounts)
        print(f"\n  [{platforms}]")

        text    = post["common"]["content"]["text"]
        images  = post["common"]["content"]["media"]["images"]
        img_url = images[0]["url"] if images else None

        is_ig = len(accounts) == 1 and accounts[0]["platform"] == "instagram"
        is_x  = len(accounts) == 1 and accounts[0]["platform"] == "twitter"

        copy, post_hashtags, link_in_copy = parse_post_fields(text)

        copy_ok = copy.strip() == expected_meta.strip() if expected_meta else False
        log(copy_ok, "Copy matches meta description")
        if not copy_ok:
            print(f"    Expected: {expected_meta}")
            print(f"    Got:      {copy}")

        if is_x:
            has_tags = any(h in post_hashtags for h in HVAC_HASHTAGS.split())
            log(has_tags, "Hashtags present (trimming allowed on X)", post_hashtags or "NONE FOUND")
        else:
            tags_ok = post_hashtags == HVAC_HASHTAGS
            log(tags_ok, "Hashtags correct")
            if not tags_ok:
                print(f"    Expected: {HVAC_HASHTAGS}")
                print(f"    Got:      {post_hashtags}")

        if img_url and expected_image:
            passed, detail = compare_images(expected_image, img_url)
            log(passed, "Image matches website", detail)
        else:
            log(False, "Image", "NO IMAGE in post" if not img_url else "NO REFERENCE IMAGE from Phase 1")

        if is_ig:
            log(bool(link_in_copy), "Blog link in copy", link_in_copy or "MISSING")


# -- Phase 3: Schedule & Channel Verification -----------------------------------
def phase3_schedule_channels(posts):
    print("\n-- PHASE 3: SCHEDULE & CHANNEL VERIFICATION " + "-" * 15)

    found = {}
    for post in posts:
        dt  = datetime.fromisoformat(post["scheduling"]["execute_time"].replace("Z", "+00:00"))
        pst = dt - PST_OFFSET
        for label, (platform, name, _) in EXPECTED_CHANNELS.items():
            for account in post["accounts"]:
                if account["platform"] == platform and name.lower() in account["name"].lower():
                    found[label] = {
                        "day":  pst.strftime("%A"),
                        "time": pst.strftime("%I:%M %p"),
                    }

    for label, (_, _, expected_time) in EXPECTED_CHANNELS.items():
        if label in found:
            ch = found[label]
            ok = ch["day"] == "Monday" and ch["time"] == expected_time
            log(ok, label, f"{ch['day']} {ch['time']}")
        else:
            log(False, label, "MISSING — no post found for this channel")


# -- Main -----------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage:   python qa_workflow_blog_hvac_rrmathome.py <date>")
        print("Example: python qa_workflow_blog_hvac_rrmathome.py 2026-05-18")
        sys.exit(1)

    date_str = sys.argv[1]
    try:
        post_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"ERROR: Invalid date '{date_str}'. Use YYYY-MM-DD format.")
        sys.exit(1)

    article_date = (post_date - BLOG_DELAY).strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"  Blog QA — RRM@home (HVAC)")
    print(f"  Posting date:  {date_str}")
    print(f"  Article date:  {article_date} (1 week prior)")
    print("=" * 60)

    print("\nFetching posts from ContentStudio...")
    all_posts = []
    for status in ("review", "draft", "scheduled", "published"):
        for page in range(1, 10):
            data = cs_get(f"/workspaces/{WS_ID}/posts?status={status}&page={page}")
            all_posts.extend(data["data"])
            if page >= data["last_page"]:
                break

    date_posts = [p for p in all_posts if p["scheduling"]["execute_time"].startswith(date_str)]
    hvac_posts = [
        p for p in date_posts
        if (
            TIME_10AM in p["scheduling"]["execute_time"]
            and not all("welton" in a["name"].lower() for a in p["accounts"])
        )
        or (
            TIME_11AM in p["scheduling"]["execute_time"]
            and all("welton" in a["name"].lower() for a in p["accounts"])
        )
    ]
    print(f"Found {len(hvac_posts)} HVAC post(s) for {date_str}")

    if not hvac_posts:
        print("\n[WARN] No HVAC posts found at 10 AM or 11 AM PST for this date.")
        sys.exit(0)

    # Get blog URL from RRM@home Instagram post (10 AM standalone)
    rrmathome_ig_post = next(
        (p for p in hvac_posts
         if TIME_10AM in p["scheduling"]["execute_time"]
         and len(p["accounts"]) == 1
         and p["accounts"][0]["platform"] == "instagram"),
        None
    )

    if not rrmathome_ig_post:
        print("[FAIL] RRM@home Instagram post not found — cannot extract blog URL for Phase 1.")
        sys.exit(1)

    _, _, blog_url = parse_post_fields(rrmathome_ig_post["common"]["content"]["text"])
    if not blog_url:
        print("[FAIL] No blog URL found in RRM@home Instagram post copy.")
        sys.exit(1)

    p1 = phase1_website_qa(blog_url, article_date)
    if p1:
        phase2_contentstudio_qa(hvac_posts, p1)
        phase3_schedule_channels(hvac_posts)

    print(f"\n{'='*60}")
    print("  QA COMPLETE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
