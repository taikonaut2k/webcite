"""
Visitor counter for WebCite.

Uses SQLite with WAL mode — safe for concurrent writes from
multiple gunicorn workers (unlike a JSON file which can race).

Tracks:
  - total visits (every pageview)
  - unique visitors (cookie-based)
  - today's visits + today's unique visitors

Data stored alongside archives at ~/archive-clone/visitors.db
(overridable via VISITORS_DB env var).

On Render free tier the filesystem is ephemeral, so counts reset
on redeploy — acceptable for now; the archive data has the same
limitation. If a persistent disk is added later, point VISITORS_DB
at it.
"""

import os, sqlite3, threading, time
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(os.environ.get("VISITORS_DB", str(Path.home() / "archive-clone" / "visitors.db")))

# Bots / monitoring services we don't want inflating the count
_BOT_PATTERNS = [
    "bot", "crawler", "spider", "curl", "wget", "python-requests",
    "python-urllib", "uptimerobot", "statuscake", "pingdom",
    "headlesschrome", "monitor", "healthcheck", "googlebot",
    "bingbot", "duckduckbot", "yandex", "baiduspider", "semrush",
    "ahrefs", "mj12", "dotbot", "petalbot", "facebookexternalhit",
    "slurp", "archive.org_bot", "go-http-client", "okhttp",
]

_lock = threading.Lock()

def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        visitor_id TEXT NOT NULL,
        ts INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_visitor ON visits(visitor_id)")
    conn.commit()
    return conn

def is_bot(user_agent):
    """True if the User-Agent looks like a bot/monitor we should ignore."""
    ua = (user_agent or "").lower()
    return any(p in ua for p in _BOT_PATTERNS)

def record_visit(visitor_id):
    """Record one pageview. Thread-safe across processes (SQLite WAL)."""
    with _lock:
        try:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO visits (visitor_id, ts) VALUES (?, ?)",
                    (visitor_id, int(time.time())),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            # Never let a counter failure break the site
            pass

def get_stats():
    """Return dict with total/today/uniques."""
    try:
        conn = _connect()
        try:
            now = datetime.now(timezone.utc)
            day_start = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp())
            total = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            today = conn.execute("SELECT COUNT(*) FROM visits WHERE ts >= ?", (day_start,)).fetchone()[0]
            uniques = conn.execute("SELECT COUNT(DISTINCT visitor_id) FROM visits").fetchone()[0]
            uniques_today = conn.execute(
                "SELECT COUNT(DISTINCT visitor_id) FROM visits WHERE ts >= ?", (day_start,)
            ).fetchone()[0]
            return {
                "total_visits": total,
                "today_visits": today,
                "unique_visitors": uniques,
                "unique_today": uniques_today,
            }
        finally:
            conn.close()
    except Exception:
        return {"total_visits": 0, "today_visits": 0, "unique_visitors": 0, "unique_today": 0}
