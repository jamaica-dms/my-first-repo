# ContentStudio Autoposting — Knowledge Base

## Overview
Automation that reads scheduled social media posts from a SharePoint Excel sheet, runs AI-powered QA checks, and automatically schedules them in ContentStudio — replacing the manual scheduling process.

---

## Tools & Platforms

| Tool | Purpose |
|---|---|
| SharePoint (Excel) | Central content calendar where team fills in post details |
| ContentStudio | Social media scheduling platform where posts go live |
| ContentStudio CLI | Command-line tool used by the script to push posts to ContentStudio |
| Python Script | The automation that ties everything together |
| Microsoft Graph API | Connects the script to SharePoint to read the Excel file |
| Email (SMTP) | Sends QA failure notifications |

---

## Workspaces

| Industry | ContentStudio Workspace Slug |
|---|---|
| Ring Ring Marketing | `ring-ring-marketing` |
| RRM@home | `rrmathome` |
| Senior Care Marketing Max | `senior-care-marketing-max` |
| Home Care Post | `home-care-post` |

---

## Excel Sheet Structure

**Location:** SharePoint — RRM In-House Marketing Initiatives

| Column | Description | Notes |
|---|---|---|
| Industry | Which brand/client this post is for | Dropdown: Ring Ring Marketing, RRM@home, Senior Care Marketing Max, Home Care Post |
| Platform | Social media channel | Dropdown: Facebook, Instagram, LinkedIn, etc. |
| Post Copy | The caption/text of the post | — |
| Hashtags | Hashtags for the post | Separate from copy for easy editing |
| Image Filename | SharePoint link to the image file | Paste SharePoint "copy link" URL |
| Schedule Date | Date the post should go live | Format: DD-MMM-YY (e.g., 14-May-26) |
| Schedule Time | Time the post should go live | Format: H:MM AM/PM (e.g., 7:00 AM) |
| Timezone | Timezone for the scheduled time | e.g., PST, EST |
| Status | Current state of the post | Dropdown: Draft, Ready, Scheduled, QA Failed |

---

## Workflow

```
Team fills in the Excel row
        ↓
Team changes Status to "Ready"
        ↓
Script detects "Ready" rows
        ↓
Automated QA checks run
        ↓
    Pass?
   /      \
 Yes       No
  ↓         ↓
Schedule   Status → "QA Failed"
in CS      Email sent to jhammy@ringringmarketing.com
  ↓
Status → "Scheduled"
```

---

## Status Values

| Status | Set By | Meaning |
|---|---|---|
| `Draft` | Team | Post is still being worked on |
| `Ready` | Team | Post is complete, ready for automation to pick up |
| `Scheduled` | Script | Successfully pushed to ContentStudio |
| `QA Failed` | Script | Did not pass QA checks — needs fixing |

---

## Automated QA Checks

| Check | What It Validates |
|---|---|
| Spelling & Grammar | Post copy has no errors |
| Character Limit | Copy is within platform limits (Instagram 2200, LinkedIn 3000, Facebook 63,206) |
| Hashtag Count | Not too many or too few hashtags per platform |
| Profanity Filter | No inappropriate language |
| Placeholder Check | No leftover placeholders like [INSERT NAME] |
| Image Exists | SharePoint image link is valid and accessible |
| Image Format | File is a supported format (JPG, PNG, etc.) |
| Date/Time Valid | Scheduled date/time is in the future |
| No Duplicates | No identical post already scheduled on same platform/date |
| AI Brand Voice | Copy matches brand tone (powered by AI) |

---

## Notifications

- **Trigger:** Post fails QA checks
- **Method:** Email
- **Recipient:** jhammy@ringringmarketing.com
- **Content:** Which row failed, which check it failed, and why

---

## ContentStudio API

- **API Requests:** 7,500/month (resets June 1, 2026)
- **CLI Install:** `npm install -g @contentstudio/cli`
- **Auth:** `contentstudio auth login --api-key <your-key>`
- **API Key:** Stored in `.env` file on local machine (never shared)

---

## Environment Config (.env file)

```
CONTENTSTUDIO_API_KEY=cs_xxxxxxxxxxxxxxxx
SHAREPOINT_SITE_URL=https://ringringmarketing.sharepoint.com/...
SHAREPOINT_FILE_PATH=/sites/RRM-In-HouseMarketingInitiatives/...
NOTIFICATION_EMAIL=jhammy@ringringmarketing.com
```

---

## Script Behavior

1. Reads all rows from the SharePoint Excel sheet
2. Filters rows where Status = `Ready`
3. For each ready row:
   - Runs all QA checks
   - If pass: calls ContentStudio CLI to schedule the post, updates Status to `Scheduled`
   - If fail: updates Status to `QA Failed`, sends email with details
4. Can be run manually or on a schedule

---

## Notes

- Image links in the sheet must be SharePoint "share links" set to accessible by the script
- One row = one post
- The script does not delete rows — it only updates the Status column
- Keep image filenames simple with no spaces (e.g., `rrm_post_may14.jpg`)
