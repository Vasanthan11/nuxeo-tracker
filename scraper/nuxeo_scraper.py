"""
Nuxeo Queue Scraper
Scrapes 6 tabs every 5 minutes: Material Review, PreMedia, Design,
In Progress Ads, In House Change, Quality Control
Saves to MySQL + Google Sheets
"""

import os
import json
import time
import logging
import mysql.connector
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# CONFIGURATION  (set in .env or edit here)
# ──────────────────────────────────────────────
NUXEO_URL  = os.getenv("NUXEO_URL",  "https://nuxeoprd.valpak.com/nuxeo/")
USERNAME   = os.getenv("NUXEO_USER", "YOUR_USERNAME")
PASSWORD   = os.getenv("NUXEO_PASS", "YOUR_PASSWORD")

DB_HOST    = os.getenv("DB_HOST",    "localhost")
DB_PORT    = int(os.getenv("DB_PORT", 3306))
DB_NAME    = os.getenv("DB_NAME",    "nuxeo_tracker")
DB_USER    = os.getenv("DB_USER",    "root")
DB_PASS    = os.getenv("DB_PASS",    "")

GSHEET_CREDS_FILE = os.getenv("GSHEET_CREDS", "gsheet_service_account.json")
GSHEET_ID         = os.getenv("GSHEET_ID",    "YOUR_GOOGLE_SHEET_ID")

INTERVAL_SECONDS = 300  # 5 minutes

# Tabs to scrape and their display names in the Nuxeo UI
TABS = [
    "Material Review",
    "PreMedia",
    "Design",
    "In Progress Ads",
    "In House Change",
    "Quality Control",
]

COLUMNS = [
    "snapshot_time", "tab_name",
    "ad_id", "due_out_deadline", "art_deadline",
    "advertiser", "dt", "material", "complexity",
    "effort", "claimed_by", "sales_rep", "market", "collections",
    "due_out_is_red", "art_is_red",
]


