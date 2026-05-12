"""
local_ownership_scorer.py — Scores each business on likelihood of having
a local decision maker worth cold-outreach to.

Score 0-100:
  >= 60 : TIER_A — full outreach sequence
  30-59 : TIER_B — send Email 1, human review before Email 2
  < 30  : TIER_C — likely corporate, add to corporate list

Signals used (all from public data already collected):
  + Named owner in reviews
  + Personalised owner responses
  + 1-2 locations only
  + Personal email domain (gmail, yahoo = owner runs it)
  - 5+ locations in city
  - Templated review responses
  - Corporate website signals
  - No reviews in last 6 months (dormant)

No hardcoded chain lists. Signal-based only.

Usage:
    python local_ownership_scorer.py --city seattle_wa
"""

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "pipeline.db"

PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com",
    "hotmail.com", "icloud.com", "me.com",
}

CORPORATE_WEBSITE_SIGNALS = [
    "thrivepet", "bluepearl", "banfield", "vca",
    "natvet", "pathway", "nvisionvet",
    "/careers", "/investor", "/franchise",
    "corporate", "holdings", "group.com",
]

PERSONAL_RESPONSE_PATTERNS = [
    r'\bdr\.?\s+\w+\b',          # "Dr. Smith"
    r'\bi personally\b',
    r'\bmy team\b',
    r'\bmy practice\b',
    r'\bmy clinic\b',
    r'\bour family\b',
    r'\bowner\b',
]

TEMPLATED_RESPONSE_PATTERNS = [
    "thank you for your feedback",
    "we take all concerns",
    "please contact our",
    "management team",
    "we strive to",
    "our team works hard",
]


def score_business(biz, reviews=None, stats=None, name_frequency=None):
    """
    Score one business 0-100 on local decision maker likelihood.
    Returns (score, tier, reasons).
    name_frequency: dict of {name: count} across all businesses in dataset.
    """
    score   = 50  # Start neutral
    reasons = []

    name    = biz.get("name", "")
    email   = biz.get("email", "")
    website = biz.get("website", "")
    phone   = biz.get("phone", "")
    review_count = biz.get("user_ratings_total", 0)
    types   = biz.get("types", [])

    # --- Positive signals ---

    # --- Chain detection signals ---

    # Name appears multiple times in local dataset = chain
    if name_frequency and name_frequency.get(name, 1) >= 3:
        score -= 25
        reasons.append(f"Name appears {name_frequency[name]}x in dataset — chain indicator")

    # Franchise affiliation pattern: ", A [X] Partner" in name
    import re as _re
    if _re.search(r',\s+a\s+\w.*partner', name, _re.IGNORECASE):
        score -= 20
        reasons.append("Franchise affiliation in name (', A [X] Partner')")

    # Google Places types — if no veterinary_care type = likely not a vet
    if types and 'veterinary_care' not in types and 'animal_shelter' not in types:
        non_vet = {'hospital', 'doctor', 'health', 'dentist', 'pharmacy'}
        if non_vet & set(types):
            score -= 30
            reasons.append(f"Non-vet business type: {set(types) & non_vet}")

    # --- Personal ownership signals ---

    # Personal email domain = owner likely runs it
    if email and "@" in email:
        domain = email.split("@")[1].lower()
        if domain in PERSONAL_EMAIL_DOMAINS:
            score += 15
            reasons.append(f"Personal email domain ({domain})")

    # Low location count = independent
    # (proxy: single place_id, no sibling signals)
    location_count = _get_location_count(biz.get("place_id", ""))
    if location_count == 0:
        pass  # Unknown — no DB data available, no signal
    elif location_count == 1:
        score += 15
        reasons.append("Single location")
    elif location_count == 2:
        score += 8
        reasons.append("Two locations (likely same owner)")
    elif location_count >= 5:
        score -= 25
        reasons.append(f"5+ locations ({location_count}) — likely corporate")

    # Review recency — active in last 6 months
    if stats:
        monthly = stats.get("monthly", {})
        six_months_ago = (datetime.now() - timedelta(days=180)).strftime("%Y-%m")
        recent_months  = [m for m in monthly.keys() if m >= six_months_ago]
        if recent_months:
            score += 10
            reasons.append("Active reviews in last 6 months")
        else:
            score -= 15
            reasons.append("No recent reviews — possibly dormant")

    # Named owner in reviews
    if reviews:
        owner_named = _check_owner_named_in_reviews(reviews)
        if owner_named:
            score += 20
            reasons.append(f"Named owner detected: {owner_named}")

        # Personal vs templated response style
        response_style = _classify_response_style(reviews)
        if response_style == "personal":
            score += 15
            reasons.append("Personalised owner responses")
        elif response_style == "templated":
            score -= 20
            reasons.append("Templated corporate-style responses")

    # --- Negative signals ---

    # Corporate website indicators — strong chain signal
    # Penalty of 25 ensures score drops below TIER_C threshold (30)
    # when combined with neutral start (50): 50-25 = 25 -> TIER_C
    if website:
        website_lower = website.lower()
        for signal in CORPORATE_WEBSITE_SIGNALS:
            if signal in website_lower:
                score -= 25
                reasons.append(f"Corporate website signal: {signal}")
                break

    # Sufficient review volume for analysis
    if review_count < 50:
        score -= 10
        reasons.append(f"Low review count ({review_count}) — thin data")
    elif review_count >= 200:
        score += 5
        reasons.append(f"Strong review volume ({review_count})")

    # Clamp and tier
    score = max(0, min(100, score))

    # Stage A threshold: 50 (Google Places lite — limited signals)
    # Stage B threshold: 60 (full DataForSEO — rich signals)
    # Use 50 since we can't distinguish independents well without review text
    # TIER_C < 30: catches businesses with strong corporate signals (website penalty -25)
    if score >= 50:
        tier = "TIER_A"
    elif score >= 30:
        tier = "TIER_B"
    else:
        tier = "TIER_C"

    return score, tier, reasons


