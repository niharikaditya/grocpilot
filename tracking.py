"""
tracking.py — Token generation and event logging for groc pilot.

Storage strategy:
  PRIMARY:  Supabase (groc_tokens, groc_tracking tables)
            — used on Render and anywhere SUPABASE_URL is set
  FALLBACK: SQLite (groc.db)
            — used for local dev only when Supabase creds are absent

This means tokens survive Render redeploys, cross-machine usage,
and are accessible from both the local scripts and the live server.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

BASE_URL     = os.getenv("WEBHOOK_BASE_URL", "https://grocpilot.onrender.com")
DB_PATH      = os.getenv("DB_PATH", "groc.db")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


# ---------------------------------------------------------------------------
# Supabase client (singleton)
# ---------------------------------------------------------------------------

_sb_client = None

def _sb():
    """Return Supabase client, or None if creds are missing."""
    global _sb_client
    if _sb_client is not None:
        return _sb_client
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        from supabase import create_client
        _sb_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _sb_client
    except Exception as e:
        print(f"[TRACKING] Supabase init failed: {e}")
        return None


def _using_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


# ---------------------------------------------------------------------------
# SQLite fallback — only initialised when Supabase is unavailable
# ---------------------------------------------------------------------------

def _init_db():
    conn = sqlite3.connect(DB_PATH)
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
# Public API
# ---------------------------------------------------------------------------

def create_token(place_id: str, store_name: str,
                 email: str = "", report_filename: str = "") -> str:
    """
    Generate a unique token for this store.
    Idempotent — returns existing token if place_id already has one.
    Writes to Supabase (primary) and SQLite (fallback/backup).
    """
    now   = datetime.now(timezone.utc).isoformat()

    # ── Supabase path ───────────────────────────────────────────
    sb = _sb()
    if sb:
        try:
            # Check for existing token
            existing = sb.table("groc_tokens") \
                         .select("token") \
                         .eq("place_id", place_id) \
                         .execute()
            if existing.data:
                return existing.data[0]["token"]

            token  = secrets.token_urlsafe(16)
            record = {
                "token":           token,
                "place_id":        place_id,
                "store_name":      store_name,
                "email":           email,
                "report_filename": report_filename,
                "created_at":      now,
            }
            sb.table("groc_tokens").insert(record).execute()
            print(f"[TRACKING] Token created (Supabase) for {store_name}: {token}")
            return token
        except Exception as e:
            print(f"[TRACKING] Supabase create_token error: {e} — falling back to SQLite")

    # ── SQLite fallback ─────────────────────────────────────────
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT token FROM groc_tokens WHERE place_id = ?", (place_id,)
    ).fetchone()
    if row:
        conn.close()
        return row[0]

    token  = secrets.token_urlsafe(16)
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
    print(f"[TRACKING] Token created (SQLite) for {store_name}: {token}")
    return token


def get_token(place_id: str) -> str:
    """Return the token for a place_id. Reads Supabase first, SQLite fallback."""
    sb = _sb()
    if sb:
        try:
            result = sb.table("groc_tokens") \
                       .select("token") \
                       .eq("place_id", place_id) \
                       .execute()
            if result.data:
                return result.data[0]["token"]
        except Exception as e:
            print(f"[TRACKING] Supabase get_token error: {e}")

    # SQLite fallback
    try:
        _init_db()
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute(
            "SELECT token FROM groc_tokens WHERE place_id = ?", (place_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def resolve_token(token: str) -> dict:
    """
    Look up a token and return its record dict.
    Returns {} if not found.
    Reads Supabase first, SQLite fallback.
    """
    sb = _sb()
    if sb:
        try:
            result = sb.table("groc_tokens") \
                       .select("*") \
                       .eq("token", token) \
                       .execute()
            if result.data:
                return result.data[0]
        except Exception as e:
            print(f"[TRACKING] Supabase resolve_token error: {e}")

    # SQLite fallback
    try:
        _init_db()
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute(
            "SELECT token, place_id, store_name, email, report_filename "
            "FROM groc_tokens WHERE token = ?", (token,)
        ).fetchone()
        conn.close()
        if row:
            return {
                "token":           row[0],
                "place_id":        row[1],
                "store_name":      row[2],
                "email":           row[3],
                "report_filename": row[4],
            }
    except Exception:
        pass
    return {}


def update_report_filename(token: str, report_filename: str):
    """Update the report_filename for an existing token."""
    sb = _sb()
    if sb:
        try:
            sb.table("groc_tokens") \
              .update({"report_filename": report_filename}) \
              .eq("token", token) \
              .execute()
            return
        except Exception as e:
            print(f"[TRACKING] Supabase update_report_filename error: {e}")

    try:
        _init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE groc_tokens SET report_filename = ? WHERE token = ?",
            (report_filename, token)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TRACKING] SQLite update_report_filename error: {e}")


def get_pixel_url(token: str) -> str:
    return f"{BASE_URL}/pixel/{token}"


def get_report_url(token: str) -> str:
    return f"{BASE_URL}/report/{token}"


def log_event(token: str, event_type: str, ip: str = ""):
    """Log an open/click/unsubscribe event. Writes Supabase first, SQLite backup."""
    record_info = resolve_token(token)
    place_id    = record_info.get("place_id", "")
    store_name  = record_info.get("store_name", "")
    now         = datetime.now(timezone.utc).isoformat()

    event = {
        "token":       token,
        "place_id":    place_id,
        "store_name":  store_name,
        "event_type":  event_type,
        "occurred_at": now,
        "ip":          ip,
    }

    # Supabase
    sb = _sb()
    if sb:
        try:
            sb.table("groc_tracking").insert(event).execute()
            print(f"[TRACKING] {event_type} | {store_name or token}")
            return
        except Exception as e:
            print(f"[TRACKING] Supabase log_event error: {e}")

    # SQLite fallback
    try:
        _init_db()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO groc_tracking
                (token, place_id, store_name, event_type, occurred_at, ip)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (token, place_id, store_name, event_type, now, ip))
        conn.commit()
        conn.close()
        print(f"[TRACKING] {event_type} | {store_name or token}")
    except Exception as e:
        print(f"[TRACKING] SQLite log_event error: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sb = _sb()
    if sb:
        print(f"\nReading from Supabase...\n")
        tokens = sb.table("groc_tokens").select("*").order("created_at").execute().data
        events = sb.table("groc_tracking").select("*").order("occurred_at", desc=True).limit(50).execute().data
    else:
        print(f"\nSupabase not configured — reading from SQLite...\n")
        _init_db()
        conn   = sqlite3.connect(DB_PATH)
        tokens = [dict(zip(["store_name","place_id","token","created_at"], r))
                  for r in conn.execute(
                      "SELECT store_name, place_id, token, created_at FROM groc_tokens ORDER BY created_at"
                  ).fetchall()]
        events = [dict(zip(["store_name","event_type","occurred_at"], r))
                  for r in conn.execute(
                      "SELECT store_name, event_type, occurred_at FROM groc_tracking ORDER BY occurred_at DESC LIMIT 50"
                  ).fetchall()]
        conn.close()

    print(f"{'='*60}")
    print(f"  TOKENS ({len(tokens)} stores)")
    print(f"{'='*60}")
    for t in tokens:
        print(f"  {t.get('store_name',''):<35} {t.get('token','')}")

    print(f"\n{'='*60}")
    print(f"  RECENT EVENTS ({len(events)})")
    print(f"{'='*60}")
    for e in events:
        ts = (e.get("occurred_at","") or "")[:16].replace("T"," ")
        print(f"  {ts}  {e.get('event_type',''):<20}  {e.get('store_name','')}")
    print()