# ──────────────────────────────────────────────
# DATABASE SETUP
# ──────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER, password=DB_PASS,
        autocommit=True
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"""
        CREATE DATABASE IF NOT EXISTS `{DB_NAME}`
    """)
    cur.execute(f"USE `{DB_NAME}`")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ad_snapshots (
            id               BIGINT AUTO_INCREMENT PRIMARY KEY,
            snapshot_time    DATETIME NOT NULL,
            tab_name         VARCHAR(60) NOT NULL,
            ad_id            VARCHAR(60),
            due_out_deadline VARCHAR(40),
            art_deadline     VARCHAR(40),
            advertiser       VARCHAR(200),
            dt               VARCHAR(60),
            material         VARCHAR(60),
            complexity       VARCHAR(20),
            effort           VARCHAR(60),
            claimed_by       VARCHAR(100),
            sales_rep        VARCHAR(100),
            market           VARCHAR(100),
            collections      VARCHAR(100),
            due_out_is_red   TINYINT(1) DEFAULT 0,
            art_is_red       TINYINT(1) DEFAULT 0,
            INDEX idx_time   (snapshot_time),
            INDEX idx_tab    (tab_name),
            INDEX idx_ad     (ad_id)
        )
    """)
    conn.close()
    log.info("Database initialised.")

def insert_rows(rows: list[dict]):
    if not rows:
        return
    conn = get_db()
    cur = conn.cursor()
    sql = """
        INSERT INTO ad_snapshots
        (snapshot_time, tab_name, ad_id, due_out_deadline, art_deadline,
         advertiser, dt, material, complexity, effort,
         claimed_by, sales_rep, market, collections,
         due_out_is_red, art_is_red)
        VALUES
        (%(snapshot_time)s, %(tab_name)s, %(ad_id)s, %(due_out_deadline)s, %(art_deadline)s,
         %(advertiser)s, %(dt)s, %(material)s, %(complexity)s, %(effort)s,
         %(claimed_by)s, %(sales_rep)s, %(market)s, %(collections)s,
         %(due_out_is_red)s, %(art_is_red)s)
    """
    cur.executemany(sql, rows)
    conn.close()
    log.info(f"  ✔ Inserted {len(rows)} rows into MySQL.")


# ──────────────────────────────────────────────
# GOOGLE SHEETS SETUP
# ──────────────────────────────────────────────
def get_gsheet():
    try:
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(GSHEET_CREDS_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GSHEET_ID)
        return sh
    except Exception as e:
        log.warning(f"Google Sheets not available: {e}")
        return None

def append_to_gsheet(sh, rows: list[dict]):
    if not sh or not rows:
        return
    try:
        try:
            ws = sh.worksheet("Raw Data")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet("Raw Data", rows=5000, cols=len(COLUMNS))
            ws.append_row(COLUMNS)

        values = [
            [str(r.get(c, "")) for c in COLUMNS]
            for r in rows
        ]
        ws.append_rows(values, value_input_option="USER_ENTERED")
        log.info(f"  ✔ Appended {len(rows)} rows to Google Sheets.")
    except Exception as e:
        log.warning(f"Google Sheets append failed: {e}")


# ──────────────────────────────────────────────
# SCRAPING LOGIC
# ──────────────────────────────────────────────
def is_red(cell) -> bool:
    """Check if a cell has a pink/red background indicating overdue."""
    style = cell.get_attribute("style") or ""
    cls   = cell.get_attribute("class") or ""
    return any(k in style.lower() for k in ["background", "color:#f", "color: #f", "red", "pink"]) \
        or any(k in cls.lower() for k in ["late", "overdue", "warning", "red", "alert"])

def scrape_tab(page, tab_name: str, snapshot_time: datetime) -> list[dict]:
    rows_data = []

    # Click the tab
    try:
        tab = page.locator(f"a:has-text('{tab_name}'), span:has-text('{tab_name}')").first
        tab.click(timeout=8000)
        page.wait_for_load_state("networkidle", timeout=15000)
        page.wait_for_timeout(2000)
    except PlaywrightTimeout:
        log.warning(f"  Tab '{tab_name}' not found or timed out — skipping.")
        return rows_data

    # Set items/page to 100 if dropdown exists
    try:
        dropdown = page.locator("select").filter(has_text="100").first
        if not dropdown.is_visible():
            # Try any items-per-page select
            dropdown = page.locator("[class*='nxl-items'], select[name*='page'], select[name*='size']").first
        if dropdown.is_visible():
            dropdown.select_option("100")
            page.wait_for_timeout(2000)
    except Exception:
        pass

    # Paginate
    page_num = 0
    while True:
        page_num += 1
        log.info(f"    [{tab_name}] Scraping page {page_num}...")

        table_rows = page.locator("table tbody tr").all()
        for row in table_rows:
            cells = row.locator("td").all()
            if len(cells) < 5:
                continue

            def txt(i):
                try:
                    return cells[i].inner_text().strip() if i < len(cells) else ""
                except Exception:
                    return ""

            # Detect red highlights on deadline cells (typically col index 1 and 2)
            try:
                red_due  = is_red(cells[1]) if len(cells) > 1 else False
                red_art  = is_red(cells[2]) if len(cells) > 2 else False
            except Exception:
                red_due = red_art = False

            rows_data.append({
                "snapshot_time":    snapshot_time.strftime("%Y-%m-%d %H:%M:%S"),
                "tab_name":         tab_name,
                "ad_id":            txt(0),
                "due_out_deadline": txt(1),
                "art_deadline":     txt(2),
                "advertiser":       txt(3),
                "dt":               txt(4),
                "material":         txt(5),
                "complexity":       txt(6),
                "effort":           txt(7),
                "claimed_by":       txt(8),
                "sales_rep":        txt(9),
                "market":           txt(10),
                "collections":      txt(11) if len(cells) > 11 else "",
                "due_out_is_red":   int(red_due),
                "art_is_red":       int(red_art),
            })

        # Check for next page
        try:
            next_btn = page.locator("button[title='Next Page'], [class*='next']:not([disabled])").first
            if next_btn.is_visible() and next_btn.is_enabled():
                next_btn.click()
                page.wait_for_timeout(2500)
            else:
                break
        except Exception:
            break

    log.info(f"  [{tab_name}] → {len(rows_data)} ads collected.")
    return rows_data


def run_once(gsheet):
    snapshot_time = datetime.now()
    log.info(f"=== Scan starting at {snapshot_time.strftime('%Y-%m-%d %H:%M:%S')} ===")

    all_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page    = context.new_page()

        try:
            # Login
            page.goto(NUXEO_URL, timeout=30000)
            page.wait_for_load_state("networkidle")

            if page.locator("input[name='username'], input[id='username']").count() > 0:
                page.fill("input[name='username'], input[id='username']", USERNAME)
                page.fill("input[name='password'], input[id='password']", PASSWORD)
                page.click("button[type='submit'], input[type='submit']")
                page.wait_for_load_state("networkidle", timeout=20000)
                log.info("  ✔ Logged in.")

            # Navigate to Workspace/Valpak if needed
            try:
                page.locator("text=WORKSPACE").click(timeout=5000)
                page.wait_for_load_state("networkidle")
            except Exception:
                pass

            # Scrape each tab
            for tab in TABS:
                rows = scrape_tab(page, tab, snapshot_time)
                all_rows.extend(rows)

        except Exception as e:
            log.error(f"Fatal scraping error: {e}")
        finally:
            browser.close()

    if all_rows:
        insert_rows(all_rows)
        append_to_gsheet(gsheet, all_rows)

    log.info(f"=== Scan complete. Total rows: {len(all_rows)} ===\n")


# ──────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Initialising database...")
    init_db()

    log.info("Connecting to Google Sheets...")
    gsheet = get_gsheet()

    log.info(f"Starting scraper loop (every {INTERVAL_SECONDS}s)...")
    while True:
        try:
            run_once(gsheet)
        except Exception as e:
            log.error(f"Unhandled error in run loop: {e}")
        log.info(f"Sleeping {INTERVAL_SECONDS}s until next scan...\n")
        time.sleep(INTERVAL_SECONDS)
