"""
report_html.py - Generates Stage 2 personalised HTML reports.

Reads city/industry from config.yaml via config_loader.
To run for a different city or industry, change config.yaml only.

Reads:
  {slug}_configs.json    - scores, tone, lead finding, alert/strength
  {slug}_stats.json      - rating, review counts, response rate
  {slug}_ratios.json     - revenue leakage (richer than configs)
  benchmarks/            - market averages

Writes:
  data/{city}/{industry}/latest/reports/stage2/{safe_name}.html

Usage:
    python report_html.py
    python report_html.py --place-id ChIJ...   (single business)

After running:
  1. Open any HTML file in your browser to preview
  2. Call POST /tokens/create on Render before sending Email 2
     to get a unique tracked URL per business
"""

import argparse
import json
import os
import re
from datetime import datetime

from config_loader import load

RENDER_BASE_URL  = os.getenv("RENDER_WEBHOOK_URL", "https://go.markvise.com")
CALENDLY_URL     = os.getenv("CALENDLY_URL", "https://calendly.com/founder-markvise/markvise-discovery-call")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return default or {}
    return default or {}


def safe_filename(name):
    return re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")


DIM_LABELS = {
    "overall":            "Overall score",
    "clinical_quality":   "Care quality",
    "appointment_access": "Appointment access",
    "wait_time":          "Wait times",
    "pricing_trust":      "Pricing clarity",
    "communication":      "Communication",
    "after_hours":        "After-hours",
}


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_report_html(name, place_id, config, stats, ratios, benchmarks, base_url=None, ranking=None, all_stats=None):
    # Build tracked CTA URL — logs click before redirecting to Calendly
    _base   = base_url or RENDER_BASE_URL
    cta_url = f"{_base}/go/{place_id}/cta?dest={CALENDLY_URL}"

    cfg          = config.get("config", {})
    dim_scores   = config.get("dimension_scores", {})
    lead_finding = config.get("lead_finding", "")
    alert        = cfg.get("alertHeadline", "")
    strength     = cfg.get("strengthHeadline", "")

    # Stats
    marketing     = stats.get("marketing", {})
    rating        = round(stats.get("avg_rating", 0), 1)
    total_reviews = stats.get("total_reviews", 0)
    neg_rate      = round(stats.get("negative_rate", 0) * 100, 1)

    # Price signals
    price_sig      = stats.get("price_signals", {})
    price_neg      = price_sig.get("price_mentions_negative", 0)
    price_total_m  = price_sig.get("price_mentions_total", 0)
    price_neg_rate = round(price_sig.get("negative_price_rate", 0) * 100, 1)
    price_summary  = price_sig.get("summary", "")
    price_dollars  = price_sig.get("dollar_amounts", [])
    MARKET_PRICE_NEG_RATE = 12  # 12% market average

    # monthly_rev defined early — needed for cluster rank calculation
    monthly_rev   = round(marketing.get("avg_monthly_reviews", 0), 1)

    # Cluster comparison data
    cluster_name   = stats.get("cluster_name", "")
    cluster_id     = stats.get("cluster_id", "")
    cluster_bench  = stats.get("cluster_benchmarks", {})
    cl_rating      = round(cluster_bench.get("avg_rating", 0), 2)
    cl_neg         = round(cluster_bench.get("avg_negative_rate", 0) * 100, 1)
    cl_pace        = round(cluster_bench.get("avg_monthly_reviews", 0), 1)
    cl_count       = cluster_bench.get("count", 0)

    # Cluster rank by review pace
    cl_rank     = None
    cl_rank_of  = cl_count
    if cluster_id and all_stats:
        cluster_paces = sorted(
            [s.get("marketing", {}).get("avg_monthly_reviews", 0)
             for pid, s in all_stats.items()
             if s.get("cluster_id") == cluster_id],
            reverse=True
        )
        if cluster_paces:
            cl_rank = sum(1 for p in cluster_paces if p > monthly_rev) + 1

    # Your Move
    your_move = config.get("your_move", "")

    # Ranking data
    ranking       = ranking or {}
    rank_position = ranking.get("position")        # e.g. 7
    rank_total    = ranking.get("total_in_search")  # e.g. 23
    rank_query    = ranking.get("query", "")
    mkt_monthly   = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)

    # Calculate months until competitor crosses 400-review threshold
    _comp_reviews = ranking.get("competitor_reviews", 0)
    _comp_monthly = ranking.get("competitor_monthly_pace", 0)
    _months_to_400 = None
    if _comp_reviews and _comp_monthly and _comp_reviews < 400:
        _months_to_400 = round((400 - _comp_reviews) / max(_comp_monthly, 0.1))

    # Benchmarks
    mkt_neg     = round(benchmarks.get("avg_negative_rate", 0.15) * 100, 1)
    # Fix blank "market average of ." in lead_finding
    import re as _re
    lead_finding = _re.sub(r'(\d+)\.0(%)', r'\1\2', lead_finding)
    lead_finding = _re.sub(r'market average of\s*\.', f'market average of {mkt_neg}%', lead_finding)
    lead_finding = _re.sub(r'market average of\s*,', f'market average of {mkt_neg}%,', lead_finding)
    mkt_monthly = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)

    # Revenue leakage — config first (most current), ratios only when config shows $0
    config_leakage = config.get("revenue_leakage", {}) or config.get("revenue_impact", {})
    ratios_leakage = ratios.get("revenue_leakage", {})
    config_low = config_leakage.get("total_recoverable", {}).get("low", 0)
    ratios_low = ratios_leakage.get("total_recoverable", {}).get("low", 0)
    leakage = config_leakage if config_low > 0 else (ratios_leakage if ratios_low > 0 else config_leakage)

    total_rec   = leakage.get("total_recoverable", {})
    rev_low     = total_rec.get("low", 0)
    rev_high    = total_rec.get("high", 0)
    quick       = leakage.get("quick_wins_90_days", {})
    quick_low   = quick.get("low", 0)
    quick_high  = quick.get("high", 0)
    quick_note  = quick.get("note", "")

    # Leakage breakdown
    churn_low    = leakage.get("new_patient_churn", {}).get("recoverable_low", 0)
    churn_high   = leakage.get("new_patient_churn", {}).get("recoverable_high", 0)
    sched_low    = leakage.get("scheduling_waste", {}).get("recoverable_low", 0)
    sched_high   = leakage.get("scheduling_waste", {}).get("recoverable_high", 0)
    procure_low  = leakage.get("procurement_gap", {}).get("recoverable_low", 0)
    procure_note = leakage.get("procurement_gap", {}).get("note", "")

    # Suppress Stage B line items for Stage A reports
    is_stage_a = stats.get("data_source") == "google_places_lite"
    if is_stage_a:
        sched_low = sched_high = procure_low = 0
        procure_note = ""

    # Velocity opportunity and competitive displacement (strong practices)
    # Always read velocity/displacement from config — ratios.json may not have them
    vel_opp      = config_leakage.get("velocity_opportunity", {})
    vel_opp_low  = vel_opp.get("low", 0)
    vel_opp_high = vel_opp.get("high", 0)
    vel_gap      = vel_opp.get("vel_gap", 0)
    cl_pace_vo   = vel_opp.get("cl_pace", 0)
    comp_disp    = config_leakage.get("competitive_displacement", {})
    disp_pct     = comp_disp.get("pct", 0)
    disp_top     = comp_disp.get("top_pace", 0)
    disp_yours   = comp_disp.get("your_pace", 0)

    # Ratios signals
    staff_lev = ratios.get("staff_leverage", {})

    # Overall score
    overall = dim_scores.get("overall", 0)

    # Colors
    neg_color  = "#d85a30" if neg_rate > mkt_neg  else "#1d9e75"
    rev_color  = "#d85a30" if rev_low > 0          else "#1d9e75"

    # Dimension bars
    priority_dims = ["clinical_quality", "appointment_access", "communication", "pricing_trust"]
    dim_bars = ""
    for dim in priority_dims:
        score = dim_scores.get(dim, 0)
        if not score or score == 0:
            continue  # skip dimensions with no data
        label = DIM_LABELS.get(dim, dim)
        color = "#1d9e75" if score >= 7.0 else "#d85a30"
        width = round((score / 10) * 100)
        dim_bars += f"""
      <div style="display:grid;grid-template-columns:130px 1fr 44px;gap:10px;align-items:center;margin-bottom:9px">
        <div style="font-size:12px;color:var(--t2)">{label}</div>
        <div style="background:var(--bg3);border-radius:3px;height:6px;overflow:hidden">
          <div style="width:{width}%;height:100%;background:{color};border-radius:3px"></div>
        </div>
        <div style="font-size:12px;font-weight:500;color:{color};text-align:right">{score}/10</div>
      </div>"""

    # Leakage breakdown rows
    leakage_rows = ""
    if churn_low:
        leakage_rows += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--bd);font-size:13px">
        <span style="color:var(--t2)">New client churn</span>
        <span style="font-weight:500">${churn_low:,} - ${churn_high:,}</span></div>"""
    if sched_low:
        leakage_rows += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--bd);font-size:13px">
        <span style="color:var(--t2)">Scheduling waste</span>
        <span style="font-weight:500">${sched_low:,} - ${sched_high:,}</span></div>"""
    if procure_low:
        leakage_rows += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--bd);font-size:13px">
        <span style="color:var(--t2)">Procurement gap</span>
        <span style="font-weight:500">${procure_low:,}+ <span style="font-size:11px;color:var(--t3)">GPO available</span></span></div>"""

    run_date = datetime.now().strftime("%B %Y")

    # Cluster rank cell
    if cl_rank and cl_rank_of:
        cl_rank_color = "#1d9e75" if cl_rank <= cl_rank_of // 3 else "#d85a30" if cl_rank > cl_rank_of * 2 // 3 else "var(--t)"
        cl_rank_val   = f"#{cl_rank} of {cl_rank_of}"
        cl_rank_label = "Cluster rank"
        cl_rank_sub   = f"Review pace · {cluster_name.split('/')[0].strip()[:15]}"
    else:
        cl_rank_color = "var(--t)"
        cl_rank_val   = f"{cl_pace}/mo" if cl_pace else "N/A"
        cl_rank_label = "Cluster avg pace"
        cl_rank_sub   = cluster_name[:25] if cluster_name else "Local cluster"

    stats_cells = ""
    for v, l, m, c in [
        (f"★ {rating}", "Google rating", f"{total_reviews:,} total reviews", "#085041"),
        (cl_rank_val, cl_rank_label, cl_rank_sub, cl_rank_color),
        (f"{monthly_rev}", "Reviews / month", f"Market avg: {mkt_monthly}", "var(--t)"),
        (f"{overall}/10", "Markvise score", "Overall index", "#085041"),
    ]:
        stats_cells += f"""<div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:12px 14px">
      <div style="font-size:22px;font-weight:500;line-height:1;margin-bottom:4px;color:{c}">{v}</div>
      <div style="font-size:11px;color:var(--t3)">{l}</div>
      <div style="font-size:11px;color:var(--t2);margin-top:2px">{m}</div>
    </div>"""

    # ── Ranking Consequence Card HTML ────────────────────────────────────────
    if rank_position and rank_total:
        rank_pct = round(rank_position / rank_total * 100)
        top3_note = "The top 3 positions capture 68% of all new patient search clicks in your area."
        if rank_position <= 3:
            rank_color  = "#065F46"
            rank_border = "#10B981"
            rank_icon   = "\u2713"
            rank_headline = f"You rank #{rank_position} of {rank_total} practices — strong visibility"
            rank_detail = f"{top3_note} You are in that group."
        elif rank_position <= 10:
            rank_color  = "#854F0B"
            rank_border = "#F59E0B"
            rank_icon   = "\u26a0"
            rank_headline = f"You rank #{rank_position} of {rank_total} practices — moderate visibility"
            rank_detail = f"{top3_note} You are outside it."
        else:
            rank_color  = "#8B1A00"
            rank_border = "#EF4444"
            rank_icon   = "\u25bc"
            rank_headline = f"You rank #{rank_position} of {rank_total} practices — low visibility"
            rank_detail = f"{top3_note} You are well outside it."

        velocity_line = ""
        if _months_to_400 and _months_to_400 <= 18:
            velocity_line = f"<div style=\'font-size:12px;color:#8B1A00;margin-top:6px;font-weight:500\'>Your nearest competitor crosses the 400-review threshold — where Google consistently ranks them above you — in approximately {_months_to_400} months.</div>"
        elif monthly_rev < mkt_monthly * 0.5:
            velocity_line = f"<div style=\'font-size:12px;color:#854F0B;margin-top:6px\'>Your review pace ({monthly_rev}/month) is less than half the metro average ({mkt_monthly}/month). Ranking gap will widen without intervention.</div>"

        ranking_card_html = (
            f'''<div class="card" style="border-left:4px solid {rank_border};margin-bottom:16px">\n'''
            f'''  <div style="font-size:11px;font-weight:700;color:{rank_color};text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">{rank_icon} Google Maps Visibility</div>\n'''
            f'''  <div style="font-size:14px;font-weight:600;color:var(--t1);margin-bottom:4px">{rank_headline}</div>\n'''
            f'''  <div style="font-size:12px;color:var(--t2)">{rank_detail}</div>\n'''
            f'''{velocity_line}\n'''
            '''</div>'''
        )
    else:
        ranking_card_html = ""

    # ── Price Signal Card HTML ─────────────────────────────────────────────
    if price_sig and price_total_m > 0 and not is_stage_a:
        price_color = "#B45309" if price_neg_rate > MARKET_PRICE_NEG_RATE else "#065F46"
        price_border = "#F59E0B" if price_neg_rate > MARKET_PRICE_NEG_RATE else "#10B981"
        price_icon = "⚠" if price_neg_rate > MARKET_PRICE_NEG_RATE else "✓"
        dollar_str = ""
        if price_dollars:
            dollar_str = f"<div style='font-size:12px;color:var(--t3);margin-top:6px'>Prices mentioned in reviews: {', '.join(['$'+str(d) for d in price_dollars[:6]])}</div>"
        price_card_html = f"""<div class="card" style="border-left:4px solid {price_border};margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;color:{price_color};text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">{price_icon} Pricing Signals</div>
    <div style="font-size:14px;font-weight:600;color:var(--t1);margin-bottom:4px">{price_summary}</div>
    <div style="font-size:12px;color:var(--t2)">Market average: {MARKET_PRICE_NEG_RATE}% of reviews mention price negatively. King County exam fee benchmark: $58–$95.</div>
    {dollar_str}
  </div>"""
    else:
        price_card_html = ""

    # ── Your Move Card HTML ────────────────────────────────────────────────
    if your_move:
        your_move_card_html = f"""<div class="card" style="border-left:4px solid #7C3AED;background:#FAF5FF;margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;color:#7C3AED;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">★ Your Highest-Impact Move</div>
    <div style="font-size:14px;color:var(--t1);line-height:1.5">{your_move}</div>
    <div style="font-size:12px;color:var(--t3);margin-top:8px">See the full 90-day competitive playbook in your Discovery Brief.</div>
  </div>"""
    else:
        your_move_card_html = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} - Market Snapshot</title>
<style>
:root{{--bg:#fff;--bg2:#f8f7f5;--bg3:#f1efe8;--t:#1a1a19;--t2:#6b6a64;--t3:#9c9a92;--bd:#e0ded8;--cor:#712b13;--cor-bd:#d85a30;--teal-bd:#1d9e75;--amb-bd:#ba7517}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--bg2:#252523;--bg3:#2c2c2a;--t:#e8e6e0;--t2:#9c9a92;--t3:#6b6a64;--bd:#3a3a38;--cor:#f5c4b3;--cor-bd:#d85a30;--teal-bd:#1d9e75;--amb-bd:#ba7517}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--t);padding:28px 20px;line-height:1.5}}
.wrap{{max-width:600px;margin:0 auto}}
.card{{border-left:3px solid var(--bd);padding:14px 16px;margin-bottom:10px;border-radius:0 8px 8px 0;background:var(--bg2)}}
.card.cor{{border-color:var(--cor-bd)}}
.card.teal{{border-color:var(--teal-bd)}}
.card.amb{{border-color:var(--amb-bd)}}
.clabel{{font-size:10px;font-weight:500;color:var(--t3);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}}
.sec{{font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--t3);margin:24px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--bd)}}
.cta{{margin-top:28px;background:var(--bg3);border:1px solid var(--bd);border-radius:10px;padding:22px}}
.btn{{display:inline-block;background:var(--t);color:var(--bg);font-size:13px;font-weight:500;padding:10px 22px;border-radius:6px;text-decoration:none}}
</style>
</head>
<body>
<div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <span style="font-size:12px;font-weight:600;letter-spacing:.05em;color:var(--t3)">markvise</span>
    <span style="font-size:12px;color:var(--t3)">{run_date}</span>
  </div>
  <h1 style="font-size:22px;font-weight:500;margin-bottom:4px">{name}</h1>
  <div style="font-size:14px;color:var(--t2);margin-bottom:24px">{strength}</div>

  <div class="sec">Your market signals</div>
  <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:10px">
    {stats_cells}
  </div>

  <div class="sec">What the data shows</div>
  <div class="card {'cor' if neg_rate > mkt_neg else 'amb'}">
    <div class="clabel">Key finding</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:8px;line-height:1.5">{lead_finding}</div>
    <div style="font-size:13px;font-weight:500;color:var(--t);padding-top:8px;border-top:1px solid var(--bd)">→ {alert}</div>
  </div>
  {'<div class="card teal"><div class="clabel">What is working</div><div style="font-size:13px;color:var(--t2)">' + strength + '</div></div>' if strength else ''}

  {f'''<div class="sec">Revenue opportunity</div>
  <div class="card cor">
    <div class="clabel">Annual upside — reputation gap</div>
    <div style="font-size:28px;font-weight:500;line-height:1;margin-bottom:8px;color:var(--cor)">${rev_low:,} - ${rev_high:,}</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:12px">Closing your reputation gap vs market benchmarks represents an estimated ${rev_low:,}–${rev_high:,} in annual revenue at King County LTV. This is recoverable upside — revenue your practice is positioned to capture with the right moves. Actual figures require your internal data.</div>
    {leakage_rows}
    <!-- 90-day quick wins removed from Stage 2 — included in full Decision Brief only -->
  </div>''' if rev_low > 0 else ''}

  {f'''<div class="sec">How you compare — {cluster_name} ({cl_count} practices)</div>
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:16px;overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr>
          <th style="text-align:left;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">Signal</th>
          <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">You</th>
          <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">{cluster_name}</th>
          <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">Seattle</th>
        </tr>
      </thead>
      <tbody>
        <tr style="border-bottom:1px solid var(--bd)">
          <td style="padding:8px;color:var(--t2)">Google rating</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#1d9e75" if rating >= cl_rating else "#d85a30"}">★ {rating}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">★ {cl_rating}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">★ {round(benchmarks.get("avg_rating",4.5),1)}</td>
        </tr>
        <tr style="border-bottom:1px solid var(--bd)">
          <td style="padding:8px;color:var(--t2)">Est. negative rate</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#d85a30" if neg_rate > cl_neg else "#1d9e75"}">{neg_rate}%</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{cl_neg}%</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{mkt_neg}%</td>
        </tr>
        <tr>
          <td style="padding:8px;color:var(--t2)">Reviews / month</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#1d9e75" if monthly_rev >= cl_pace else "#d85a30"}">{monthly_rev}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{cl_pace}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{mkt_monthly}</td>
        </tr>
      </tbody>
    </table>
    <div style="font-size:10px;color:var(--t3);margin-top:10px">Estimated from Google Places data. Full competitive analysis available in your Discovery Brief.</div>
  </div>''' if cl_count > 0 else f'''<div class="sec">Dimension breakdown</div>
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:16px">
    {dim_bars}
  </div>'''}

  {(f'''<div class="sec">Staff signal</div>''') if not is_stage_a else ""}
report_html.py - Generates Stage 2 personalised HTML reports.

Reads city/industry from config.yaml via config_loader.
To run for a different city or industry, change config.yaml only.

Reads:
  {slug}_configs.json    - scores, tone, lead finding, alert/strength
  {slug}_stats.json      - rating, review counts, response rate
  {slug}_ratios.json     - revenue leakage (richer than configs)
  benchmarks/            - market averages

Writes:
  data/{city}/{industry}/latest/reports/stage2/{safe_name}.html

Usage:
    python report_html.py
    python report_html.py --place-id ChIJ...   (single business)

After running:
  1. Open any HTML file in your browser to preview
  2. Call POST /tokens/create on Render before sending Email 2
     to get a unique tracked URL per business
"""

