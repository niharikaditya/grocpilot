"""
webhook_server.py — Groc pilot tracking and report serving.

Storage: Supabase primary, SQLite fallback (see tracking.py).
All token reads/writes go through tracking.py which handles
the Supabase-first pattern — no direct SQLite calls here.

Routes:
  GET  /                  Health check
  GET  /pixel/<token>     Email open tracking
  GET  /report/<token>    Log click + serve HTML report
  GET  /unsubscribe       Opt-out handler
  GET  /events            Tracking dashboard
  POST /tokens/create     Create tracked token for a store
"""

import glob
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_file

load_dotenv()

app = Flask(__name__)

BASE_URL     = os.getenv("WEBHOOK_BASE_URL", "https://grocpilot.onrender.com")
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


def is_bot(ua: str) -> bool:
    ua = (ua or "").lower()
    return any(s in ua for s in BOT_SIGNALS)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    from tracking import _using_supabase
    return jsonify({
        "status":   "ok",
        "service":  "grocpilot tracking server",
        "storage":  "supabase" if _using_supabase() else "sqlite",
        "version":  "2.0",
    })


# ---------------------------------------------------------------------------
# Tracking pixel
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
# Report serving — reads token from Supabase via tracking.resolve_token()
# ---------------------------------------------------------------------------

@app.route("/report/<token>", methods=["GET"])
def serve_report(token):
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ua = request.headers.get("User-Agent", "")

    # Resolve token — Supabase first, SQLite fallback
    from tracking import resolve_token, log_event
    record = resolve_token(token)

    report_filename = record.get("report_filename", "")
    store_name      = record.get("store_name", "")

    if not record:
        print(f"[REPORT] Token not found: {token}")

    # Log click
    if not is_bot(ua):
        try:
            log_event(token, "report_clicked", ip=ip)
        except Exception as e:
            print(f"[REPORT] Log event error: {e}")

    # Serve HTML file
    if report_filename:
        candidates = [
            report_filename,
            os.path.join(REPORTS_DIR, report_filename),
        ] + glob.glob(
            os.path.join(REPORTS_DIR, "**", report_filename), recursive=True
        )
        for path in candidates:
            if os.path.exists(path):
                print(f"[REPORT] Serving {path}")
                return send_file(path, mimetype="text/html")

    print(f"[REPORT] File not found for token {token} (filename: {report_filename!r})")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Report not found</title>
