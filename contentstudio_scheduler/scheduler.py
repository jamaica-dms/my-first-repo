#!/usr/bin/env python3
"""
ContentStudio Auto-Scheduler
Reads posts marked "Ready" from a SharePoint-synced Excel file,
runs automated QA checks, schedules them in ContentStudio,
and emails jhammy@ringringmarketing.com on any failure.
"""

import os
import re
import smtplib
import requests
import pytz
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import openpyxl
from anthropic import Anthropic

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
CS_API_KEY   = os.getenv("CONTENTSTUDIO_API_KEY")
CS_API_BASE  = "https://api.contentstudio.io/api/v1"
EXCEL_PATH   = os.getenv("EXCEL_PATH")          # Local path to your synced SharePoint Excel file
NOTIFY_EMAIL = os.getenv("NOTIFICATION_EMAIL", "jhammy@ringringmarketing.com")
SMTP_HOST    = os.getenv("SMTP_HOST", "smtp.office365.com")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER")           # Your Microsoft 365 email
SMTP_PASS    = os.getenv("SMTP_PASS")           # Your Microsoft 365 password or app password

anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── Workspace Mapping ─────────────────────────────────────────────────────────
WORKSPACE_MAP = {
    "Ring Ring Marketing":    "ring-ring-marketing",
    "RRM@home":               "rrmathome",
    "Senior Care Marketing Max": "senior-care-marketing-max",
    "Home Care Post":         "home-care-post",
}

# ── Platform Limits ───────────────────────────────────────────────────────────
CHAR_LIMITS = {
    "Instagram": 2200,
    "Facebook":  63206,
    "LinkedIn":  3000,
    "Twitter":   280,
    "TikTok":    2200,
}

HASHTAG_LIMITS = {
    "Instagram": 30,
    "Facebook":  10,
    "LinkedIn":  5,
    "Twitter":   5,
    "TikTok":    20,
}

TIMEZONE_MAP = {
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "EST": "America/New_York",
    "EDT": "America/New_York",
}

PLACEHOLDER_RE = re.compile(r'\[.*?\]|\{.*?\}|<[A-Z].*?>')


# ── ContentStudio API ─────────────────────────────────────────────────────────
def cs_headers():
    return {"X-API-Key": CS_API_KEY, "Content-Type": "application/json"}


def get_workspace_id(slug):
    resp = requests.get(f"{CS_API_BASE}/workspaces", headers=cs_headers(), timeout=15)
    resp.raise_for_status()
    for ws in resp.json().get("data", []):
        if ws.get("slug") == slug or ws.get("id") == slug:
            return ws["id"]
    raise ValueError(f"Workspace '{slug}' not found in ContentStudio")


def get_account_ids(workspace_id, platform):
    resp = requests.get(
        f"{CS_API_BASE}/workspaces/{workspace_id}/accounts",
        headers=cs_headers(), timeout=15
    )
    resp.raise_for_status()
    ids = []
    for acc in resp.json().get("data", []):
        acc_type = acc.get("type", "").lower()
        if platform.lower() in acc_type:
            ids.append(acc["id"])
    return ids


