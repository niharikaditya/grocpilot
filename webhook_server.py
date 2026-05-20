"""
webhook_server.py — Groc pilot tracking and report serving.

Stripped from vetpipeline's 12-route server to 4 routes only.
Uses Supabase groc_* tables (same account, different prefix).

Routes:
  GET  /                    Health check
  GET  /pixel/<token>       Email open tracking pixel
  GET  /report/<token>      Log report click + serve HTML report
  GET  /unsubscribe         Opt-out handler
  GET  /events              View all tracking events (for Niharika)

Env vars (same as vetpipeline):
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
  WEBHOOK_BASE_URL          e.g. https://grocpilot.onrender.com
  DB_PATH                   defaults to groc.db
"""

import os
import glob
import json
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, redirect, request, send_file
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

BASE_URL     = os.getenv("WEBHOOK_BASE_URL", "https://grocpilot.onrender.com")
DB_PATH      = os.getenv("DB_PATH", "groc.db")
REPORTS_DIR  = os.getenv("REPORTS_DIR", "reports")
CONTACT_NAME = "Niharika"

BOT_SIGNALS = [
    "bot", "crawler", "spider", "preview", "slack", "twitter",
    "facebook", "linkedin", "whatsapp", "googlebot", "bingbot",
    "yahoo", "baidu", "duckduck", "semrush", "ahrefs",
]

TRANSPARENT_GIF = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00"
    b"\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00"
    b"\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02"
    b"\x44\x01\x00\x3b"
)


def is_bot(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    return any(s in ua for s in BOT_SIGNALS)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "service": "grocpilot tracking server",
        "version": "1.0",
    })


# ---------------------------------------------------------------------------
# Tracking pixel  /pixel/<token>
# Embed in each email as:
#   <img src="https://grocpilot.onrender.com/pixel/{token}"
#        width="1" height="1" style="display:none" alt="">
# ---------------------------------------------------------------------------

@app.route("/pixel/<token>", methods=["GET"])
def tracking_pixel(token):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ua = request.headers.get("User-Agent", "")

    if not is_bot(ua):
        try:
            from tracking import log_event
            log_event(token, "email_opened", ip=ip)
        except Exception as e:
            print(f"[PIXEL ERROR] {e}")

    return Response(
        TRANSPARENT_GIF,
        mimetype="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma":        "no-cache",
            "Expires":       "0",
        },
    )


# ---------------------------------------------------------------------------
# Report serving  /report/<token>
# Logs the click then serves the HTML report file.
# ---------------------------------------------------------------------------

