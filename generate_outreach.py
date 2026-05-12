"""
generate_outreach.py — Single personalised email per store, groc pilot.

Reads analyse output for a store, calls Claude Sonnet to write
one email in Niharika's personal voice, then saves an HTML preview
file Niharika can open in her browser, review, and copy-paste into Gmail.

Output per store:
  outreach/pnw/indian_grocery/<safe_store_name>_preview.html

Usage:
  python generate_outreach.py                  # all TIER_A/B stores
  python generate_outreach.py --place_id XYZ   # single store
"""

import json
import os
import re
import sqlite3
from datetime import datetime

import anthropic
from dotenv import load_dotenv

load_dotenv()

from config_loader import load as _load_cfg
cfg, paths = _load_cfg()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_KEY"))

SENDER_NAME = "Niharika"
DB_PATH     = os.getenv("DB_PATH", "groc.db")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_name(s: str) -> str:
    return re.sub(r"[^\w\s-]", "", s).strip().replace(" ", "_")


def load_analyse_output(place_id: str) -> dict:
    """Load Claude's analysis output for this store."""
    for pattern in [
        paths.data_file(f"{place_id}_analysis.json"),
        paths.data_file("analysis.json"),
        f"data/{cfg.city}/{cfg.industry}/latest/analysis.json",
    ]:
        if os.path.exists(pattern):
            try:
                data = json.load(open(pattern))
                # Some analysis files are keyed by place_id
                if place_id in data:
                    return data[place_id]
                return data
            except Exception:
                pass
    return {}


def load_stats(place_id: str) -> dict:
    """Load stats_lite output for this store."""
    stats_path = paths.data_file("stats_lite.json")
    if not os.path.exists(stats_path):
        stats_path = paths.data_file("stats.json")
    if os.path.exists(stats_path):
        try:
            raw = json.load(open(stats_path))
            return raw.get("businesses", raw).get(place_id, {})
        except Exception:
            pass
    return {}


def load_tier_a_b_stores() -> list:
    """Return all TIER_A and TIER_B stores from ownership scorer output."""
    stores = []
    slug   = cfg.city
    for tier in ("tier_a", "tier_b"):
        path = f"{slug}_{tier}.json"
        if os.path.exists(path):
            try:
                stores.extend(json.load(open(path)))
            except Exception:
                pass
    return stores