import argparse
import json
import os
import re
from datetime import datetime

from config_loader import load

RENDER_BASE_URL  = os.getenv("RENDER_WEBHOOK_URL", "https://go.markvise.com")
CALENDLY_URL     = os.getenv("CALENDLY_URL", "https://calendly.com/founder-markvise/markvise-discovery-call")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return default or {}
    return default or {}


def safe_filename(name):
    return re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")


DIM_LABELS = {
    "overall":            "Overall score",
    "clinical_quality":   "Care quality",
    "appointment_access": "Appointment access",
    "wait_time":          "Wait times",
    "pricing_trust":      "Pricing clarity",
    "communication":      "Communication",
    "after_hours":        "After-hours",
}


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_report_html(name, place_id, config, stats, ratios, benchmarks, base_url=None, ranking=None, all_stats=None):
    # Build tracked CTA URL — logs click before redirecting to Calendly
    _base   = base_url or RENDER_BASE_URL
    cta_url = f"{_base}/go/{place_id}/cta?dest={CALENDLY_URL}"

    cfg          = config.get("config", {})
    dim_scores   = config.get("dimension_scores", {})
    lead_finding = config.get("lead_finding", "")
    alert        = cfg.get("alertHeadline", "")
    strength     = cfg.get("strengthHeadline", "")

    # Stats
    marketing     = stats.get("marketing", {})
    rating        = round(stats.get("avg_rating", 0), 1)
    total_reviews = stats.get("total_reviews", 0)
    neg_rate      = round(stats.get("negative_rate", 0) * 100, 1)

    # Price signals
    price_sig      = stats.get("price_signals", {})
    price_neg      = price_sig.get("price_mentions_negative", 0)
    price_total_m  = price_sig.get("price_mentions_total", 0)
    price_neg_rate = round(price_sig.get("negative_price_rate", 0) * 100, 1)
    price_summary  = price_sig.get("summary", "")
    price_dollars  = price_sig.get("dollar_amounts", [])
    MARKET_PRICE_NEG_RATE = 12  # 12% market average

    # monthly_rev defined early — needed for cluster rank calculation
    monthly_rev   = round(marketing.get("avg_monthly_reviews", 0), 1)

    # Cluster comparison data
    cluster_name   = stats.get("cluster_name", "")
    cluster_id     = stats.get("cluster_id", "")
    cluster_bench  = stats.get("cluster_benchmarks", {})
    cl_rating      = round(cluster_bench.get("avg_rating", 0), 2)
    cl_neg         = round(cluster_bench.get("avg_negative_rate", 0) * 100, 1)
    cl_pace        = round(cluster_bench.get("avg_monthly_reviews", 0), 1)
    cl_count       = cluster_bench.get("count", 0)

    # Cluster rank by review pace
    cl_rank     = None
    cl_rank_of  = cl_count
    if cluster_id and all_stats:
        cluster_paces = sorted(
            [s.get("marketing", {}).get("avg_monthly_reviews", 0)
             for pid, s in all_stats.items()
             if s.get("cluster_id") == cluster_id],
            reverse=True
        )
        if cluster_paces:
            cl_rank = sum(1 for p in cluster_paces if p > monthly_rev) + 1

    # Your Move
    your_move = config.get("your_move", "")

    # Ranking data
    ranking       = ranking or {}
    rank_position = ranking.get("position")        # e.g. 7
    rank_total    = ranking.get("total_in_search")  # e.g. 23
    rank_query    = ranking.get("query", "")
    mkt_monthly   = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)

    # Calculate months until competitor crosses 400-review threshold
    _comp_reviews = ranking.get("competitor_reviews", 0)
    _comp_monthly = ranking.get("competitor_monthly_pace", 0)
    _months_to_400 = None
    if _comp_reviews and _comp_monthly and _comp_reviews < 400:
        _months_to_400 = round((400 - _comp_reviews) / max(_comp_monthly, 0.1))

    # Benchmarks
    mkt_neg     = round(benchmarks.get("avg_negative_rate", 0.15) * 100, 1)
    # Fix blank "market average of ." in lead_finding
    import re as _re
    lead_finding = _re.sub(r'(\d+)\.0(%)', r'\1\2', lead_finding)
    lead_finding = _re.sub(r'market average of\s*\.', f'market average of {mkt_neg}%', lead_finding)
    lead_finding = _re.sub(r'market average of\s*,', f'market average of {mkt_neg}%,', lead_finding)
    mkt_monthly = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)

    # Revenue leakage — config first (most current), ratios only when config shows $0
    config_leakage = config.get("revenue_leakage", {}) or config.get("revenue_impact", {})
    ratios_leakage = ratios.get("revenue_leakage", {})
    config_low = config_leakage.get("total_recoverable", {}).get("low", 0)
    ratios_low = ratios_leakage.get("total_recoverable", {}).get("low", 0)
    leakage = config_leakage if config_low > 0 else (ratios_leakage if ratios_low > 0 else config_leakage)

    total_rec   = leakage.get("total_recoverable", {})
    rev_low     = total_rec.get("low", 0)
    rev_high    = total_rec.get("high", 0)
    quick       = leakage.get("quick_wins_90_days", {})
    quick_low   = quick.get("low", 0)
    quick_high  = quick.get("high", 0)
    quick_note  = quick.get("note", "")

    # Leakage breakdown
    churn_low    = leakage.get("new_patient_churn", {}).get("recoverable_low", 0)
    churn_high   = leakage.get("new_patient_churn", {}).get("recoverable_high", 0)
    sched_low    = leakage.get("scheduling_waste", {}).get("recoverable_low", 0)
    sched_high   = leakage.get("scheduling_waste", {}).get("recoverable_high", 0)
    procure_low  = leakage.get("procurement_gap", {}).get("recoverable_low", 0)
    procure_note = leakage.get("procurement_gap", {}).get("note", "")

    # Suppress Stage B line items for Stage A reports
    is_stage_a = stats.get("data_source") == "google_places_lite"
    if is_stage_a:
        sched_low = sched_high = procure_low = 0
        procure_note = ""

    # Velocity opportunity and competitive displacement (strong practices)
    # Always read velocity/displacement from config — ratios.json may not have them
    vel_opp      = config_leakage.get("velocity_opportunity", {})
    vel_opp_low  = vel_opp.get("low", 0)
    vel_opp_high = vel_opp.get("high", 0)
    vel_gap      = vel_opp.get("vel_gap", 0)
    cl_pace_vo   = vel_opp.get("cl_pace", 0)
    comp_disp    = config_leakage.get("competitive_displacement", {})
    disp_pct     = comp_disp.get("pct", 0)
    disp_top     = comp_disp.get("top_pace", 0)
    disp_yours   = comp_disp.get("your_pace", 0)

    # Ratios signals
    staff_lev = ratios.get("staff_leverage", {})

    # Overall score
    overall = dim_scores.get("overall", 0)

    # Colors
    neg_color  = "#d85a30" if neg_rate > mkt_neg  else "#1d9e75"
    rev_color  = "#d85a30" if rev_low > 0          else "#1d9e75"

    # Dimension bars
    priority_dims = ["clinical_quality", "appointment_access", "communication", "pricing_trust"]
    dim_bars = ""
    for dim in priority_dims:
        score = dim_scores.get(dim, 0)
        if not score or score == 0:
            continue  # skip dimensions with no data
        label = DIM_LABELS.get(dim, dim)
        color = "#1d9e75" if score >= 7.0 else "#d85a30"
        width = round((score / 10) * 100)
        dim_bars += f"""
      <div style="display:grid;grid-template-columns:130px 1fr 44px;gap:10px;align-items:center;margin-bottom:9px">
        <div style="font-size:12px;color:var(--t2)">{label}</div>
        <div style="background:var(--bg3);border-radius:3px;height:6px;overflow:hidden">
          <div style="width:{width}%;height:100%;background:{color};border-radius:3px"></div>
        </div>
        <div style="font-size:12px;font-weight:500;color:{color};text-align:right">{score}/10</div>
      </div>"""

    # Leakage breakdown rows
    leakage_rows = ""
    if churn_low:
        leakage_rows += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--bd);font-size:13px">
        <span style="color:var(--t2)">New client churn</span>
        <span style="font-weight:500">${churn_low:,} - ${churn_high:,}</span></div>"""
    if sched_low:
        leakage_rows += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--bd);font-size:13px">
        <span style="color:var(--t2)">Scheduling waste</span>
        <span style="font-weight:500">${sched_low:,} - ${sched_high:,}</span></div>"""
    if procure_low:
        leakage_rows += f"""<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--bd);font-size:13px">
        <span style="color:var(--t2)">Procurement gap</span>
        <span style="font-weight:500">${procure_low:,}+ <span style="font-size:11px;color:var(--t3)">GPO available</span></span></div>"""

    run_date = datetime.now().strftime("%B %Y")

    # Cluster rank cell
    if cl_rank and cl_rank_of:
        cl_rank_color = "#1d9e75" if cl_rank <= cl_rank_of // 3 else "#d85a30" if cl_rank > cl_rank_of * 2 // 3 else "var(--t)"
        cl_rank_val   = f"#{cl_rank} of {cl_rank_of}"
        cl_rank_label = "Cluster rank"
        cl_rank_sub   = f"Review pace · {cluster_name.split('/')[0].strip()[:15]}"
    else:
        cl_rank_color = "var(--t)"
        cl_rank_val   = f"{cl_pace}/mo" if cl_pace else "N/A"
        cl_rank_label = "Cluster avg pace"
        cl_rank_sub   = cluster_name[:25] if cluster_name else "Local cluster"

    stats_cells = ""
    for v, l, m, c in [
        (f"★ {rating}", "Google rating", f"{total_reviews:,} total reviews", "#085041"),
        (cl_rank_val, cl_rank_label, cl_rank_sub, cl_rank_color),
        (f"{monthly_rev}", "Reviews / month", f"Market avg: {mkt_monthly}", "var(--t)"),
        (f"{overall}/10", "Markvise score", "Overall index", "#085041"),
    ]:
        stats_cells += f"""<div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:12px 14px">
      <div style="font-size:22px;font-weight:500;line-height:1;margin-bottom:4px;color:{c}">{v}</div>
      <div style="font-size:11px;color:var(--t3)">{l}</div>
      <div style="font-size:11px;color:var(--t2);margin-top:2px">{m}</div>
    </div>"""

    # ── Ranking Consequence Card HTML ────────────────────────────────────────
    if rank_position and rank_total:
        rank_pct = round(rank_position / rank_total * 100)
        top3_note = "The top 3 positions capture 68% of all new patient search clicks in your area."
        if rank_position <= 3:
            rank_color  = "#065F46"
            rank_border = "#10B981"
            rank_icon   = "\u2713"
            rank_headline = f"You rank #{rank_position} of {rank_total} practices — strong visibility"
            rank_detail = f"{top3_note} You are in that group."
        elif rank_position <= 10:
            rank_color  = "#854F0B"
            rank_border = "#F59E0B"
            rank_icon   = "\u26a0"
            rank_headline = f"You rank #{rank_position} of {rank_total} practices — moderate visibility"
            rank_detail = f"{top3_note} You are outside it."
        else:
            rank_color  = "#8B1A00"
            rank_border = "#EF4444"
            rank_icon   = "\u25bc"
            rank_headline = f"You rank #{rank_position} of {rank_total} practices — low visibility"
            rank_detail = f"{top3_note} You are well outside it."

        velocity_line = ""
        if _months_to_400 and _months_to_400 <= 18:
            velocity_line = f"<div style=\'font-size:12px;color:#8B1A00;margin-top:6px;font-weight:500\'>Your nearest competitor crosses the 400-review threshold — where Google consistently ranks them above you — in approximately {_months_to_400} months.</div>"
        elif monthly_rev < mkt_monthly * 0.5:
            velocity_line = f"<div style=\'font-size:12px;color:#854F0B;margin-top:6px\'>Your review pace ({monthly_rev}/month) is less than half the metro average ({mkt_monthly}/month). Ranking gap will widen without intervention.</div>"

        ranking_card_html = (
            f'''<div class="card" style="border-left:4px solid {rank_border};margin-bottom:16px">\n'''
            f'''  <div style="font-size:11px;font-weight:700;color:{rank_color};text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">{rank_icon} Google Maps Visibility</div>\n'''
            f'''  <div style="font-size:14px;font-weight:600;color:var(--t1);margin-bottom:4px">{rank_headline}</div>\n'''
            f'''  <div style="font-size:12px;color:var(--t2)">{rank_detail}</div>\n'''
            f'''{velocity_line}\n'''
            '''</div>'''
        )
    else:
        ranking_card_html = ""

    # ── Velocity Opportunity Card (Option 1) ─────────────────────────────
    if vel_opp_low > 0 and is_stage_a:
        velocity_opp_card_html = f"""<div class="sec">Revenue opportunity</div>
  <div class="card" style="border-left:4px solid #1d9e75;background:#F0FDF4;margin-bottom:10px">
    <div class="clabel">Velocity gap upside</div>
    <div style="font-size:28px;font-weight:500;line-height:1;margin-bottom:8px;color:#065F46">${vel_opp_low:,} - ${vel_opp_high:,}</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:8px;line-height:1.5">Your practice generates {disp_yours}/mo in new reviews vs the {cluster_name} cluster average of {cl_pace_vo}/mo. Closing that {vel_gap}/mo gap — at industry-standard 15% review-to-client conversion and King County LTV — represents an estimated ${vel_opp_low:,}–${vel_opp_high:,} in incremental annual revenue.</div>
    <div style="font-size:12px;color:#065F46;font-weight:500">Based on public review velocity data. Actual conversion depends on your intake capacity.</div>
  </div>"""
    else:
        velocity_opp_card_html = ""

    # ── Competitive Displacement Card (Option 2) ──────────────────────────
    if disp_pct > 0 and is_stage_a:
        competitive_disp_card_html = f"""<div class="card" style="border-left:4px solid #F59E0B;background:#FFFBEB;margin-bottom:16px">
    <div class="clabel">Competitive exposure</div>
    <div style="font-size:22px;font-weight:500;line-height:1;margin-bottom:6px;color:#92400E">{disp_pct}% more discovery clicks</div>
    <div style="font-size:13px;color:var(--t2);line-height:1.5">The top-performing practices in {cluster_name} generate approximately {disp_top}/mo in new reviews vs your {disp_yours}/mo. Higher review velocity means higher Google Maps visibility — practices above you are capturing an estimated {disp_pct}% more new-client discovery clicks each month.</div>
  </div>"""
    else:
        competitive_disp_card_html = ""

    # ── Price Signal Card HTML ─────────────────────────────────────────────
    if price_sig and price_total_m > 0 and not is_stage_a:
        price_color = "#B45309" if price_neg_rate > MARKET_PRICE_NEG_RATE else "#065F46"
        price_border = "#F59E0B" if price_neg_rate > MARKET_PRICE_NEG_RATE else "#10B981"
        price_icon = "⚠" if price_neg_rate > MARKET_PRICE_NEG_RATE else "✓"
        dollar_str = ""
        if price_dollars:
            dollar_str = f"<div style='font-size:12px;color:var(--t3);margin-top:6px'>Prices mentioned in reviews: {', '.join(['$'+str(d) for d in price_dollars[:6]])}</div>"
        price_card_html = f"""<div class="card" style="border-left:4px solid {price_border};margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;color:{price_color};text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">{price_icon} Pricing Signals</div>
    <div style="font-size:14px;font-weight:600;color:var(--t1);margin-bottom:4px">{price_summary}</div>
    <div style="font-size:12px;color:var(--t2)">Market average: {MARKET_PRICE_NEG_RATE}% of reviews mention price negatively. King County exam fee benchmark: $58–$95.</div>
    {dollar_str}
  </div>"""
    else:
        price_card_html = ""

    # ── Your Move Card HTML ────────────────────────────────────────────────
    if your_move:
        your_move_card_html = f"""<div class="card" style="border-left:4px solid #7C3AED;background:#FAF5FF;margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;color:#7C3AED;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">★ Your Highest-Impact Move</div>
    <div style="font-size:14px;color:var(--t1);line-height:1.5">{your_move}</div>
    <div style="font-size:12px;color:var(--t3);margin-top:8px">See the full 90-day competitive playbook in your Discovery Brief.</div>
  </div>"""
    else:
        your_move_card_html = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} - Market Snapshot</title>
