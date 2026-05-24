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
import requests
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

BASE_URL     = os.getenv("WEBHOOK_BASE_URL", "https://grocpilot.onrender.com")
DB_PATH      = os.getenv("DB_PATH", "groc.db")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


# ---------------------------------------------------------------------------
# Supabase REST API helpers — uses requests only, no supabase package
# ---------------------------------------------------------------------------

def _using_supabase() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)

def _sb_headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }

def _sb_get(table: str, filters: dict) -> list:
    """GET rows from Supabase table matching filters."""
    import requests as _req
    params = {f"{k}": f"eq.{v}" for k, v in filters.items()}
    try:
        r = _req.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_sb_headers(), params=params, timeout=8
        )
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"[TRACKING] Supabase GET error: {e}")
        return []

def _sb_insert(table: str, record: dict) -> bool:
    """INSERT a row into Supabase table."""
    import requests as _req
    try:
        r = _req.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_sb_headers(), json=record, timeout=8
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"[TRACKING] Supabase INSERT error: {e}")
        return False

def _sb_patch(table: str, filters: dict, data: dict) -> bool:
    """PATCH rows in Supabase table matching filters."""
    import requests as _req
    params = {f"{k}": f"eq.{v}" for k, v in filters.items()}
    try:
        r = _req.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_sb_headers(), params=params, json=data, timeout=8
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"[TRACKING] Supabase PATCH error: {e}")
        return False


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
    if _using_supabase():
        existing = _sb_get("groc_tokens", {"place_id": place_id})
        if existing:
            return existing[0]["token"]
        token  = secrets.token_urlsafe(16)
        record = {
            "token": token, "place_id": place_id, "store_name": store_name,
            "email": email, "report_filename": report_filename, "created_at": now,
        }
        if _sb_insert("groc_tokens", record):
            print(f"[TRACKING] Token created (Supabase) for {store_name}: {token}")
            return token
        print(f"[TRACKING] Supabase insert failed — falling back to SQLite")

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
    if _using_supabase():
        rows = _sb_get("groc_tokens", {"place_id": place_id})
        if rows:
            return rows[0]["token"]

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
    if _using_supabase():
        rows = _sb_get("groc_tokens", {"token": token})
        if rows:
            return rows[0]

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
    if _using_supabase():
        if _sb_patch("groc_tokens", {"token": token}, {"report_filename": report_filename}):
            return

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
    if _using_supabase():
        if _sb_insert("groc_tracking", event):
            print(f"[TRACKING] {event_type} | {store_name or token}")
            return

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
    if _using_supabase():
        print(f"\nReading from Supabase...\n")
        import requests as _req
        tokens = _req.get(f"{SUPABASE_URL}/rest/v1/groc_tokens?order=created_at", headers=_sb_headers()).json() or []
        events = _req.get(f"{SUPABASE_URL}/rest/v1/groc_tracking?order=occurred_at.desc&limit=50", headers=_sb_headers()).json() or []
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
