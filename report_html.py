"""
report_html.py — Generates personalised HTML market snapshots for groc pilot.
Reads city/industry from config.yaml. No Markvise branding.
"""

import argparse
import json
import os
import re
from datetime import datetime

from config_loader import load
from dotenv import load_dotenv

load_dotenv()

RENDER_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://grocpilot.onrender.com")

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
    "clinical_quality":   "Product range",
    "appointment_access": "Staff helpfulness",
    "wait_time":          "Freshness & quality",
    "pricing_trust":      "Pricing & value",
    "communication":      "Community connection",
    "after_hours":        "Hours & availability",
}


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

def build_report_html(name, place_id, config, stats, ratios, benchmarks, base_url=None, ranking=None, all_stats=None, reviews=None, metro_avg=None):
    cfg          = config.get("config", {})
    dim_scores   = config.get("dimension_scores", {})
    lead_finding = config.get("lead_finding", "")
    # Replace vet-specific language with grocery equivalents
    for _old, _new in [
        ("practice", "store"), ("clinic", "store"), ("patient", "customer"),
        ("clients", "customers"), ("client", "customer"),
        ("Seattle market average", "PNW market average"),
        ("Seattle metro", "PNW region"),
        ("King County", "PNW"),
    ]:
        lead_finding = lead_finding.replace(_old, _new)
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

    # "What needs attention" — derived from neg rate + alert (needs rating, neg_rate, cl_* vars)
    if neg_rate > cl_neg + 2:
        alert_detail = f"Negative review rate is {neg_rate}% — above the {cluster_name} average of {cl_neg}%. {alert}."
    elif rating < cl_rating - 0.1:
        alert_detail = f"Rating of {rating}\u2605 is below the {cluster_name} average of {cl_rating}\u2605. {alert}."
    elif alert:
        alert_detail = alert
    else:
        alert_detail = ""
    import re as _re2
    alert_detail = _re2.sub(r'\.+', '.', alert_detail).strip()

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
    # Strip vet-calibrated dollar figures — meaningless for grocery
    lead_finding = _re.sub(r',? representing \$[\d,]+[–-]\$[\d,]+[^.]*\.', '.', lead_finding)
    lead_finding = _re.sub(r',? representing \$[\d,]+[–-]\$[\d,]+[^,]*', '', lead_finding)
    mkt_monthly = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)

    # Grocery-calibrated revenue at risk
    # Each negative review represents ~10 silent unhappy customers (iceberg).
    # Average Indian grocery basket: $50.
    # At-risk = customers who had a bad experience and won't return.
    _BASKET           = 50
    _ICEBERG          = 10
    _neg_rev_mo       = round(monthly_rev * (neg_rate / 100), 1)
    _at_risk_mo       = int(_neg_rev_mo * _ICEBERG * _BASKET)
    _at_risk_annual   = _at_risk_mo * 12
    # Only show if meaningful (neg rate above market average and >$500/mo at risk)
    _show_risk_card   = neg_rate > mkt_neg and _at_risk_mo >= 500

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
        (f"{overall}/10", "Snapshot score", "Overall index", "#085041"),
    ]:
        stats_cells += f"""<div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:12px 14px">
      <div style="font-size:22px;font-weight:500;line-height:1;margin-bottom:4px;color:{c}">{v}</div>
      <div style="font-size:11px;color:var(--t3)">{l}</div>
      <div style="font-size:11px;color:var(--t2);margin-top:2px">{m}</div>
    </div>"""

    # ── Ranking Consequence Card HTML ────────────────────────────────────────
    if rank_position and rank_total:
        rank_pct = round(rank_position / rank_total * 100)
        top3_note = "The top 3 positions capture the majority of new customer discovery clicks in your area."
        if rank_position <= 3:
            rank_color  = "#065F46"
            rank_border = "#10B981"
            rank_icon   = "\u2713"
            rank_headline = f"You rank #{rank_position} of {rank_total} stores — strong visibility"
            rank_detail = f"{top3_note} You are in that group."
        elif rank_position <= 10:
            rank_color  = "#854F0B"
            rank_border = "#F59E0B"
            rank_icon   = "\u26a0"
            rank_headline = f"You rank #{rank_position} of {rank_total} stores — moderate visibility"
            rank_detail = f"{top3_note} You are outside it."
        else:
            rank_color  = "#8B1A00"
            rank_border = "#EF4444"
            rank_icon   = "\u25bc"
            rank_headline = f"You rank #{rank_position} of {rank_total} stores — low visibility"
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
    <div style="font-size:12px;color:var(--t2)">Market average: {MARKET_PRICE_NEG_RATE}% of reviews mention pricing negatively.</div>
    {dollar_str}
  </div>"""
    else:
        price_card_html = ""

    # ── Review Quotes + Pattern Card ─────────────────────────────────────
    # Pick the most signal-rich quotes from actual customer reviews
    review_quotes_html = ""
    pattern_card_html = ""
    if reviews:
        EXPIRY_KW   = ["expired", "expiry", "expir", "old stock", "out of date", "mold", "mould", "stale"]
        PRICE_KW    = ["expensive", "overpriced", "overcharge", "extra charge", "extra item", "too much", "price hike", "raised price", "price increase"]
        BILLING_KW  = ["overcharge", "extra item", "wrong bill", "bill", "charged twice", "added to my bill"]
        SERVICE_KW  = ["rude", "unhelpful", "ignored", "dismiss", "attitude", "disrespectful", "mean", "unprofessional", "turned off", "forced me"]
        VARIETY_KW  = ["selection", "variety", "stock", "empty", "missing", "out of stock", "available", "hard to find"]
        FRESH_KW    = ["fresh", "freshness", "produce", "vegetables", "rotten", "wilted"]

        def score_review(r):
            text = (r.get("text") or "").lower()
            rating = r.get("rating", 3)
            score = 0
            for kw_list in [EXPIRY_KW, PRICE_KW, BILLING_KW, SERVICE_KW]:
                if any(kw in text for kw in kw_list):
                    score += 3
            if rating <= 2: score += 2
            if rating >= 4: score -= 1
            if len(text) > 80: score += 1
            return score

        neg_reviews = sorted(
            [r for r in reviews if r.get("rating", 5) <= 3 and len(r.get("text","")) > 40],
            key=score_review, reverse=True
        )
        pos_reviews = [r for r in reviews if r.get("rating", 0) >= 4 and len(r.get("text","")) > 40]

        def clean_quote(text, maxlen=140):
            text = text.replace("\n", " ").strip()
            if len(text) > maxlen:
                text = text[:maxlen].rsplit(" ", 1)[0] + "…"
            return text

        quotes_html = ""
        shown = 0
        if neg_reviews and shown < 1:
            r = neg_reviews[0]
            q = clean_quote(r.get("text", ""))
            stars = "★" * r.get("rating",1) + "☆" * (5 - r.get("rating",1))
            quotes_html += f"""<div style="border-left:3px solid var(--cor-bd);padding:10px 14px;margin-bottom:10px;background:var(--bg2);border-radius:0 6px 6px 0">
      <div style="font-size:11px;color:#d85a30;margin-bottom:4px">{stars}</div>
      <div style="font-size:13px;color:var(--t2);line-height:1.5;font-style:italic">"{q}"</div>
    </div>"""
            shown += 1
        if pos_reviews and shown < 2:
            r = pos_reviews[0]
            q = clean_quote(r.get("text", ""))
            stars = "★" * r.get("rating",5)
            quotes_html += f"""<div style="border-left:3px solid var(--teal-bd);padding:10px 14px;margin-bottom:10px;background:var(--bg2);border-radius:0 6px 6px 0">
      <div style="font-size:11px;color:#1d9e75;margin-bottom:4px">{stars}</div>
      <div style="font-size:13px;color:var(--t2);line-height:1.5;font-style:italic">"{q}"</div>
    </div>"""

        if quotes_html:
            review_quotes_html = f'''<div class="sec">What customers are saying</div>
  {quotes_html}'''

        # Pattern card — rule-based from review signals
        all_text = " ".join((r.get("text") or "").lower() for r in reviews)
        patterns = []
        if any(kw in all_text for kw in EXPIRY_KW):
            patterns.append(("⚠ Freshness & expiry complaints visible", "Multiple reviews mention expired or poor-quality products. This is the fastest path to 1-star reviews — and the easiest to fix operationally."))
        if any(kw in all_text for kw in BILLING_KW):
            patterns.append(("⚠ Billing accuracy flagged", "At least one reviewer reported incorrect items added to their bill. Even isolated incidents erode trust disproportionately."))
        if any(kw in all_text for kw in PRICE_KW):
            patterns.append(("↑ Pricing perception gap", "Customers are comparing your prices explicitly. Perception of fairness matters more than actual price — small signage changes can shift this without touching margins."))
        if any(kw in all_text for kw in SERVICE_KW):
            patterns.append(("⚠ Service experience signal", "Staff interactions are mentioned specifically in reviews. Positive service comments build loyalty; negative ones get shared."))
        if any(kw in all_text for kw in VARIETY_KW):
            patterns.append(("✓ Variety is a draw", "Customers mention product selection as a reason to visit. Stock depth is your competitive advantage — it comes up unprompted."))

        if patterns:
            label, insight = patterns[0]
            pattern_card_html = f'''<div class="card" style="border-left:4px solid #7C3AED;background:#FAF5FF;margin-bottom:16px">
    <div style="font-size:11px;font-weight:700;color:#7C3AED;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">One pattern to watch</div>
    <div style="font-size:14px;font-weight:500;color:var(--t1);margin-bottom:6px">{label}</div>
    <div style="font-size:13px;color:var(--t2);line-height:1.5">{insight}</div>
  </div>'''

    your_move_card_html = ""  # suppressed — replaced by pattern_card_html above

    # Comparison table: show 4 cols (You | Cluster | Metro) only when cluster != metro
    metro_label   = metro_avg.get("label", "Metro") if metro_avg else "Metro"
    show_metro_col = metro_label != cluster_name  # collapse to 3 cols when identical

    # Metro column values
    _m_rating   = round((metro_avg or benchmarks).get("avg_rating", 4.5), 1)
    _m_neg      = round((metro_avg or {}).get("avg_negative_rate", benchmarks.get("avg_negative_rate", 0.10)) * 100, 1)
    _m_monthly  = round((metro_avg or benchmarks).get("avg_monthly_reviews", mkt_monthly), 1)

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
    <span style="font-size:12px;font-weight:600;letter-spacing:.05em;color:var(--t3)">Market Snapshot</span>
    <span style="font-size:12px;color:var(--t3)">{run_date}</span>
  </div>
  <h1 style="font-size:22px;font-weight:500;margin-bottom:4px">{name}</h1>
  <div style="font-size:14px;color:var(--t2);margin-bottom:20px">{strength}</div>

  {"" if not _show_risk_card else f'<div style="background:#FEF3F0;border:1px solid #d85a30;border-radius:10px;padding:20px;margin-bottom:20px"><div style="font-size:11px;font-weight:700;color:#d85a30;text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px">Revenue at risk</div><div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px"><span style="font-size:32px;font-weight:600;color:#712b13">${_at_risk_annual:,}</span><span style="font-size:14px;color:#d85a30">&nbsp;per year</span></div><div style="font-size:13px;color:#5a2010;line-height:1.6">Based on {_neg_rev_mo} negative reviews/month — each one representing ~{_ICEBERG} customers who had a poor experience and may not return. At an average basket of ${_BASKET}, that is <strong>${_at_risk_mo:,}/month</strong> in at-risk spend.</div></div>'}

  <div class="sec">What the data shows</div>
  <div class="card {'cor' if neg_rate > mkt_neg else 'amb'}">
    <div class="clabel">Key finding</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:8px;line-height:1.5">{lead_finding}</div>
    <div style="font-size:13px;font-weight:500;color:var(--t);padding-top:8px;border-top:1px solid var(--bd)">→ {alert}</div>
  </div>
  {'<div class="card teal"><div class="clabel">What is working</div><div style="font-size:13px;color:var(--t2)">' + strength + '</div></div>' if strength else ''}
  {f'''<div class="card cor"><div class="clabel">What needs attention</div><div style="font-size:13px;color:var(--t2);line-height:1.5">{alert_detail}</div></div>''' if alert_detail else ''}



  {(f'''<div class="sec" style="margin-top:28px">How you compare — {cluster_name + " (" + str(cl_count) + " stores)" if show_metro_col else metro_label}</div>
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:16px;overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr>
        <th style="text-align:left;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">Signal</th>
        <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">You</th>
        {"" if not show_metro_col else f'<th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">{cluster_name}</th>'}
        <th style="text-align:center;padding:6px 8px;color:var(--t3);font-weight:600;border-bottom:1px solid var(--bd)">{metro_label}</th>
      </tr></thead>
      <tbody>
        <tr style="border-bottom:1px solid var(--bd)">
          <td style="padding:8px;color:var(--t2)">Google rating</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#1d9e75" if rating >= cl_rating else "#d85a30"}">★ {rating}</td>
          {"" if not show_metro_col else f'<td style="padding:8px;text-align:center;color:var(--t2)">★ {cl_rating}</td>'}
          <td style="padding:8px;text-align:center;color:var(--t2)">★ {_m_rating}</td>
        </tr>
        <tr style="border-bottom:1px solid var(--bd)">
          <td style="padding:8px;color:var(--t2)">Est. negative rate</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#d85a30" if neg_rate > cl_neg else "#1d9e75"}">{neg_rate}%</td>
          {"" if not show_metro_col else f'<td style="padding:8px;text-align:center;color:var(--t2)">{cl_neg}%</td>'}
          <td style="padding:8px;text-align:center;color:var(--t2)">{_m_neg}%</td>
        </tr>
        <tr>
          <td style="padding:8px;color:var(--t2)">Reviews / month</td>
          <td style="padding:8px;text-align:center;font-weight:500;color:{"#1d9e75" if monthly_rev >= cl_pace else "#d85a30"}">{monthly_rev}</td>
          {"" if not show_metro_col else f'<td style="padding:8px;text-align:center;color:var(--t2)">{cl_pace}</td>'}
          <td style="padding:8px;text-align:center;color:var(--t2)">{_m_monthly}</td>
        </tr>
      </tbody>
    </table>
    <div style="font-size:10px;color:var(--t3);margin-top:10px">Estimated from Google Places data. Based on public Google reviews and Maps signals.</div>
  </div>''') if cl_count > 0 else f'''<div class="sec">Dimension breakdown</div>
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:16px">
    {dim_bars}
  </div>'''}

  {(f'''<div class="sec">Staff signal</div>''') if not is_stage_a else ""}

  <!-- Ranking Consequence Card -->
  <div class="sec">Market signals</div>
  <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:10px">
    {stats_cells}
  </div>

  <!-- What customers are saying + pattern card — before comparison -->
  {review_quotes_html}
  {pattern_card_html}

  <div class="cta">
    <div style="font-size:16px;font-weight:500;margin-bottom:8px">Questions about what you're seeing?</div>
    <div style="font-size:13px;color:var(--t2);margin-bottom:14px;line-height:1.5">Happy to walk through any of this. Just reply to this email.</div>
  </div>

  <div style="font-size:11px;color:var(--t3);margin-top:20px;line-height:1.6">
    This snapshot uses publicly available data: Google reviews and Google Maps.<br>
    Data is estimated from public signals — actual figures require your internal records.
  </div>
</div>
</body>
</html>"""


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

    # Load reviews for hook generation
    all_reviews = {}
    biz_raw = load_json(f"{slug}_all_businesses.json", [])
    if isinstance(biz_raw, list):
        for b in biz_raw:
            pid = b.get("place_id", "")
            if pid and b.get("reviews"):
                all_reviews[pid] = b["reviews"]
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

    # Compute metro-level averages for geographic comparison column
    # Metro groupings:
    #   Seattle Metro  = clusters 1 (Eastside), 2 (Seattle Core), 3 (South King), 4 (Snohomish)
    #   Puget Sound    = clusters 1-5 (includes Tacoma, used when cluster is Tacoma)
    #   Portland Metro = cluster 6
    def _avg_stats(stat_list, key, nested=None):
        vals = []
        for s in stat_list:
            v = s.get(nested, {}).get(key) if nested else s.get(key)
            if v is not None: vals.append(float(v))
        return round(sum(vals) / len(vals), 3) if vals else 0

    def _build_metro(stat_list, label):
        return {
            "label":               label,
            "avg_rating":          _avg_stats(stat_list, "avg_rating"),
            "avg_negative_rate":   _avg_stats(stat_list, "negative_rate"),
            "avg_monthly_reviews": _avg_stats(stat_list, "avg_monthly_reviews", nested="marketing"),
            "count":               len(stat_list),
        }

    seattle_metro_stats = [s for s in all_stats.values() if str(s.get("cluster_id","")) in {"1","2","3","4"}]
    puget_sound_stats   = [s for s in all_stats.values() if str(s.get("cluster_id","")) in {"1","2","3","4","5"}]
    portland_stats      = [s for s in all_stats.values() if str(s.get("cluster_id","")) == "6"]

    metro_avgs = {
        "seattle_metro": _build_metro(seattle_metro_stats, "Seattle Metro"),
        "puget_sound":   _build_metro(puget_sound_stats,   "Puget Sound"),
        "portland":      _build_metro(portland_stats,      "Portland Metro"),
    }

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
            _cluster_id = str(stats.get("cluster_id",""))
            if _cluster_id == "6":
                _metro_avg = metro_avgs["portland"]
            elif _cluster_id == "5":
                _metro_avg = metro_avgs["puget_sound"]   # Tacoma — use full Puget Sound
            else:
                _metro_avg = metro_avgs["seattle_metro"] # Eastside/Seattle/SouthKing/Snohomish

            # If cluster has < 3 stores, show metro for both cluster and metro columns
            _cl_count = stats.get("cluster_benchmarks", {}).get("count", 0)
            if _cl_count < 3:
                # Override cluster benchmarks with metro so comparison is still meaningful
                stats["cluster_benchmarks"] = {
                    "avg_rating":          _metro_avg["avg_rating"],
                    "avg_negative_rate":   _metro_avg["avg_negative_rate"],
                    "avg_monthly_reviews": _metro_avg["avg_monthly_reviews"],
                    "count":               _metro_avg["count"],
                }
                stats["cluster_name"] = _metro_avg["label"]

            html     = build_report_html(name, place_id, config, stats, ratios, benchmarks, ranking=ranking, all_stats=all_stats, reviews=all_reviews.get(place_id, []), metro_avg=_metro_avg)
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
    print("  Open any HTML file in your browser to review.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate Stage 2 personalised HTML reports. "
                    "City and industry read from config.yaml."
    )
    parser.add_argument("--place-id", default=None,
                        help="Generate for one business only (optional)")
    args = parser.parse_args()
    run(args.place_id)