def _get_location_count(place_id, db_path=DB_PATH):
    """Estimate location count from existing DB data."""
    try:
        conn  = sqlite3.connect(db_path)
        grp   = conn.execute(
            "SELECT owner_group_id FROM businesses WHERE place_id = ?",
            (place_id,)
        ).fetchone()
        if grp and grp[0]:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM businesses WHERE owner_group_id = ?",
                (grp[0],)
            ).fetchone()[0]
            conn.close()
            return cnt
        conn.close()
        return 1
    except Exception:
        return 0  # unknown — no signal available, don't add location bonus


def _check_owner_named_in_reviews(reviews):
    """Returns first named owner found in review responses, or None."""
    for review in (reviews or []):
        response = review.get("owner_response", {})
        text     = response if isinstance(response, str) else (response.get("text", "") if response else "")
        for pattern in PERSONAL_RESPONSE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and pattern.startswith(r'\bdr'):
                return match.group(0)
    return None


def _classify_response_style(reviews):
    """Returns 'personal', 'templated', or 'none'."""
    personal_count  = 0
    templated_count = 0
    total_responses = 0

    for review in (reviews or []):
        response = review.get("owner_response", {})
        text     = (response if isinstance(response, str) else (response.get("text", "") if response else "")).lower()
        if not text:
            continue
        total_responses += 1

        for pat in PERSONAL_RESPONSE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                personal_count += 1
                break

        for phrase in TEMPLATED_RESPONSE_PATTERNS:
            if phrase in text:
                templated_count += 1
                break

    if total_responses == 0:
        return "none"
    if personal_count > templated_count:
        return "personal"
    if templated_count > personal_count:
        return "templated"
    return "none"