@app.route("/report/<token>", methods=["GET"])
def serve_report(token):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ua = request.headers.get("User-Agent", "")

    # Resolve report filename from token
    report_filename = ""
    store_name = ""
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute(
            "SELECT report_filename, store_name FROM groc_tokens WHERE token = ?",
            (token,)
        ).fetchone()
        conn.close()
        if row:
            report_filename = row[0] or ""
            store_name      = row[1] or ""
    except Exception as e:
        print(f"[REPORT] DB lookup error: {e}")

    # Log click event (not bots)
    if not is_bot(ua):
        try:
            from tracking import log_event
            log_event(token, "report_clicked", ip=ip)
        except Exception as e:
            print(f"[REPORT] Log event error: {e}")

    # Serve the report file
    if report_filename:
        # Try direct path first, then search in reports dir
        candidates = [
            report_filename,
            os.path.join(REPORTS_DIR, report_filename),
        ]
        # Also search recursively
        pattern_matches = glob.glob(
            os.path.join(REPORTS_DIR, "**", report_filename), recursive=True
        )
        candidates += pattern_matches

        for path in candidates:
            if os.path.exists(path):
                print(f"[REPORT] Serving {path} for token {token}")
                return send_file(path, mimetype="text/html")

    # Fallback — report not found
    print(f"[REPORT] File not found for token {token} (filename: {report_filename})")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Report not found</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 500px;
           margin: 80px auto; padding: 0 20px; color: #2D2D2A; }}
    h1 {{ font-size: 22px; }}
    p {{ color: #6B6A64; line-height: 1.6; }}
  </style>
</head>
<body>
  <h1>Report coming soon</h1>
  <p>Hi{(' ' + store_name) if store_name else ''} — your market snapshot is being prepared.
     {CONTACT_NAME} will follow up shortly.</p>
</body>
</html>""", 200


# ---------------------------------------------------------------------------
# Unsubscribe  /unsubscribe
# ---------------------------------------------------------------------------

@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    token    = request.args.get("token", "").strip()
    email    = request.args.get("email", "").strip().lower()
    place_id = request.args.get("place_id", "").strip()

    if token:
        try:
            from tracking import log_event
            log_event(token, "unsubscribed")
        except Exception as e:
            print(f"[UNSUBSCRIBE] {e}")

    # Also log to SQLite directly if we have email/place_id but no token
    if (email or place_id) and not token:
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS groc_unsubscribes (
                    email_address TEXT UNIQUE,
                    place_id      TEXT,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO groc_unsubscribes (email_address, place_id) VALUES (?, ?)",
                (email, place_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[UNSUBSCRIBE DB] {e}")

    return """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Unsubscribed</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 500px;
           margin: 80px auto; padding: 0 20px; color: #2D2D2A; }
    h1 { font-size: 22px; }
    p { color: #6B6A64; line-height: 1.6; }
  </style>
</head>
<body>
  <h1>You've been unsubscribed.</h1>
  <p>No further emails from us. Apologies for the interruption.</p>
</body>
</html>""", 200


# ---------------------------------------------------------------------------
# Events view  /events
# Simple dashboard for Niharika to check who opened / clicked.
# ---------------------------------------------------------------------------

@app.route("/events", methods=["GET"])
def events():
    try:
        import sqlite3
        conn   = sqlite3.connect(DB_PATH)
        rows   = conn.execute("""
            SELECT t.store_name, t.event_type, t.occurred_at, t.ip,
                   tk.email
            FROM groc_tracking t
            LEFT JOIN groc_tokens tk ON t.token = tk.token
            ORDER BY t.occurred_at DESC
            LIMIT 200
        """).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Build a simple HTML table
    rows_html = ""
    for store_name, event_type, occurred_at, ip, email in rows:
        ts    = (occurred_at or "")[:16].replace("T", " ")
        badge = {
            "email_opened":   "🟡",
            "report_clicked": "🟢",
            "unsubscribed":   "🔴",
        }.get(event_type, "⚪")
        rows_html += f"""
        <tr>
          <td>{ts}</td>
          <td>{badge} {event_type}</td>
          <td>{store_name or '—'}</td>
          <td style="color:#9c9a92;font-size:12px">{email or '—'}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Groc Pilot — Tracking</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 800px;
           margin: 40px auto; padding: 0 20px; color: #1a1a19; }}
    h1 {{ font-size: 20px; color: #085041; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    th {{ text-align: left; font-size: 12px; color: #9c9a92;
          padding: 8px 12px; border-bottom: 2px solid #e8e5e0; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #f0ede8;
          font-size: 14px; }}
    tr:hover td {{ background: #fafaf8; }}
  </style>
</head>
<body>
  <h1>Groc Pilot — Tracking Events</h1>
  <table>
    <thead>
      <tr>
        <th>Time (UTC)</th>
        <th>Event</th>
        <th>Store</th>
        <th>Email</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>""", 200


# ---------------------------------------------------------------------------
# Token creation  POST /tokens/create
# Called once per business to generate a unique tracked report URL.
# Stores token in groc_tokens table and returns the full URL.
# ---------------------------------------------------------------------------

@app.route("/tokens/create", methods=["POST"])
def create_token():
    data         = request.get_json(force=True) or {}
    place_id     = data.get("place_id", "").strip()
    email        = data.get("email", "").strip()
    business_name = data.get("business_name", "").strip()
    city         = data.get("city", "").strip()
    report_type  = data.get("report_type", "stage2_personalised").strip()

    if not place_id or not email:
        return jsonify({"error": "place_id and email are required"}), 400

    import secrets, sqlite3
    token = secrets.token_urlsafe(24)
    url   = f"{BASE_URL}/report/{token}"

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS groc_tokens (
                token           TEXT PRIMARY KEY,
                place_id        TEXT,
                email           TEXT,
                store_name      TEXT,
                city            TEXT,
                report_type     TEXT,
                report_filename TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO groc_tokens
                (token, place_id, email, store_name, city, report_type)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (token, place_id, email, business_name, city, report_type))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TOKENS/CREATE] DB error: {e}")
        return jsonify({"error": str(e)}), 500

    print(f"[TOKENS/CREATE] {business_name} -> {token[:12]}...")
    return jsonify({"token": token, "url": url}), 200


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