def get_store_email(place_id: str) -> str:
    """Look up contact email from groc.db tracking table (added manually)."""
    try:
        conn  = sqlite3.connect(DB_PATH)
        row   = conn.execute(
            "SELECT email FROM groc_tokens WHERE place_id = ?", (place_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Email generation via Claude Sonnet
# ---------------------------------------------------------------------------

def generate_email(store_name: str, place_id: str,
                   stats: dict, analysis: dict) -> dict:
    """
    Generate one email in Niharika's personal voice.
    Returns {"subject": str, "body": str}.
    """
    # Pull key signals from stats
    rating       = stats.get("rating", 0)
    review_count = stats.get("review_count", 0)
    monthly_rev  = round(stats.get("avg_monthly_reviews", 0), 1)
    neg_rate     = round(stats.get("neg_rate", 0) * 100, 1)
    cluster_name = stats.get("cluster_name", "your area")
    cluster_avg  = round(stats.get("cluster_avg_monthly_reviews", 0), 1)
    top_theme    = stats.get("top_negative_theme", "")
    top_positive = stats.get("top_positive_theme", "")

    # Pull from analysis if available
    key_finding = analysis.get("key_finding", "")
    risk_flag   = analysis.get("risk_flag", "")

    # Build a context block for Claude
    signal_context = f"""Store: {store_name}
Area: {cluster_name}
Google rating: {rating} stars ({review_count} reviews)
Monthly review pace: {monthly_rev}/mo (area avg: {cluster_avg}/mo)
Negative review rate: {neg_rate}%
Top positive theme: {top_positive or 'not identified'}
Top concern in reviews: {top_theme or 'not identified'}
Key analyst finding: {key_finding or 'not available'}
Risk flag: {risk_flag or 'none'}"""

    prompt = f"""You are helping Niharika write a personal cold email to the owner of an independent Indian grocery store in the Pacific Northwest.

Niharika is not representing any company. She is writing personally, as someone who has been analysing independent Indian grocery stores in the region and wants to share what she found.

Context about this store:
{signal_context}

Write ONE short, personal cold email. Rules:
- Subject line: specific and data-driven, max 55 characters, no question marks, no "Re:", no spam words (free, guarantee, etc.)
- Body: 80-100 words maximum
- Open with one specific observation about this store — use a real number or signal from the context
- Do not mention any company name, product, or service
- Do not use the phrase "market intelligence" or "analytics platform"
- Do not ask for a meeting or a call — only ask if they'd be interested in seeing a short snapshot
- Sign off as "Niharika" only, no last name, no title
- Tone: curious, direct, human — not salesy

Return ONLY a JSON object with exactly two keys: "subject" and "body".
No markdown, no preamble, no explanation. Just the JSON."""

    try:
        resp = client.messages.create(
            model      = "claude-sonnet-4-20250514",
            max_tokens = 600,
            messages   = [{"role": "user", "content": prompt}]
        )
        raw  = resp.content[0].text.strip()
        # Strip any accidental markdown fences
        raw  = re.sub(r"^```json\s*", "", raw)
        raw  = re.sub(r"```$", "", raw).strip()
        data = json.loads(raw)
        return {
            "subject": data.get("subject", "").strip(),
            "body":    data.get("body", "").strip(),
        }
    except Exception as e:
        print(f"  [ERROR] Claude generation failed for {store_name}: {e}")
        return {"subject": "", "body": ""}


# ---------------------------------------------------------------------------
# HTML preview builder
# ---------------------------------------------------------------------------

def build_preview_html(store_name: str, place_id: str,
                       subject: str, body: str,
                       pixel_url: str, report_url: str,
                       store_address: str = "", store_rating: str = "",
                       contact_email: str = "") -> str:
    """
    Build a clean HTML preview file Niharika opens in her browser.
    Shows subject, body, copy buttons, and send checklist.
    """

    # Inject tracking pixel and report link into display body
    body_with_link = body
    if report_url and "snapshot" in body.lower():
        # Replace last sentence containing "snapshot" with linked version
        body_with_link = re.sub(
            r"(snapshot[^.]*\.)",
            f'\\1 → <a href="{report_url}" style="color:#085041">View snapshot</a>',
            body, count=1
        )

    # Format body for HTML display
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body_html = ""
    for para in body.split("\n\n"):
        para = para.strip()
        if para:
            body_html += f"<p>{esc(para)}</p>\n"

    # Tracking pixel img tag (invisible, for open tracking)
    pixel_tag = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'

    # Report link for insertion into email
    report_link_display = report_url if report_url else "(no report linked yet)"

    # Unsubscribe URL
    from tracking import BASE_URL as _BASE
    unsub_url = f"{_BASE}/unsubscribe?token={place_id}"

    now = datetime.now().strftime("%d %b %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Email Preview — {store_name}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      background: #f4f2ef;
      color: #1a1a19;
      padding: 32px 20px;
      min-height: 100vh;
    }}
    .layout {{ max-width: 900px; margin: 0 auto; display: grid;
               grid-template-columns: 1fr 300px; gap: 24px; align-items: start; }}
    .card {{ background: #fff; border-radius: 12px;
             box-shadow: 0 1px 4px rgba(0,0,0,.08); overflow: hidden; }}
    .card-header {{
      padding: 16px 24px;
      background: #085041;
      color: #fff;
      display: flex; align-items: center; justify-content: space-between;
    }}
    .card-header h2 {{ font-size: 15px; font-weight: 600; }}
    .card-header span {{ font-size: 12px; opacity: .7; }}
    .subject-row {{
      padding: 16px 24px;
      border-bottom: 1px solid #f0ede8;
      display: flex; align-items: center; gap: 12px;
    }}
    .subject-label {{
      font-size: 11px; font-weight: 600; color: #9c9a92;
      text-transform: uppercase; letter-spacing: .06em;
      white-space: nowrap;
    }}
    .subject-text {{
      flex: 1; font-size: 15px; font-weight: 600; color: #1a1a19;
    }}
    .copy-btn {{
      background: #085041; color: #fff; border: none;
      border-radius: 6px; padding: 6px 14px;
      font-size: 12px; font-weight: 600; cursor: pointer;
      white-space: nowrap;
    }}
    .copy-btn:hover {{ background: #064033; }}
    .copy-btn.copied {{ background: #2da879; }}
    .body-area {{ padding: 24px; }}
    .body-area p {{ font-size: 15px; line-height: 1.75; color: #1a1a19;
                    margin-bottom: 16px; }}
    .body-area p:last-child {{ margin-bottom: 0; }}
    .sig {{ font-size: 15px; color: #1a1a19; padding-top: 8px; }}
    .tracking-row {{
      padding: 14px 24px;
      background: #fafaf8;
      border-top: 1px solid #f0ede8;
      font-size: 12px; color: #6b6a64;
    }}
    .tracking-row a {{ color: #085041; word-break: break-all; }}
    .actions {{ padding: 16px 24px;
                border-top: 1px solid #f0ede8;
                display: flex; gap: 10px; flex-wrap: wrap; }}
    /* Sidebar */
    .sidebar .card + .card {{ margin-top: 16px; }}
    .sidebar-title {{
      padding: 14px 20px;
      background: #f8f7f5;
      border-bottom: 1px solid #f0ede8;
      font-size: 12px; font-weight: 700;
      text-transform: uppercase; letter-spacing: .06em; color: #6b6a64;
    }}
    .sidebar-body {{ padding: 16px 20px; font-size: 13px; line-height: 1.7; }}
    .sidebar-body .row {{ display: flex; justify-content: space-between;
                          padding: 6px 0;
                          border-bottom: 1px solid #f8f7f5; }}
    .sidebar-body .row:last-child {{ border-bottom: none; }}
    .sidebar-body .key {{ color: #9c9a92; }}
    .sidebar-body .val {{ font-weight: 600; color: #1a1a19; }}
    .checklist {{ list-style: none; padding: 16px 20px; }}
    .checklist li {{
      font-size: 13px; line-height: 1.6; padding: 5px 0;
      display: flex; align-items: flex-start; gap: 8px;
      border-bottom: 1px solid #f8f7f5;
    }}
    .checklist li:last-child {{ border-bottom: none; }}
    .check {{ color: #ccc; font-size: 16px; flex-shrink: 0; cursor: pointer; }}
    .check:hover {{ color: #085041; }}
    .status-bar {{
      text-align: center; padding: 10px;
      font-size: 11px; color: #9c9a92;
      background: #f8f7f5;
      border-top: 1px solid #f0ede8;
      border-radius: 0 0 12px 12px;
    }}
    @media (max-width: 700px) {{
      .layout {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<div class="layout">

  <!-- Main email card -->
  <div>
    <div class="card">
      <div class="card-header">
        <h2>✉ Email Preview — {store_name}</h2>
        <span>Generated {now}</span>
      </div>

      <!-- Subject -->
      <div class="subject-row">
        <span class="subject-label">Subject</span>
        <span class="subject-text" id="subj-text">{subject}</span>
        <button class="copy-btn" onclick="copyText('subj-text', this)">Copy</button>
      </div>

      <!-- Body -->
      <div class="body-area" id="body-area">
        {body_html}
        <p class="sig">Niharika</p>
      </div>

      <!-- Tracking pixel note -->
      <div class="tracking-row">
        <strong>Tracking pixel</strong> (paste this invisible tag into the email HTML or use Mailtrack):<br>
        <code>{pixel_tag}</code>
      </div>

      <!-- Report link -->
      <div class="tracking-row" style="border-top:none; padding-top:0">
        <strong>Report link</strong> (use this URL as the "snapshot" link in your email):<br>
        <a href="{report_link_display}" target="_blank">{report_link_display}</a>
      </div>

      <!-- Unsubscribe note -->
      <div class="tracking-row" style="border-top:none; padding-top:0">
        <strong>Unsubscribe URL</strong> (add to email footer if needed):<br>
        <a href="{unsub_url}">{unsub_url}</a>
      </div>

      <!-- Copy full body -->
      <div class="actions">
        <button class="copy-btn" onclick="copyBody()">Copy full email body</button>
      </div>
      <div class="status-bar" id="status-bar">
        Review → Copy subject → Copy body → Paste into Gmail → Attach report → Send
      </div>
    </div>
  </div>

  <!-- Sidebar -->
  <div class="sidebar">
    <div class="card">
      <div class="sidebar-title">Store Info</div>
      <div class="sidebar-body">
        <div class="row"><span class="key">Name</span><span class="val">{store_name}</span></div>
        <div class="row"><span class="key">Address</span><span class="val">{store_address or '—'}</span></div>
        <div class="row"><span class="key">Rating</span><span class="val">{store_rating or '—'}</span></div>
        <div class="row"><span class="key">Contact</span><span class="val">{contact_email or 'Not found yet'}</span></div>
        <div class="row"><span class="key">Place ID</span>
          <span class="val" style="font-size:10px;word-break:break-all">{place_id}</span></div>
      </div>
    </div>

    <div class="card">
      <div class="sidebar-title">Send Checklist</div>
      <ul class="checklist">
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Read the email — does it sound like you?</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Confirm you have the owner's email address</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Verify the report is generated and linked</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Mailtrack is active on your Gmail</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Copy subject → paste into Gmail subject field</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Copy body → paste into Gmail body</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Replace [REPORT_LINK] with the tracked URL above</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Send from Niharika's Gmail (not Markvise)</li>
        <li><span class="check" onclick="this.textContent=this.textContent=='☐'?'☑':'☐'">☐</span>
            Log sent date in tracker spreadsheet</li>
      </ul>
    </div>
  </div>

</div>

<script>
  // Plain text body for copy — strips HTML tags
  const PLAIN_BODY = {json.dumps(body + chr(10) + chr(10) + "Niharika")};

  function copyText(id, btn) {{
    const text = document.getElementById(id).textContent.trim();
    navigator.clipboard.writeText(text).then(() => {{
      btn.textContent = "Copied!";
      btn.classList.add("copied");
      setTimeout(() => {{ btn.textContent = "Copy"; btn.classList.remove("copied"); }}, 2000);
    }});
  }}

  function copyBody() {{
    navigator.clipboard.writeText(PLAIN_BODY).then(() => {{
      document.getElementById("status-bar").textContent = "✓ Body copied — paste into Gmail now";
      setTimeout(() => {{
        document.getElementById("status-bar").textContent =
          "Review → Copy subject → Copy body → Paste into Gmail → Attach report → Send";
      }}, 3000);
    }});
  }}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_for_store(place_id: str, store: dict):
    store_name   = store.get("name", place_id)
    store_address = store.get("address", "")
    store_rating  = str(store.get("rating", ""))

    print(f"  Generating: {store_name}")

    stats    = load_stats(place_id)
    analysis = load_analyse_output(place_id)

    # Merge rating/address from store record if not in stats
    if not stats.get("rating") and store_rating:
        stats["rating"] = store.get("rating", 0)

    result = generate_email(store_name, place_id, stats, analysis)
    subject = result.get("subject", "")
    body    = result.get("body", "")

    if not subject or not body:
        print(f"    SKIPPED — empty generation output")
        return None

    # Create or retrieve tracking token
    contact_email = get_store_email(place_id)
    from tracking import create_token, get_pixel_url, get_report_url

    # Determine report filename
    report_filename = f"{safe_name(store_name)}.html"
    token       = create_token(place_id, store_name, contact_email, report_filename)
    pixel_url   = get_pixel_url(token)
    report_url  = get_report_url(token)

    # Build and save HTML preview
    html = build_preview_html(
        store_name    = store_name,
        place_id      = place_id,
        subject       = subject,
        body          = body,
        pixel_url     = pixel_url,
        report_url    = report_url,
        store_address = store_address,
        store_rating  = store_rating,
        contact_email = contact_email,
    )

    out_dir = paths.outreach()
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{safe_name(store_name)}_preview.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"    Saved: {out_path}")
    return out_path


def run(only_place_id: str = None):
    stores = load_tier_a_b_stores()

    if not stores:
        print("No TIER_A/B stores found. Run discover_all.py and local_ownership_scorer.py first.")
        return

    if only_place_id:
        stores = [s for s in stores if s.get("place_id") == only_place_id]
        if not stores:
            print(f"Place ID {only_place_id} not found in TIER_A/B stores.")
            return

    print(f"\nGenerating emails for {len(stores)} store(s)...\n")
    generated = []
    for store in stores:
        pid = store.get("place_id", "")
        if not pid:
            continue
        result = generate_for_store(pid, store)
        if result:
            generated.append(result)

    print(f"\n{'='*50}")
    print(f"DONE — {len(generated)} email previews generated")
    print(f"Output: {paths.outreach()}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--place_id", default=None, help="Generate for one store only")
    args = parser.parse_args()
    run(only_place_id=args.place_id)
