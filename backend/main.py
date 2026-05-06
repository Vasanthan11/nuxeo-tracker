"""
Nuxeo Tracker — FastAPI Backend
Serves metrics and ad-detail data for the web dashboard.
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import mysql.connector
import os
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Nuxeo Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_NAME = os.getenv("DB_NAME", "nuxeo_tracker")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "")

TABS = [
    "Material Review",
    "PreMedia",
    "Design",
    "In Progress Ads",
    "In House Change",
    "Quality Control",
]

def db():
    return mysql.connector.connect(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER, password=DB_PASS
    )

def query(sql, params=None):
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    conn.close()
    return rows


# ─── HELPERS ─────────────────────────────────

def day_range(d: date):
    start = datetime.combine(d, datetime.min.time())
    end   = datetime.combine(d, datetime.max.time())
    return start, end

def incoming_ads(tab: str, d: date):
    """Ads that appeared for the first time on date d in this tab."""
    start, end = day_range(d)
    sql = """
        SELECT ad_id, MIN(snapshot_time) AS first_seen
        FROM ad_snapshots
        WHERE tab_name = %s AND snapshot_time BETWEEN %s AND %s
        GROUP BY ad_id
        HAVING MIN(snapshot_time) BETWEEN %s AND %s
    """
    return query(sql, (tab, start, end, start, end))

def outgoing_ads(tab: str, d: date):
    """Ads that were present before or during d but gone from last snapshot of d."""
    start, end = day_range(d)
    # Present during d
    present = {r["ad_id"] for r in query(
        "SELECT DISTINCT ad_id FROM ad_snapshots WHERE tab_name=%s AND snapshot_time BETWEEN %s AND %s",
        (tab, start, end)
    )}
    # Present in last snapshot of d
    last_snap = query(
        "SELECT MAX(snapshot_time) AS t FROM ad_snapshots WHERE tab_name=%s AND snapshot_time BETWEEN %s AND %s",
        (tab, start, end)
    )
    if not last_snap or not last_snap[0]["t"]:
        return []
    last_t = last_snap[0]["t"]
    last_present = {r["ad_id"] for r in query(
        "SELECT DISTINCT ad_id FROM ad_snapshots WHERE tab_name=%s AND snapshot_time=%s",
        (tab, last_t)
    )}
    gone_ids = present - last_present
    if not gone_ids:
        return []
    placeholders = ",".join(["%s"] * len(gone_ids))
    return query(
        f"SELECT * FROM ad_snapshots WHERE tab_name=%s AND ad_id IN ({placeholders}) AND snapshot_time BETWEEN %s AND %s ORDER BY snapshot_time DESC LIMIT 1000",
        (tab, *gone_ids, start, end)
    )

def overdue_ads(tab: str, d: date):
    start, end = day_range(d)
    return query(
        "SELECT * FROM ad_snapshots WHERE tab_name=%s AND snapshot_time BETWEEN %s AND %s AND (due_out_is_red=1 OR art_is_red=1) GROUP BY ad_id",
        (tab, start, end)
    )

def pending_ads(tab: str, d: date):
    """Ads still present at last snapshot of d."""
    start, end = day_range(d)
    last_snap = query(
        "SELECT MAX(snapshot_time) AS t FROM ad_snapshots WHERE tab_name=%s AND snapshot_time BETWEEN %s AND %s",
        (tab, start, end)
    )
    if not last_snap or not last_snap[0]["t"]:
        return []
    last_t = last_snap[0]["t"]
    return query(
        "SELECT * FROM ad_snapshots WHERE tab_name=%s AND snapshot_time=%s",
        (tab, last_t)
    )


# ─── ENDPOINTS ───────────────────────────────

@app.get("/api/summary")
def summary(date_str: str = Query(default=None)):
    """Returns per-tab metric counts for a given date (default: today)."""
    d = date.fromisoformat(date_str) if date_str else date.today()
    result = {}
    for tab in TABS:
        inc  = incoming_ads(tab, d)
        out  = outgoing_ads(tab, d)
        over = overdue_ads(tab, d)
        pend = pending_ads(tab, d)
        result[tab] = {
            "incoming":          len(inc),
            "outgoing":          len(out),
            "overdue_due_out":   sum(1 for r in over if r.get("due_out_is_red")),
            "overdue_art":       sum(1 for r in over if r.get("art_is_red")),
            "pending":           len(pend),
            "pending_due_red":   sum(1 for r in pend if r.get("due_out_is_red")),
            "pending_art_red":   sum(1 for r in pend if r.get("art_is_red")),
        }
    return {"date": str(d), "tabs": result}


@app.get("/api/ads")
def ads_detail(
    tab:      str = Query(...),
    category: str = Query(..., description="incoming|outgoing|overdue|pending"),
    date_str: str = Query(default=None),
):
    """Returns full ad rows for a specific tab + category."""
    d = date.fromisoformat(date_str) if date_str else date.today()
    if category == "incoming":
        rows = incoming_ads(tab, d)
    elif category == "outgoing":
        rows = outgoing_ads(tab, d)
    elif category == "overdue":
        rows = overdue_ads(tab, d)
    elif category == "pending":
        rows = pending_ads(tab, d)
    else:
        return {"error": "Unknown category"}

    # Convert datetime objects to strings
    for r in rows:
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.strftime("%Y-%m-%d %H:%M:%S")
    return {"tab": tab, "category": category, "date": str(d), "ads": rows}


@app.get("/api/dates")
def available_dates():
    """Returns list of dates that have data."""
    rows = query("SELECT DISTINCT DATE(snapshot_time) AS d FROM ad_snapshots ORDER BY d DESC LIMIT 60")
    return {"dates": [str(r["d"]) for r in rows]}


@app.get("/api/last_updated")
def last_updated():
    rows = query("SELECT MAX(snapshot_time) AS t FROM ad_snapshots")
    t = rows[0]["t"] if rows else None
    return {"last_updated": t.strftime("%Y-%m-%d %H:%M:%S") if t else None}


# Serve the frontend
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