def score_all_businesses(city, industry="veterinary"):
    slug = city.lower().replace(" ", "_").replace(",", "")

    # Load businesses
    biz_file = f"{slug}_all_businesses.json"
    if not os.path.exists(biz_file):
        biz_file = f"{slug}_veterinary_clinic.json"
    if not os.path.exists(biz_file):
        print(f"  No business file found for {city}")
        return []

    with open(biz_file) as f:
        businesses = json.load(f)

    # Load reviews if available
    reviews_by_pid = {}
    reviews_file   = f"{slug}_clean.json"
    if os.path.exists(reviews_file):
        with open(reviews_file) as f:
            raw = json.load(f)
        for pid, data in raw.items():
            reviews_by_pid[pid] = data.get("reviews", [])
    else:
        # Stage A: use up to 5 Place Details reviews from all_businesses.json
        for biz in businesses:
            pid = biz.get("place_id", "")
            if pid and biz.get("reviews"):
                reviews_by_pid[pid] = biz["reviews"]

    # Load stats if available
    stats_by_pid = {}
    stats_file   = f"{slug}_stats.json"
    if os.path.exists(stats_file):
        with open(stats_file) as f:
            raw = json.load(f)
        stats_by_pid = raw.get("businesses", {})

    print(f"\n  Scoring {len(businesses)} businesses...")
    print()

    results  = []
    tier_a   = []
    tier_b   = []
    tier_c   = []

    # Build name frequency map — same name appearing 3+ times = chain
    from collections import Counter as _Counter
    name_freq = _Counter(b.get("name", "") for b in businesses)

    for biz in businesses:
        pid     = biz.get("place_id", "")
        reviews = reviews_by_pid.get(pid)
        stats   = stats_by_pid.get(pid)

        score, tier, reasons = score_business(biz, reviews, stats, name_frequency=name_freq)

        biz["ownership_score"] = score
        biz["ownership_tier"]  = tier
        biz["score_reasons"]   = reasons

        results.append(biz)

        if tier == "TIER_A":
            tier_a.append(biz)
        elif tier == "TIER_B":
            tier_b.append(biz)
        else:
            tier_c.append(biz)

        status = "✅" if tier == "TIER_A" else "🔍" if tier == "TIER_B" else "❌"
        print(f"  {status} {score:>3}/100  [{tier}]  {biz.get('name','')[:40]}")

    # Save scored businesses
    out_file = f"{slug}_all_businesses.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)

    # Save tier lists
    tier_a_file = f"{slug}_tier_a.json"
    tier_b_file = f"{slug}_tier_b.json"
    tier_c_file = f"{slug}_tier_c.json"

    with open(tier_a_file, "w") as f:
        json.dump(tier_a, f, indent=2)
    with open(tier_b_file, "w") as f:
        json.dump(tier_b, f, indent=2)
    with open(tier_c_file, "w") as f:
        json.dump(tier_c, f, indent=2)

    # Update pipeline.db
    conn = sqlite3.connect(DB_PATH)
    for biz in results:
        try:
            conn.execute("""
                UPDATE businesses
                SET ownership_type = ?
                WHERE place_id = ?
            """, (biz["ownership_tier"], biz["place_id"]))
        except Exception:
            pass
    conn.commit()
    conn.close()

    print()
    print("  SCORING COMPLETE")
    print("  " + "-"*50)
    print(f"  TIER A (full sequence):   {len(tier_a)}")
    print(f"  TIER B (human review):    {len(tier_b)}")
    print(f"  TIER C (corporate, skip): {len(tier_c)}")
    print()
    print(f"  Saved scored list to {out_file}")
    print(f"  Tier lists: {tier_a_file}, {tier_b_file}, {tier_c_file}")
    print()

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    from config_loader import load as _load_cfg
    _cfg_main, _ = _load_cfg()
    parser.add_argument("--city",     default=_cfg_main.city)
    parser.add_argument("--industry", default=_cfg_main.industry)
    args = parser.parse_args()
    score_all_businesses(args.city, args.industry)
