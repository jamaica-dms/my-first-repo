# Setup Guide — ContentStudio Auto-Scheduler

## What You Need Before Starting
- Python 3.10+ installed on your computer
- Your ContentStudio API key
- Your Anthropic API key (for AI QA checks)
- The SharePoint Excel file synced to your local computer via OneDrive

---

## Step 1 — Sync SharePoint Excel to Your Computer

1. Open SharePoint in your browser
2. Navigate to the content calendar Excel file
3. Click **Sync** (top menu) — this downloads it via OneDrive
4. Find the synced file on your PC (usually in `C:\Users\Jhammy\Ring Ring Marketing\...`)
5. Copy that full file path — you'll need it in Step 3

---

## Step 2 — Install Python Dependencies

Open PowerShell and run:

```powershell
cd "C:\Users\Jhammy\Documents\Github\my-first-repo\contentstudio_scheduler"
pip install -r requirements.txt
```

---

## Step 3 — Create Your .env Config File

1. Copy `.env.example` and rename it to `.env`
2. Open `.env` and fill in your values:

```
CONTENTSTUDIO_API_KEY=cs_your_actual_key_here
EXCEL_PATH=C:\Users\Jhammy\...\Content Calendar.xlsx
ANTHROPIC_API_KEY=sk-ant-your_key_here
SMTP_USER=jhammy@ringringmarketing.com
SMTP_PASS=your_password_here
```

> **Never share your .env file or commit it to GitHub.**

---

## Step 4 — Run the Script

Open PowerShell and run:

```powershell
cd "C:\Users\Jhammy\Documents\Github\my-first-repo\contentstudio_scheduler"
python scheduler.py
```

The script will:
1. Read all rows marked **Ready** in the Excel file
2. Run QA checks on each post
3. Schedule passing posts in ContentStudio
4. Update the Status column to **Scheduled** or **QA Failed**
5. Email you if anything fails

---

## How to Use (Day-to-Day)

1. Fill in a row in the Excel sheet (Industry, Platform, Copy, Hashtags, Image, Date, Time, Timezone)
2. Set Status to **Ready**
3. Run the script: `python scheduler.py`
4. Check ContentStudio — the post should appear as scheduled

---

## Status Column Reference

| Status | Meaning |
|---|---|
| Draft | Still being worked on |
| Ready | Complete — run the script to schedule |
| Scheduled | Successfully added to ContentStudio |
| QA Failed | Something failed — check your email for details |

---

## Troubleshooting

**"Workspace not found"** — Check that the Industry name in Excel exactly matches one of the 4 options in the dropdown.

**"No platform account found"** — Make sure the social media account is connected in that ContentStudio workspace.

**"Image URL not accessible"** — The SharePoint image link needs to be set to "Anyone with link can view". Re-copy the link from SharePoint.

**Email not sending** — You may need to create an App Password in your Microsoft 365 account instead of using your regular password.