<style>
:root{{--bg:#fff;--bg2:#f8f7f5;--bg3:#f1efe8;--t:#1a1a19;--t2:#6b6a64;--t3:#9c9a92;--bd:#e0ded8;--cor:#712b13;--cor-bd:#d85a30;--teal-bd:#1d9e75;--amb-bd:#ba7517}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--bg2:#252523;--bg3:#2c2c2a;--t:#e8e6e0;--t2:#9c9a92;--t3:#6b6a64;--bd:#3a3a38;--cor:#f5c4b3;--cor-bd:#d85a30;--teal-bd:#1d9e75;--amb-bd:#ba7517}}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--t);padding:28px 20px;line-height:1.5}}
.wrap{{max-width:600px;margin:0 auto}}
.card{{border-left:3px solid var(--bd);padding:14px 16px;margin-bottom:10px;border-radius:0 8px 8px 0;background:var(--bg2)}}
.card.cor{{border-color:var(--cor-bd)}}
.card.teal{{border-color:var(--teal-bd)}}
.card.amb{{border-color:var(--amb-bd)}}
.clabel{{font-size:10px;font-weight:500;color:var(--t3);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}}
.sec{{font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--t3);margin:24px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--bd)}}
.cta{{margin-top:28px;background:var(--bg3);border:1px solid var(--bd);border-radius:10px;padding:22px}}
.btn{{display:inline-block;background:var(--t);color:var(--bg);font-size:13px;font-weight:500;padding:10px 22px;border-radius:6px;text-decoration:none}}
</style>
</head>
<body>
<div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <span style="font-size:12px;font-weight:600;letter-spacing:.05em;color:var(--t3)">markvise</span>
    <span style="font-size:12px;color:var(--t3)">{run_date}</span>
  </div>
  <h1 style="font-size:22px;font-weight:500;margin-bottom:4px">{name}</h1>
  <div style="font-size:14px;color:var(--t2);margin-bottom:24px">{strength}</div>

  <div class="sec">Your market signals</div>
  <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:10px">
    {stats_cells}
  </div>

  <div class="sec">What the data shows</div>
  <div class="card {'cor' if neg_rate > mkt_neg else 'amb'}">
    <div class="clabel">Key finding</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:8px;line-height:1.5">{lead_finding}</div>
    <div style="font-size:13px;font-weight:500;color:var(--t);padding-top:8px;border-top:1px solid var(--bd)">→ {alert}</div>
  </div>
  {'<div class="card teal"><div class="clabel">What is working</div><div style="font-size:13px;color:var(--t2)">' + strength + '</div></div>' if strength else ''}

  {f'''<div class="sec">Revenue opportunity</div>
  <div class="card cor">
    <div class="clabel">Annual upside — reputation gap</div>
    <div style="font-size:28px;font-weight:500;line-height:1;margin-bottom:8px;color:var(--cor)">${rev_low:,} - ${rev_high:,}</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:12px">Closing your reputation gap vs market benchmarks represents an estimated ${rev_low:,}–${rev_high:,} in annual revenue at King County LTV. This is recoverable upside — revenue your practice is positioned to capture with the right moves. Actual figures require your internal data.</div>
    {leakage_rows}
    <!-- 90-day quick wins removed from Stage 2 — included in full Decision Brief only -->
  </div>''' if rev_low > 0 else ''}

  {f'''<div class="sec">How you compare — {cluster_name} ({cl_count} practices)</div>
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:16px;overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead>
        <tr>
          <th style="text-align:left;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">Signal</th>
          <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">You</th>
          <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">{cluster_name}</th>
          <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">Seattle</th>
        </tr>
      </thead>
      <tbody>
        <tr style="border-bottom:1px solid var(--bd)">
          <td style="padding:8px;color:var(--t2)">Google rating</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#1d9e75" if rating >= cl_rating else "#d85a30"}">★ {rating}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">★ {cl_rating}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">★ {round(benchmarks.get("avg_rating",4.5),1)}</td>
        </tr>
        <tr style="border-bottom:1px solid var(--bd)">
          <td style="padding:8px;color:var(--t2)">Est. negative rate</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#d85a30" if neg_rate > cl_neg else "#1d9e75"}">{neg_rate}%</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{cl_neg}%</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{mkt_neg}%</td>
        </tr>
        <tr>
          <td style="padding:8px;color:var(--t2)">Reviews / month</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#1d9e75" if monthly_rev >= cl_pace else "#d85a30"}">{monthly_rev}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{cl_pace}</td>
          <td style="padding:8px;text-align:center;color:var(--t2)">{mkt_monthly}</td>
        </tr>
      </tbody>
    </table>
    <div style="font-size:10px;color:var(--t3);margin-top:10px">Estimated from Google Places data. Full competitive analysis available in your Discovery Brief.</div>
  </div>''' if cl_count > 0 else f'''<div class="sec">Dimension breakdown</div>
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:16px">
    {dim_bars}
  </div>'''}

  {(f'''<div class="sec">Staff signal</div>
  <div class="card {'teal' if staff_lev.get('label') == 'HIGH' else 'amb'}">
    <div class="clabel">Staff leverage - {staff_lev.get("label","")}</div>
    <div style="font-size:13px;color:var(--t2)">{staff_lev.get("insight","")}</div>
  </div>''') if staff_lev.get("insight") and not is_stage_a else ''}

  <!-- Ranking Consequence Card -->
  {ranking_card_html}

  <!-- Velocity Opportunity Card (strong practices) -->
  {velocity_opp_card_html}

  <!-- Competitive Displacement Card (strong practices) -->
  {competitive_disp_card_html}

  <!-- Price Signal Card -->
  {price_card_html}

  <!-- Your Move Card -->
  {your_move_card_html}

  <div class="cta">
    <div style="font-size:16px;font-weight:500;margin-bottom:8px">Your competitors don't have this picture. You will.</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:6px;line-height:1.5">
      30 minutes. Three prioritised moves. Done.
    </div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:14px;font-weight:500">Book a call. Let us help you.</div>
    <a class="btn" href="{cta_url}">Get your 90-day competitive playbook</a>
  </div>

  <div style="font-size:11px;color:var(--t3);margin-top:20px;line-height:1.6">
    This report uses publicly available data: Google reviews, Google Maps, and market benchmarks.
    Markvise does not share your data. Questions: founder@markvise.com
    <br><br>
    Markvise is a Seattle-based market intelligence service built by operations and analytics professionals.
    We track reputation, competitive, labor, and neighborhood signals for independent practices so owners
    see what is changing in their market before it shows up in their numbers.
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(target_place_id=None):
    cfg, paths = load()
    slug = cfg.city

    configs = load_json(f"{slug}_configs.json")
    if not configs:
        print(f"ERROR: {slug}_configs.json not found. Run analyse.py first.")
        return

    stats_raw  = load_json(f"{slug}_stats.json", {})
    all_stats  = stats_raw.get("businesses", stats_raw)

    ratios_raw   = load_json(f"{slug}_ratios.json", {})
    all_ratios   = ratios_raw.get("businesses", {})
    rankings_raw = load_json(f"{slug}_rankings.json", {})
    # rankings.json is keyed by place_id
    all_rankings = rankings_raw if isinstance(rankings_raw, dict) else {}

    benchmarks = {}
    for bp in [
        paths.benchmarks_universal(),
        paths.benchmarks_industry(),
        paths.benchmarks_city(),
    ]:
        if os.path.exists(bp):
            benchmarks.update(load_json(bp))

    out_dir = os.path.join(paths.latest_reports(), "stage2")
    os.makedirs(out_dir, exist_ok=True)

    generated = 0
    skipped   = 0

    for place_id, config in configs.items():
        if target_place_id and place_id != target_place_id:
            continue

        name   = config.get("name", place_id)
        stats  = all_stats.get(place_id, {})
        ratios = all_ratios.get(place_id, {})

        if not stats:
            print(f"  {name} -- no stats, skipping")
            skipped += 1
            continue

        ranking = all_rankings.get(place_id, {})
        try:
            html     = build_report_html(name, place_id, config, stats, ratios, benchmarks, ranking=ranking, all_stats=all_stats)
            filename = f"{safe_filename(name)}.html"
            out_path = os.path.join(out_dir, filename)

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)

            print(f"  {name} --> {out_path}")
            generated += 1

        except Exception as e:
            print(f"  ERROR {name}: {e}")
            import traceback; traceback.print_exc()
            skipped += 1

    print()
    print("=" * 55)
    print("REPORT GENERATION COMPLETE")
    print("=" * 55)
    print(f"City:      {cfg.city}")
    print(f"Industry:  {cfg.industry}")
    print(f"Generated: {generated}")
    print(f"Skipped:   {skipped}")
    print(f"Output:    {out_dir}")
    print()
    print("Next steps:")
    print("  1. Open any HTML file in your browser to preview")
    print("  2. Before sending Email 2, call /tokens/create on Render")
    print(f"     POST https://vetpipeline.onrender.com/tokens/create")
    print(f"     Body: {{place_id, email, business_name, city, report_type:'stage2_personalised'}}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate Stage 2 personalised HTML reports. "
                    "City and industry read from config.yaml."
    )
    parser.add_argument("--place-id", default=None,
                        help="Generate for one business only (optional)")
    args = parser.parse_args()
    run(args.place_id)