<style>body{{font-family:Arial,sans-serif;max-width:500px;margin:80px auto;
padding:0 20px;color:#2D2D2A}}h1{{font-size:22px}}
p{{color:#6B6A64;line-height:1.6}}</style></head>
<body><h1>Report coming soon</h1>
<p>Hi{(' ' + store_name) if store_name else ''} — your market snapshot
is being prepared. {CONTACT_NAME} will follow up shortly.</p>
</body></html>""", 200


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------

@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    token = request.args.get("token", "").strip()
    if token:
        try:
            from tracking import log_event, resolve_token, _sb_insert, _using_supabase
            log_event(token, "unsubscribed")
            if _using_supabase():
                rec = resolve_token(token)
                if rec.get("email"):
                    _sb_insert("groc_unsubscribes", {
                        "email_address": rec["email"],
                        "place_id":      rec.get("place_id", ""),
                        "created_at":    datetime.now(timezone.utc).isoformat(),
                    })
        except Exception as e:
            print(f"[UNSUBSCRIBE] {e}")

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Unsubscribed</title>
<style>body{font-family:Arial,sans-serif;max-width:500px;margin:80px auto;
padding:0 20px;color:#2D2D2A}h1{font-size:22px}
p{color:#6B6A64;line-height:1.6}</style></head>
<body><h1>You've been unsubscribed.</h1>
<p>No further emails from us. Apologies for the interruption.</p>
</body></html>""", 200


# ---------------------------------------------------------------------------
# Events dashboard — reads from Supabase
# ---------------------------------------------------------------------------

@app.route("/events", methods=["GET"])
def events():
    rows = []
    try:
        from tracking import _using_supabase, _sb_get
        if _using_supabase():
            events_data = _sb_get("groc_tracking?order=occurred_at.desc&limit=200", {}) or []
            # _sb_get doesn't support raw query strings — use requests directly
            import requests as _req, os as _os
            from tracking import SUPABASE_URL, SUPABASE_KEY, _sb_headers
            ev_r = _req.get(f"{SUPABASE_URL}/rest/v1/groc_tracking?order=occurred_at.desc&limit=200",
                            headers=_sb_headers())
            tk_r = _req.get(f"{SUPABASE_URL}/rest/v1/groc_tokens?select=token,email",
                            headers=_sb_headers())
            ev_data = ev_r.json() if ev_r.status_code == 200 else []
            tk_data = tk_r.json() if tk_r.status_code == 200 else []
            token_emails = {t["token"]: t.get("email","") for t in tk_data}
            rows = [(r.get("store_name",""), r.get("event_type",""), r.get("occurred_at",""),
                     r.get("ip",""), token_emails.get(r.get("token",""),""))
                    for r in ev_data]
        else:
            # SQLite fallback
            import sqlite3
            from tracking import DB_PATH, _init_db
            _init_db()
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("""
                SELECT t.store_name, t.event_type, t.occurred_at, t.ip, tk.email
                FROM groc_tracking t
                LEFT JOIN groc_tokens tk ON t.token = tk.token
                ORDER BY t.occurred_at DESC LIMIT 200
            """).fetchall()
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    rows_html = ""
    for store_name, event_type, occurred_at, ip, email in rows:
        ts    = (occurred_at or "")[:16].replace("T", " ")
        badge = {"email_opened":"🟡","report_clicked":"🟢","unsubscribed":"🔴"}.get(event_type,"⚪")
        rows_html += f"""<tr><td>{ts}</td><td>{badge} {event_type}</td>
          <td>{store_name or '—'}</td>
          <td style="color:#9c9a92;font-size:12px">{email or '—'}</td></tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Groc Pilot — Tracking</title>
<style>body{{font-family:Arial,sans-serif;max-width:800px;margin:40px auto;
padding:0 20px;color:#1a1a19}}h1{{font-size:20px;color:#085041}}
table{{width:100%;border-collapse:collapse;margin-top:20px}}
th{{text-align:left;font-size:12px;color:#9c9a92;padding:8px 12px;
border-bottom:2px solid #e8e5e0}}
td{{padding:10px 12px;border-bottom:1px solid #f0ede8;font-size:14px}}
tr:hover td{{background:#fafaf8}}</style></head>
<body><h1>Groc Pilot — Tracking Events</h1>
<table><thead><tr><th>Time (UTC)</th><th>Event</th>
<th>Store</th><th>Email</th></tr></thead>
<tbody>{rows_html}</tbody></table></body></html>""", 200


# ---------------------------------------------------------------------------
# Token creation — writes to Supabase via tracking.create_token()
# ---------------------------------------------------------------------------

@app.route("/tokens/create", methods=["POST"])
def create_token_route():
    data          = request.get_json(force=True) or {}
    place_id      = data.get("place_id", "").strip()
    email         = data.get("email", "").strip()
    business_name = data.get("business_name", "").strip()
    report_type   = data.get("report_type", "stage2_personalised").strip()

    if not place_id or not email:
        return jsonify({"error": "place_id and email are required"}), 400

    from tracking import create_token, get_report_url
    token = create_token(
        place_id      = place_id,
        store_name    = business_name,
        email         = email,
        report_filename = ""  # updated later when report is generated
    )
    url = get_report_url(token)
    print(f"[TOKENS/CREATE] {business_name} -> {token[:12]}...")
    return jsonify({"token": token, "url": url}), 200


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
