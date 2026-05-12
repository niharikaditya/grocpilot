"""
tracking.py — Token generation and management for groc pilot.

Each store gets one UUID token that ties together:
  - The tracking pixel (email open)
  - The report link (click)
  - The Supabase groc_tokens record

Usage:
    from tracking import create_token, get_pixel_url, get_report_url

    token = create_token(place_id, store_name, email, report_filename)
    pixel = get_pixel_url(token)   # embed in email HTML as 1x1 <img>
    link  = get_report_url(token)  # use as report CTA link in email
"""

import os
import secrets
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

BASE_URL   = os.getenv("WEBHOOK_BASE_URL", "https://grocpilot.onrender.com")
DB_PATH    = os.getenv("DB_PATH", "groc.db")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


# ---------------------------------------------------------------------------
# SQLite setup — local backup, also used when Supabase is unavailable
# ---------------------------------------------------------------------------

def _init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groc_tokens (
            token           TEXT PRIMARY KEY,
            place_id        TEXT,
            store_name      TEXT,
            email           TEXT,
            report_filename TEXT,
            created_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groc_tracking (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token       TEXT,
            place_id    TEXT,
            store_name  TEXT,
            event_type  TEXT,
            occurred_at TEXT,
            ip          TEXT
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Supabase helpers (same creds as vetpipeline, groc_ table prefix)
# ---------------------------------------------------------------------------

def _get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None


def _supabase_upsert_token(record: dict):
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.table("groc_tokens").upsert(record).execute()
    except Exception as e:
        print(f"[TRACKING] Supabase token upsert failed: {e}")


def _supabase_log_event(record: dict):
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.table("groc_tracking").insert(record).execute()
    except Exception as e:
        print(f"[TRACKING] Supabase event insert failed: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_token(place_id: str, store_name: str,
                 email: str = "", report_filename: str = "") -> str:
    """
    Generate a unique token for this store, persist to SQLite + Supabase.
    Idempotent — if a token already exists for this place_id, return it.
    """
    _init_db()
    conn = sqlite3.connect(DB_PATH)

    # Return existing token if already created for this place_id
    row = conn.execute(
        "SELECT token FROM groc_tokens WHERE place_id = ?", (place_id,)
    ).fetchone()
    if row:
        conn.close()
        return row[0]

    token = secrets.token_urlsafe(16)
    now   = datetime.now(timezone.utc).isoformat()

    record = {
        "token":           token,
        "place_id":        place_id,
        "store_name":      store_name,
        "email":           email,
        "report_filename": report_filename,
        "created_at":      now,
    }

    conn.execute("""
        INSERT OR IGNORE INTO groc_tokens
            (token, place_id, store_name, email, report_filename, created_at)
        VALUES (:token, :place_id, :store_name, :email, :report_filename, :created_at)
    """, record)
    conn.commit()
    conn.close()

    _supabase_upsert_token(record)
    print(f"[TRACKING] Token created for {store_name}: {token}")
    return token


def get_token(place_id: str) -> str:
    """Return the token for a place_id, or empty string if not found."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT token FROM groc_tokens WHERE place_id = ?", (place_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else ""


def get_pixel_url(token: str) -> str:
    """Returns the tracking pixel URL to embed in the email as a 1x1 <img>."""
    return f"{BASE_URL}/pixel/{token}"


def get_report_url(token: str) -> str:
    """Returns the tracked report link to use as the CTA in the email."""
    return f"{BASE_URL}/report/{token}"


def log_event(token: str, event_type: str, ip: str = ""):
    """Log an open or click event to SQLite + Supabase."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT place_id, store_name FROM groc_tokens WHERE token = ?", (token,)
    ).fetchone()

    place_id   = row[0] if row else ""
    store_name = row[1] if row else ""
    now        = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO groc_tracking (token, place_id, store_name, event_type, occurred_at, ip)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (token, place_id, store_name, event_type, now, ip))
    conn.commit()
    conn.close()

    _supabase_log_event({
        "token":       token,
        "place_id":    place_id,
        "store_name":  store_name,
        "event_type":  event_type,
        "occurred_at": now,
        "ip":          ip,
    })
    print(f"[TRACKING] {event_type} | {store_name or token}")


# ---------------------------------------------------------------------------
# CLI — print current tracking table
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _init_db()
    conn = sqlite3.connect(DB_PATH)

    tokens = conn.execute(
        "SELECT store_name, place_id, token, created_at FROM groc_tokens ORDER BY created_at"
    ).fetchall()

    events = conn.execute("""
        SELECT store_name, event_type, occurred_at
        FROM groc_tracking
        ORDER BY occurred_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()

    print(f"\n{'='*60}")
    print(f"  GROC PILOT TOKENS ({len(tokens)} stores)")
    print(f"{'='*60}")
    for store_name, place_id, token, created_at in tokens:
        print(f"  {store_name:<35} {token}")

    print(f"\n{'='*60}")
    print(f"  RECENT EVENTS ({len(events)})")
    print(f"{'='*60}")
    for store_name, event_type, occurred_at in events:
        ts = occurred_at[:16].replace("T", " ") if occurred_at else ""
        print(f"  {ts}  {event_type:<20}  {store_name}")
    print()
