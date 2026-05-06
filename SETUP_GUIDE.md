# Nuxeo Queue Dashboard — Setup Guide

## What You're Getting

| File | Purpose |
|------|---------|
| `scraper/nuxeo_scraper.py` | Runs every 5 min, logs into Nuxeo, scrapes all 6 tabs |
| `backend/main.py` | FastAPI server — serves metrics & ad details via API |
| `frontend/index.html` | Dashboard webpage — auto-refreshes every 5 minutes |
| `.env.example` | All your credentials (rename to `.env`) |

---

## STEP 1 — Install Python (Windows & Mac)

Download Python 3.11+ from https://python.org/downloads  
✅ On Windows: Check **"Add Python to PATH"** during install

Open a terminal (Command Prompt / Terminal app) and run:
```
python --version    # should show Python 3.11 or higher
```

---

## STEP 2 — Install Project Dependencies

Navigate to your project folder, then run:
```bash
pip install -r requirements.txt
playwright install chromium
```

---

## STEP 3 — Set Up MySQL

### Option A: Local MySQL (your PC)
1. Download MySQL Community Server from https://dev.mysql.com/downloads/
2. Install and note your root password
3. The scraper will auto-create the database on first run

### Option B: Free Cloud MySQL (PlanetScale / Railway)
- Go to https://railway.app → New Project → MySQL
- Copy the connection details into your `.env`

---

## STEP 4 — Set Up Google Sheets

1. Go to https://console.cloud.google.com
2. Create a new project (e.g. "nuxeo-tracker")
3. Enable **Google Sheets API** and **Google Drive API**
4. Go to "Credentials" → "Create Credentials" → "Service Account"
5. Name it anything, click Done
6. Click the service account → Keys tab → Add Key → JSON
7. **Download the JSON file** and rename it `gsheet_service_account.json`
8. Place this file in the `nuxeo-tracker/` folder

Then:
1. Create a new Google Sheet at https://sheets.google.com
2. Copy the Sheet ID from the URL (the long string between `/d/` and `/edit`)
3. Click **Share** on the sheet → paste the service account email (from the JSON file, looks like `name@project.iam.gserviceaccount.com`) → give **Editor** access

---

## STEP 5 — Configure Your Credentials

```bash
# In the nuxeo-tracker/ folder:
cp .env.example .env
```

Open `.env` in Notepad / VS Code and fill in:
- Your Nuxeo username and password
- MySQL host/user/password
- Google Sheet ID
- Path to your `gsheet_service_account.json`

---

## STEP 6 — Run the Scraper

```bash
# From the nuxeo-tracker/ folder:
python scraper/nuxeo_scraper.py
```

You'll see logs like:
```
2026-05-06 09:00:00 [INFO] === Scan starting at 2026-05-06 09:00:00 ===
2026-05-06 09:00:12 [INFO]   [Design] → 127 ads collected.
2026-05-06 09:00:18 [INFO]   ✔ Inserted 512 rows into MySQL.
2026-05-06 09:00:19 [INFO]   ✔ Appended 512 rows to Google Sheets.
```

**Leave this terminal running all day.** It automatically re-runs every 5 minutes.

> **Note**: First run will open a browser window (headless=False). Once confirmed working, change `headless=True` in `nuxeo_scraper.py` line ~100 to run in background.

---

## STEP 7 — Run the Dashboard Backend

Open a **second** terminal window and run:
```bash
# From the nuxeo-tracker/ folder:
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## STEP 8 — Open the Dashboard

Open your browser and go to:
```
http://localhost:8000
```

You'll see the full dashboard with all 6 tabs!

---

## DEPLOYING TO THE WEB (Access from Anywhere)

### Option A: Railway (Free, Easiest)
1. Sign up at https://railway.app
2. Connect your GitHub repo (push this project to GitHub first)
3. Railway auto-detects FastAPI and deploys it
4. Add all your `.env` variables in Railway's dashboard
5. Your dashboard will be live at `https://yourapp.railway.app`

### Option B: Render (Free Tier)
1. Sign up at https://render.com
2. New Web Service → Connect GitHub repo
3. Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables from your `.env`

### For the Scraper on a Server:
- The scraper needs to run on a machine that can access `nuxeoprd.valpak.com`
- On your office PC, you can use **Windows Task Scheduler** to start it on login
- Or run it as a background service using `pm2` (Node) or `supervisor` (Python)

---

## Windows Task Scheduler (Auto-start scraper on login)

1. Search "Task Scheduler" in Windows
2. Create Basic Task → Name: "Nuxeo Scraper"
3. Trigger: "When I log on"
4. Action: Start a program
   - Program: `python`
   - Arguments: `C:\path\to\nuxeo-tracker\scraper\nuxeo_scraper.py`
5. Click Finish

---

## Selector Tuning (If Scraper Misses Data)

If the scraper isn't picking up data correctly, you need to inspect the real Nuxeo page:
1. Open Nuxeo in Chrome → right-click the table → Inspect
2. Find the exact `class` or `id` of table rows
3. Update the selectors in `nuxeo_scraper.py` around line 80-100

Common fixes:
- Tab click: change `a:has-text('{tab_name}')` to match the exact HTML element
- Table rows: change `table tbody tr` to the actual container class
- Next page: change the next-button selector to match your Nuxeo version

---

## Dashboard Features

| Feature | How it works |
|---------|-------------|
| **6 Tab Switcher** | Click any tab at the top to see that queue's stats |
| **4 Metric Cards** | Incoming / Outgoing / Overdue / Pending — click to view details |
| **Date Filter** | Pick any past date to see that day's report |
| **Search** | Filter by Ad ID, Advertiser, Claimed By, or Market |
| **Category Filter** | Incoming / Outgoing / Overdue / Pending buttons above the table |
| **Auto-refresh** | Countdown timer in top-right — fetches new data every 5 minutes |
| **Red highlights** | Overdue deadlines shown in red in the table |