def schedule_post(workspace_id, account_ids, row):
    payload = {
        "content": {
            "text": f"{row['post_copy']}\n\n{row['hashtags']}".strip(),
            "media": {"images": [row["image_url"]]} if row["image_url"] else {},
        },
        "accounts": account_ids,
        "scheduling": {
            "publish_type": "scheduled",
            "scheduled_at": row["scheduled_at"].strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    resp = requests.post(
        f"{CS_API_BASE}/workspaces/{workspace_id}/posts",
        headers=cs_headers(), json=payload, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


# ── QA Checks ─────────────────────────────────────────────────────────────────
def run_qa(row):
    issues = []
    copy     = row["post_copy"]
    hashtags = row["hashtags"]
    platform = row["platform"]
    image    = row["image_url"]
    sched    = row["scheduled_at"]

    # Required fields
    if not copy.strip():
        issues.append("Post Copy is empty")
    if not platform.strip():
        issues.append("Platform is missing")
    if not row["industry"].strip():
        issues.append("Industry is missing")

    # Character limit
    full_text = f"{copy}\n{hashtags}".strip()
    limit = CHAR_LIMITS.get(platform, 2200)
    if len(full_text) > limit:
        issues.append(f"Text too long: {len(full_text)} chars (limit {limit} for {platform})")

    # Hashtag count
    tags = re.findall(r"#\w+", hashtags)
    max_tags = HASHTAG_LIMITS.get(platform, 30)
    if len(tags) > max_tags:
        issues.append(f"Too many hashtags: {len(tags)} (max {max_tags} for {platform})")

    # Placeholder check
    if PLACEHOLDER_RE.search(copy):
        issues.append("Post copy contains unfilled placeholders like [NAME] or {VALUE}")

    # Future date
    if sched <= datetime.now(pytz.UTC):
        issues.append("Scheduled date/time is in the past")

    # Image URL reachable
    if image:
        try:
            r = requests.head(image, timeout=10, allow_redirects=True)
            if r.status_code >= 400:
                issues.append(f"Image URL returned HTTP {r.status_code} — check SharePoint link permissions")
        except Exception:
            issues.append("Image URL could not be reached — check the link")
    else:
        issues.append("No image URL provided in Image Filename column")

    # Workspace mapping
    if row["industry"] not in WORKSPACE_MAP:
        issues.append(f"Unknown industry '{row['industry']}' — check spelling matches the dropdown")

    # AI grammar + brand voice check (only if basics pass)
    if not issues:
        ai_issues = _ai_qa(copy, platform)
        issues.extend(ai_issues)

    return issues


def _ai_qa(copy, platform):
    prompt = (
        f"Review this {platform} social media post for a professional marketing agency:\n\n"
        f"\"{copy}\"\n\n"
        "Check ONLY for: spelling errors, grammar errors, or unprofessional language.\n"
        "If everything looks good, reply with exactly: PASS\n"
        "If there are issues, reply with: FAIL\n"
        "Then list each issue on its own line. Be brief."
    )
    msg = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    result = msg.content[0].text.strip()
    if result.upper().startswith("FAIL"):
        return [line.strip("- ").strip() for line in result.splitlines()[1:] if line.strip()]
    return []


# ── Email ─────────────────────────────────────────────────────────────────────
def send_failure_email(row, issues):
    msg = MIMEMultipart()
    msg["From"]    = SMTP_USER
    msg["To"]      = NOTIFY_EMAIL
    msg["Subject"] = f"[QA Failed] {row['industry']} — {row['platform']} on {row['schedule_date']}"

    body = (
        f"A post failed QA and was NOT scheduled in ContentStudio.\n\n"
        f"Industry : {row['industry']}\n"
        f"Platform : {row['platform']}\n"
        f"Schedule : {row['schedule_date']} {row['schedule_time']} {row['timezone']}\n\n"
        f"Issues Found:\n"
        + "\n".join(f"  • {i}" for i in issues)
        + f"\n\nPost Copy:\n{row['post_copy']}\n\n"
        f"Fix the issues above, then set the Status back to 'Ready' to retry."
    )
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


# ── Excel Helpers ─────────────────────────────────────────────────────────────
def read_excel():
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]

    rows = []
    for i, excel_row in enumerate(ws.iter_rows(min_row=2), start=2):
        values = []
        for cell in excel_row:
            # Pull hyperlink target if present (SharePoint image links)
            if cell.hyperlink:
                values.append(cell.hyperlink.target)
            else:
                values.append(cell.value)

        if not any(values):
            continue

        data = dict(zip(headers, values))
        rows.append({
            "row_num":      i,
            "industry":     str(data.get("Industry") or "").strip(),
            "platform":     str(data.get("Platform") or "").strip(),
            "post_copy":    str(data.get("Post Copy") or "").strip(),
            "hashtags":     str(data.get("Hashtags") or "").strip(),
            "image_url":    str(data.get("Image Filename") or "").strip(),
            "schedule_date": data.get("Schedule Date"),
            "schedule_time": data.get("Schedule Time"),
            "timezone":     str(data.get("Timezone") or "PST").strip(),
            "status":       str(data.get("Status") or "").strip(),
        })
    return wb, ws, rows, headers


def update_status(ws, row_num, headers, status):
    col = headers.index("Status") + 1
    ws.cell(row=row_num, column=col).value = status


def parse_datetime(date_val, time_val, tz_str):
    tz = pytz.timezone(TIMEZONE_MAP.get(tz_str.upper(), "America/Los_Angeles"))

    if isinstance(date_val, datetime):
        date_part = date_val.date()
    else:
        for fmt in ("%d-%b-%y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                date_part = datetime.strptime(str(date_val).strip(), fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Unrecognised date format: {date_val}")

    if isinstance(time_val, datetime):
        time_part = time_val.time()
    else:
        for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
            try:
                time_part = datetime.strptime(str(time_val).strip(), fmt).time()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Unrecognised time format: {time_val}")

    return tz.localize(datetime.combine(date_part, time_part)).astimezone(pytz.UTC)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("ContentStudio Auto-Scheduler")
    print("=" * 50)

    wb, ws, rows, headers = read_excel()
    ready = [r for r in rows if r["status"] == "Ready"]
    print(f"Found {len(ready)} post(s) marked Ready\n")

    workspace_cache = {}
    account_cache   = {}
    changes = False

    for row in ready:
        label = f"{row['industry']} | {row['platform']} | Row {row['row_num']}"
        print(f"Processing: {label}")

        # Parse datetime
        try:
            row["scheduled_at"] = parse_datetime(
                row["schedule_date"], row["schedule_time"], row["timezone"]
            )
        except Exception as e:
            issues = [f"Date/time error: {e}"]
            print(f"  ✗ {issues[0]}")
            update_status(ws, row["row_num"], headers, "QA Failed")
            send_failure_email(row, issues)
            changes = True
            continue

        # QA checks
        issues = run_qa(row)
        if issues:
            print(f"  ✗ QA Failed ({len(issues)} issue(s))")
            for iss in issues:
                print(f"      - {iss}")
            update_status(ws, row["row_num"], headers, "QA Failed")
            send_failure_email(row, issues)
            changes = True
            continue

        # Schedule in ContentStudio
        slug = WORKSPACE_MAP[row["industry"]]
        try:
            if slug not in workspace_cache:
                workspace_cache[slug] = get_workspace_id(slug)
            workspace_id = workspace_cache[slug]

            cache_key = f"{workspace_id}_{row['platform']}"
            if cache_key not in account_cache:
                account_cache[cache_key] = get_account_ids(workspace_id, row["platform"])
            account_ids = account_cache[cache_key]

            if not account_ids:
                raise ValueError(f"No {row['platform']} account connected in workspace '{slug}'")

            schedule_post(workspace_id, account_ids, row)
            update_status(ws, row["row_num"], headers, "Scheduled")
            print(f"  ✓ Scheduled for {row['scheduled_at'].strftime('%Y-%m-%d %H:%M UTC')}")
            changes = True

        except Exception as e:
            print(f"  ✗ Scheduling error: {e}")
            update_status(ws, row["row_num"], headers, "QA Failed")
            send_failure_email(row, [f"Scheduling error: {e}"])
            changes = True

    if changes:
        wb.save(EXCEL_PATH)
        print(f"\nExcel file saved: {EXCEL_PATH}")

    print("\nDone.")


if __name__ == "__main__":
    main()
