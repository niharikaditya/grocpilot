"""
analyse.py — Claude scoring, tone profiling, lead finding.

Changes from previous version:
- PROMPT_VERSION constant — skip businesses with current config version
- Dimension scores calculated rule-based (no Claude tokens needed)
- Claude used only for tone profile + lead finding narrative
- Merge into existing configs — never overwrite unchanged businesses
"""

import json
import os
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_KEY"))

# Load content from config
try:
    from config_loader import load as _load_cfg
    _cfg, _ = _load_cfg()
    _INDUSTRY = _cfg.industry
except Exception:
    _INDUSTRY = "veterinary"

try:
    from content_loader import get_industry_content as _get_ic
    _IC = _get_ic(_INDUSTRY)
except Exception:
    _IC = None

# Bump this when the Claude prompt changes.
# Businesses with this version in their config will be skipped.
PROMPT_VERSION = "3.6"  # Stage A signal rules: no response rate, no dimension scores in lead_finding


def load_stats(city):
    filename = f"{city.lower().replace(' ', '_').replace(',', '')}_stats.json"
    with open(filename, "r") as f:
        return json.load(f)


def load_existing_configs(city):
    filename = f"{city.lower().replace(' ', '_').replace(',', '')}_configs.json"
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Rule-based dimension scoring — no Claude needed, no cost
# ---------------------------------------------------------------------------

def score_dimensions(business_stats, benchmarks):
    dims = business_stats.get("dimensions") or business_stats.get("dimension_scores") or {}
    total  = business_stats.get("total_reviews", 0)
    scores = {}

    def calc_score(mention_rate, neg_mention_rate, market_mention_rate):
        if mention_rate == 0:
            return 7.0
        complaint_intensity = mention_rate * neg_mention_rate
        market_intensity    = market_mention_rate * 0.5
        raw      = 10 - (complaint_intensity * 20)
        relative = raw + (market_intensity - complaint_intensity) * 5
        return round(max(1.0, min(10.0, relative)), 1)

    dim_keys = [
        ("clinical_quality",   "clinical_quality_mention_rate",   0.10),
        ("appointment_access", "appointment_access_mention_rate",  0.15),
        ("wait_time",          "wait_time_mention_rate",           0.20),
        ("pricing_trust",      "pricing_trust_mention_rate",       0.15),
        ("communication",      "communication_mention_rate",       0.10),
        ("after_hours",        "after_hours_mention_rate",         0.10),
    ]

    for dim, bench_key, default in dim_keys:
        scores[dim] = calc_score(
            dims[dim]["mention_rate"],
            dims[dim]["neg_mention_rate"],
            benchmarks.get(bench_key, default)
        )

    # Confidence dampener — low review counts pull score toward market avg
    confidence = min(total / 60.0, 1.0)
    MARKET_AVG = 7.0
    for dim in list(scores.keys()):
        raw         = scores[dim]
        dampened    = round(raw * confidence + MARKET_AVG * (1 - confidence), 1)
        scores[dim] = max(1.0, min(10.0, dampened))

    weights = {
        "clinical_quality":   0.30,
        "appointment_access": 0.25,
        "wait_time":          0.15,
        "pricing_trust":      0.15,
        "communication":      0.10,
        "after_hours":        0.05,
    }
    scores["overall"] = round(
        sum(scores[dim] * weight for dim, weight in weights.items()), 1
    )
    return scores


# ---------------------------------------------------------------------------
# Claude — tone profile + lead finding narrative only
# ---------------------------------------------------------------------------

def generate_narrative(business_name, business_stats, dimension_scores,
                       benchmarks, ratios=None):
    """
    Uses Claude Haiku to generate:
    - tone_profile: urgent / collegial / analytical
    - lead_finding: one-sentence hook for outreach
    - alertHeadline: the main problem
    - strengthHeadline: the genuine win

    Everything else is calculated rule-based above.
    """
    marketing     = business_stats.get("marketing", {})
    neg_rate      = round(business_stats.get("negative_rate", 0) * 100, 1)
    response_rate = round(marketing.get("owner_response_rate", 0) * 100, 1)
    monthly_rev   = round(marketing.get("avg_monthly_reviews", 0), 1)
    overall_score = dimension_scores["overall"]

    market_neg    = round(benchmarks.get("avg_negative_rate", 0.15) * 100, 1)
    market_resp   = round(benchmarks.get("avg_response_rate", 0.65) * 100, 1)
    market_rev    = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)

    leakage_low  = 0
    leakage_high = 0
    if ratios:
        total_rec = ratios.get("revenue_leakage", {}).get("total_recoverable", {})
        leakage_low  = total_rec.get("low", 0)
        leakage_high = total_rec.get("high", 0)

    # Load industry-specific prompt template
    industry_label = f"independent {_INDUSTRY} practice"
    prompt_template = ""
    if _IC and _IC.analyse_prompt:
        prompt_template = _IC.analyse_prompt
        industry_label  = _IC.label
    
    # Build prompt variables
    prompt_vars = {
        "business_name":         business_name,
        "label":                 industry_label,
        "overall_score":         dimension_scores["overall"],
        "neg_rate":              round(business_stats.get("negative_rate", 0) * 100, 1),
        "response_rate":         round(business_stats.get("marketing", {}).get("owner_response_rate", 0) * 100, 1),
        "monthly_reviews":       round(business_stats.get("marketing", {}).get("avg_monthly_reviews", 0), 1),
        "market_neg":            round(benchmarks.get("avg_negative_rate", 0.15) * 100, 1),
        "market_resp":           round(benchmarks.get("avg_response_rate", 0.65) * 100, 1),
        "market_rev":            round(benchmarks.get("avg_monthly_reviews", 6.2), 1),
        "rev_low":               leakage_low,
        "rev_high":              leakage_high,
        "dim_clinical_quality":  dimension_scores.get("clinical_quality", 7.0),
        "dim_appointment_access":dimension_scores.get("appointment_access", 7.0),
        "dim_wait_time":         dimension_scores.get("wait_time", 7.0),
        "dim_pricing_trust":     dimension_scores.get("pricing_trust", 7.0),
        "dim_communication":     dimension_scores.get("communication", 7.0),
        "dim_after_hours":       dimension_scores.get("after_hours", 7.0),
    }

    if prompt_template:
        from content_loader import render_template
        prompt = render_template(prompt_template, prompt_vars)
    else:
        # Fallback inline prompt
        # STAGE A DATA ONLY: Google Places API — rating, review count, monthly pace, estimated negative rate
        # Response rate and dimension scores are NOT available at Stage A (requires DataForSEO)
        # They are provided below for internal scoring only — NEVER appear in lead_finding or strengthHeadline
        avg_rating = round(business_stats.get("avg_rating", 0), 1)
        total_reviews = business_stats.get("total_reviews", 0)
        # Cluster comparison signals
        cluster_name     = business_stats.get("cluster_name", "Seattle metro")
        cl_count         = business_stats.get("cluster_benchmarks", {}).get("count", 0)
        cl_rating        = round(business_stats.get("cluster_benchmarks", {}).get("avg_rating", 0), 2)
        cl_neg           = round(business_stats.get("cluster_benchmarks", {}).get("avg_negative_rate", 0) * 100, 1)
        cl_pace          = round(business_stats.get("cluster_benchmarks", {}).get("avg_monthly_reviews", 0), 1)
        market_avg_rating = round(benchmarks.get("avg_rating", 4.5), 1)
        prompt = f"""You are analysing a {industry_label} for Markvise, a market intelligence service.

BUSINESS: {business_name}

STAGE A PUBLIC SIGNALS (Google Maps — fully verifiable by the owner):
  Google rating:    {avg_rating} stars   (cluster avg: {cl_rating} | Seattle avg: {market_avg_rating})
  Total reviews:    {total_reviews}
  Monthly pace:     {monthly_rev}/mo     (cluster avg: {cl_pace}/mo | Seattle avg: {market_rev}/mo)
  Negative rate:    {neg_rate}%          (cluster avg: {cl_neg}% | Seattle avg: {market_neg}%) [estimated from rating]
  Cluster:          {cluster_name} ({cl_count} practices)
  Revenue opportunity:  ${leakage_low:,} - ${leakage_high:,}/year

[Internal scoring data withheld — not available at Stage A]

Generate JSON with EXACTLY these four fields:

{{
  "tone_profile": "urgent | collegial | analytical",
  "lead_finding": "One specific sentence using only Stage A public signals. No internal scores.",
  "alertHeadline": "Their most urgent problem in 8 words or less",
  "strengthHeadline": "Their genuine strength in 8 words or less"
}}

tone_profile rules:
  urgent:     neg_rate > 20% OR avg_rating < 4.0
  analytical: avg_rating >= 4.7 AND neg_rate < 10%
  collegial:  everything else

lead_finding — CHOOSE EXACTLY ONE of the three templates below based on the data:

  IF neg_rate > {market_neg} (negative rate gap):
    COPY THIS TEMPLATE EXACTLY, filling in the numbers:
    "{business_name} has a {{neg_rate}}% estimated negative review rate — {{X}}x the {{market_neg}}% {cluster_name} average — representing {{revenue}} in recoverable annual revenue."
    Use: neg_rate={neg_rate}, X={round(neg_rate/max(market_neg,1),1)}, market_neg={market_neg}, revenue=${leakage_low:,}-${leakage_high:,} (or omit if $0)

  ELSE IF avg_rating < 4.0 (low Google rating):
    COPY THIS TEMPLATE EXACTLY:
    "At {{avg_rating}} stars across {{total_reviews}} Google reviews, {business_name} sits below the {cluster_name} cluster average of {cl_rating} stars."
    Use: avg_rating={avg_rating}, total_reviews={total_reviews}

  ELSE (strong practice — use cluster or velocity comparison):
    COPY THIS TEMPLATE EXACTLY:
    "{business_name} generates {{monthly_rev}} new Google reviews per month at {{avg_rating}} stars — strong social proof that most practices in {cluster_name} (avg: {cl_pace}/mo) are not building."
    Use: monthly_rev={monthly_rev}, avg_rating={avg_rating}

DO NOT invent scores, dimensions, or metrics not in the templates above.
DO NOT mention: response rate, clinical quality, communication, pricing trust, appointment access, wait time, overall score, or any X/10 score.

strengthHeadline — use ONLY these signals: Google rating, review count, monthly pace, cluster rank.
  NEVER cite dimension scores or X/10 scores.
  Example: "{total_reviews} reviews at {avg_rating} stars — strong volume for {cluster_name}"

Return only valid JSON. No explanation.
"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )

    raw   = response.content[0].text.strip()
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


# ---------------------------------------------------------------------------
# Revenue leakage estimates — rule-based, no Claude
# ---------------------------------------------------------------------------

def estimate_revenue_leakage(business_stats, benchmarks):
    """
    Calculates revenue opportunity from public data signals.
    Rule-based — no Claude needed.
    """
    marketing     = business_stats.get("marketing", {})
    neg_rate      = business_stats.get("negative_rate", 0)
    response_rate = marketing.get("owner_response_rate", 0)
    monthly_rev   = marketing.get("avg_monthly_reviews", 0)

    market_neg  = benchmarks.get("avg_negative_rate", 0.15)
    market_resp = benchmarks.get("avg_response_rate", 0.65)
    market_rev  = benchmarks.get("avg_monthly_reviews", 6.2)

    # Revenue leakage from negative reviews above market average
    neg_gap     = max(0, neg_rate - market_neg)
    rev_from_neg_low  = round(neg_gap * 100 * 800)   # $800 avg client LTV low
    rev_from_neg_high = round(neg_gap * 100 * 1500)  # $1500 avg client LTV high

    # Revenue from response rate gap
    # Only apply when practice has meaningful negative exposure (>= half market avg)
    resp_gap = max(0, market_resp - response_rate)
    if neg_rate >= market_neg * 0.5:
        rev_from_resp_low  = round(resp_gap * 50 * 400)
        rev_from_resp_high = round(resp_gap * 50 * 800)
    else:
        rev_from_resp_low  = 0
        rev_from_resp_high = 0

    total_low  = rev_from_neg_low  + rev_from_resp_low
    total_high = rev_from_neg_high + rev_from_resp_high

    # ── Velocity opportunity (Option 1) ──────────────────────────────────
    # Only for strong practices where leakage = $0
    # Formula: velocity_gap * 12 * 0.15 (conversion) * LTV
    cl_bench   = business_stats.get("cluster_benchmarks", {})
    cl_pace    = cl_bench.get("avg_monthly_reviews", 0)
    vel_gap    = max(0, cl_pace - monthly_rev)
    if vel_gap > 0:
        vel_opp_low  = round(vel_gap * 12 * 0.15 * 800)
        vel_opp_high = round(vel_gap * 12 * 0.15 * 1500)
    else:
        vel_opp_low  = 0
        vel_opp_high = 0

    # ── Competitive displacement (Option 2) ───────────────────────────────
    # Estimate how many more discovery clicks top cluster performer captures
    # Based on: review pace ratio vs top-quartile cluster pace
    top_quartile_pace = cl_pace * 1.5  # estimate top quartile is 1.5x cluster avg
    if monthly_rev > 0 and top_quartile_pace > monthly_rev:
        displacement_pct = round((top_quartile_pace - monthly_rev) / top_quartile_pace * 100)
    else:
        displacement_pct = 0

    return {
        "total_recoverable": {
            "low":  total_low,
            "high": total_high,
        },
        "quick_wins_90_days": {
            "low":  round(total_low  * 0.3),
            "high": round(total_high * 0.3),
        },
        "from_negative_reviews": {"low": rev_from_neg_low,  "high": rev_from_neg_high},
        "from_response_rate":    {"low": rev_from_resp_low, "high": rev_from_resp_high},
        "velocity_opportunity": {
            "low":      vel_opp_low,
            "high":     vel_opp_high,
            "vel_gap":  round(vel_gap, 1),
            "cl_pace":  round(cl_pace, 1),
        },
        "competitive_displacement": {
            "pct":             displacement_pct,
            "top_pace":        round(top_quartile_pace, 1),
            "your_pace":       round(monthly_rev, 1),
        },
    }


# ---------------------------------------------------------------------------
# Your Move — rule-based, no Claude cost
# ---------------------------------------------------------------------------

# Dimension-specific action playbooks — what top-quartile practices do differently
_MOVE_PLAYBOOK = {
    "pricing_trust": {
        "action": (
            "Add an itemised estimate to your pre-visit confirmation email. "
            "The top-rated practices in your cluster respond to all price-related reviews "
            "within 24 hours with a specific explanation (not a template). "
            "Practices that made this change saw a 30-40% reduction in price-related "
            "negative reviews within 90 days."
        ),
        "short": "Improve pricing transparency",
    },
    "communication": {
        "action": (
            "Send a 48-hour post-visit follow-up message asking one specific question: "
            "'Did we explain everything clearly?' "
            "The top-rated practices in your cluster use this to catch dissatisfied patients "
            "before they write a negative review. "
            "This single change consistently reduces negative review rates by 15-25%."
        ),
        "short": "Add post-visit follow-up",
    },
    "wait_time": {
        "action": (
            "Set a waiting room expectation at check-in — one sentence on realistic wait time. "
            "The top-rated practices in your cluster proactively communicate delays. "
            "Practices that added this had wait-time complaints drop by an average of 40% "
            "within 60 days."
        ),
        "short": "Communicate wait times proactively",
    },
    "appointment_access": {
        "action": (
            "Add a same-day or next-day urgent slot visible on your booking page. "
            "The top-rated practices in your cluster reserve 2-3 urgent slots per day. "
            "This is the single most-mentioned factor in 5-star reviews for your practice type."
        ),
        "short": "Add urgent appointment availability",
    },
    "clinical_quality": {
        "action": (
            "Record a 60-second post-diagnosis explanation for your top 5 most common conditions "
            "and send it to clients after their visit. "
            "The top-rated practices in your cluster use this to reduce 'I didn't understand "
            "the diagnosis' complaints — the root cause of your most negative reviews."
        ),
        "short": "Improve diagnosis communication",
    },
    "after_hours": {
        "action": (
            "Add a clear after-hours protocol to your Google Business profile and website. "
            "Your nearest competitor prominently lists emergency referral partners. "
            "Practices that added this saw a measurable improvement in search ranking "
            "for 'emergency vet' queries in their neighbourhood."
        ),
        "short": "Clarify after-hours and emergency access",
    },
}


def generate_your_move(business_name, dimension_scores, biz_stats, benchmarks):
    """
    Generates one specific recommended action for this business.
    Rule-based — no Claude cost. Uses the worst dimension score vs benchmarks
    to identify the highest-impact move.

    Returns a string (the recommendation) or empty string if insufficient data.
    """
    if not dimension_scores:
        return ""

    # Find the worst-performing dimension (excluding overall)
    dims = {k: v for k, v in dimension_scores.items() if k != "overall"}
    if not dims:
        return ""

    numeric_dims={k:v for k,v in dims.items() if isinstance(v,(int,float))};worst_dim=min(numeric_dims,key=numeric_dims.get) if numeric_dims else None
    worst_score = dims[worst_dim]

    # Only generate if the worst dimension is below 8.0 (meaningful gap)
    # Threshold raised slightly for lite stats where scores are compressed
    if worst_score >= 8.0:
        # All dimensions are reasonably strong — use review velocity gap instead
        marketing    = biz_stats.get("marketing", {})
        monthly_rev  = marketing.get("avg_monthly_reviews", 0)
        market_rev   = benchmarks.get("avg_monthly_reviews", 6.2)
        if monthly_rev < market_rev * 0.5:
            return (
                f"{business_name} collects {round(monthly_rev, 1)} new reviews per month "
                f"vs the Seattle metro average of {market_rev}. "
                "The fastest way to close this gap: ask every client at checkout — "
                "verbally, not via a card — for a Google review. "
                "Practices that added this single verbal ask doubled their review pace "
                "within 30 days."
            )
        return ""

    # Get the playbook for this dimension
    playbook = _MOVE_PLAYBOOK.get(worst_dim)
    if not playbook:
        return ""

    action = playbook["action"]

    # Add a score context line at the start
    dim_label = worst_dim.replace("_", " ").title()
    intro = (
        f"{business_name}'s {dim_label} score ({worst_score}/10) is below the "
        f"Seattle metro average. "
    )

    return intro + action


# ---------------------------------------------------------------------------
# Rule-based lead_finding — Stage A only, no Claude hallucination risk
# ---------------------------------------------------------------------------

def generate_headlines_stage_a(name, biz_stats, benchmarks):
    """
    Rule-based alertHeadline and strengthHeadline for Stage A.
    Uses only Google rating, review count, monthly pace, negative rate, cluster data.
    No Claude — no hallucination, no inferred response rates or dimension scores.
    """
    avg_rating    = round(biz_stats.get("avg_rating", 0), 1)
    total_reviews = biz_stats.get("total_reviews", 0)
    neg_rate      = round(biz_stats.get("negative_rate", 0) * 100, 1)
    monthly_rev   = round(biz_stats.get("marketing", {}).get("avg_monthly_reviews", 0), 1)
    market_neg    = round(benchmarks.get("avg_negative_rate", 0.15) * 100, 1)
    market_rev    = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)
    market_rating = round(benchmarks.get("avg_rating", 4.5), 1)
    cluster_name  = biz_stats.get("cluster_name", "local cluster")
    cl_bench      = biz_stats.get("cluster_benchmarks", {})
    cl_pace       = round(cl_bench.get("avg_monthly_reviews", 0), 1)

    # alertHeadline — most urgent public signal
    if neg_rate > market_neg * 2:
        alert = f"{neg_rate}% negative rate — {round(neg_rate/max(market_neg,1),1)}x cluster average"
    elif neg_rate > market_neg:
        alert = f"Negative review rate above {cluster_name} average"
    elif avg_rating < 4.0:
        alert = f"{avg_rating} stars — below Seattle market average"
    elif monthly_rev < market_rev * 0.5:
        alert = f"Review pace {monthly_rev}/mo — below market average of {market_rev}/mo"
    elif cl_pace > 0 and monthly_rev < cl_pace * 0.5:
        alert = f"Review velocity below {cluster_name} cluster average"
    else:
        alert = f"Review volume opportunity in {cluster_name}"

    # strengthHeadline — best public signal
    if avg_rating >= 4.8 and total_reviews >= 200:
        strength = f"{avg_rating} stars across {total_reviews:,} reviews — top-tier reputation"
    elif monthly_rev >= market_rev * 2:
        strength = f"{monthly_rev}/mo review pace — {round(monthly_rev/max(market_rev,1),1)}x market average"
    elif avg_rating >= 4.7:
        strength = f"{avg_rating} stars — above {cluster_name} average"
    elif total_reviews >= 500:
        strength = f"{total_reviews:,} reviews — strong market presence"
    elif neg_rate == 0 and avg_rating >= 4.5:
        strength = f"Zero negative reviews at {avg_rating} stars"
    elif avg_rating >= 4.5:
        strength = f"{avg_rating} stars — strong client satisfaction"
    else:
        strength = f"{avg_rating} stars — established local practice"

    return alert, strength


def generate_lead_finding_stage_a(name, biz_stats, benchmarks):
    """
    Generates lead_finding from Stage A public signals only.
    Deterministic — no Claude, no hallucination risk.
    Uses only: Google rating, review count, monthly pace, estimated negative rate,
    cluster benchmarks.
    """
    avg_rating   = round(biz_stats.get("avg_rating", 0), 1)
    total_reviews = biz_stats.get("total_reviews", 0)
    neg_rate     = round(biz_stats.get("negative_rate", 0) * 100, 1)
    monthly_rev  = round(biz_stats.get("marketing", {}).get("avg_monthly_reviews", 0), 1)
    market_neg   = round(benchmarks.get("avg_negative_rate", 0.15) * 100, 1)
    market_rev   = round(benchmarks.get("avg_monthly_reviews", 6.2), 1)
    market_rating = round(benchmarks.get("avg_rating", 4.5), 1)

    cluster_name  = biz_stats.get("cluster_name", "the local cluster")
    cl_bench      = biz_stats.get("cluster_benchmarks", {})
    cl_rating     = round(cl_bench.get("avg_rating", 0), 2)
    cl_neg        = round(cl_bench.get("avg_negative_rate", 0) * 100, 1)
    cl_pace       = round(cl_bench.get("avg_monthly_reviews", 0), 1)

    leakage       = estimate_revenue_leakage(biz_stats, benchmarks)
    rev_low       = leakage["total_recoverable"]["low"]
    rev_high      = leakage["total_recoverable"]["high"]

    # SCENARIO 1: Negative rate gap
    if neg_rate > market_neg:
        multiplier = round(neg_rate / max(market_neg, 1), 1)
        rev_str = f", representing ${rev_low:,}-${rev_high:,} in recoverable annual revenue" if rev_low > 0 else ""
        return (
            f"{name} has an estimated {neg_rate}% negative review rate — "
            f"{multiplier}x the {market_neg}% {cluster_name} average{rev_str}."
        )

    # SCENARIO 2: Low Google rating
    if avg_rating < 4.0:
        cluster_str = f" versus the {cluster_name} cluster average of {cl_rating} stars" if cl_rating > 0 else ""
        return (
            f"At {avg_rating} stars across {total_reviews:,} Google reviews, "
            f"{name} sits below the Seattle market average of {market_rating} stars{cluster_str}."
        )

    # SCENARIO 3: Cluster rating gap
    if cl_rating > 0 and avg_rating < cl_rating - 0.1:
        return (
            f"{name} rates {avg_rating} stars versus the {cluster_name} cluster average of "
            f"{cl_rating} stars — a gap visible to every client choosing between local practices."
        )

    # SCENARIO 4: Review velocity gap vs cluster
    if cl_pace > 0 and monthly_rev < cl_pace * 0.6:
        return (
            f"{name} generates {monthly_rev} new Google reviews per month at {avg_rating} stars — "
            f"below the {cluster_name} cluster average of {cl_pace}/mo. "
            f"Strong ratings not converting into review volume."
        )

    # SCENARIO 5: Strong practice — highlight volume as opportunity
    return (
        f"With {total_reviews:,} Google reviews at {avg_rating} stars, {name} has one of the "
        f"stronger reputations in {cluster_name} — and an opportunity to convert that into "
        f"measurable competitive advantage."
    )


# ---------------------------------------------------------------------------
# Main analysis loop
# ---------------------------------------------------------------------------

def analyse_businesses(city, force=False):
    stats_data = load_stats(city)
    businesses = stats_data.get("businesses", {})

    # Load industry + city benchmarks
    benchmarks = {}
    _slug = city.lower().replace(" ", "_").replace(",", "")
    for bench_path in [
        "benchmarks/universal.json",
        f"benchmarks/industries/{_INDUSTRY}.json",
        f"benchmarks/cities/{_slug}.json",
    ]:
        if os.path.exists(bench_path):
            with open(bench_path) as f:
                benchmarks.update(json.load(f))

    # Load existing configs — only update what changed
    existing_configs = load_existing_configs(city)

    print(f"Loading stats for {len(businesses)} businesses")
    print(f"Analysing {len(businesses)} businesses with Claude...")
    print()

    configs_generated = 0
    configs_skipped   = 0

    for place_id, biz_stats in businesses.items():
        name = biz_stats.get("name", place_id)

        # Skip if config is current version and not forced
        existing = existing_configs.get(place_id, {})
        if (not force and
                existing.get("prompt_version") == PROMPT_VERSION and
                existing.get("config")):
            print(f"  {name} — config current (v{PROMPT_VERSION}), skipping")
            configs_skipped += 1
            continue

        print(f"  Analysing {name}...")

        # Rule-based scoring — no Claude cost
        dim_scores = biz_stats.get("dimension_scores") if biz_stats.get("data_source")=="google_places_lite" else score_dimensions(biz_stats, benchmarks)
        leakage    = estimate_revenue_leakage(biz_stats, benchmarks)

        # ── Stage-aware narrative generation ──────────────────────────────────
        # Stage A (google_places_lite): rule-based lead_finding, Claude for tone/headlines only
        # Stage B (DataForSEO, post-interest): Claude generates full narrative including lead_finding
        is_stage_a = biz_stats.get("data_source") == "google_places_lite"

        try:
            narrative = generate_narrative(
                name, biz_stats, dim_scores, benchmarks,
                ratios={"revenue_leakage": leakage}
            )
        except Exception as e:
            print(f"    ERROR generating narrative: {e}")
            narrative = {
                "tone_profile":     "collegial",
                "lead_finding":     f"{name} has opportunities to improve patient engagement.",
                "alertHeadline":    "Review engagement gap identified",
                "strengthHeadline": "Established local practice",
            }

        config = {
            "alertHeadline":    narrative.get("alertHeadline", ""),
            "strengthHeadline": narrative.get("strengthHeadline", ""),
        }

        # Generate "Your Move" — rule-based, no Claude cost
        your_move = generate_your_move(name, dim_scores, biz_stats, benchmarks)

        existing = existing_configs.get(place_id, {})

        if is_stage_a:
            # Stage A: fully rule-based — no Claude for any content fields
            # Uses only Google rating, review count, monthly pace, negative rate, cluster data
            # No response rate, no dimension scores, no hallucination risk
            lead_finding   = generate_lead_finding_stage_a(name, biz_stats, benchmarks)
            alert_headline, strength_headline = generate_headlines_stage_a(name, biz_stats, benchmarks)
            config["alertHeadline"]    = alert_headline
            config["strengthHeadline"] = strength_headline
            print(f"    [Stage A] rule-based lead_finding + headlines")
        else:
            # Stage B: Claude-generated from full DataForSEO data
            # 100 reviews, real dimension scores, response rate, text analysis all available
            lead_finding = narrative.get("lead_finding", "")
            print(f"    [Stage B] Claude lead_finding + headlines")

        existing_configs[place_id] = {
            "name":             name,
            "prompt_version":   PROMPT_VERSION,
            "config":           config,
            "dimension_scores": dim_scores,
            "tone_profile":     narrative.get("tone_profile", "collegial"),
            "lead_finding":     lead_finding,
            "revenue_leakage":  leakage,
            "your_move":        your_move,
            # Pillar data — populated by pillar scripts after analyse.py runs.
            "p2_data":          existing.get("p2_data", {}),
            "p4_data":          existing.get("p4_data", {}),
            "p3_data":          existing.get("p3_data", {}),
            "p5_data":          existing.get("p5_data", {}),
            "named_competitors": existing.get("named_competitors", []),
            "onboarded_at":     existing.get("onboarded_at", ""),
        }

        rev_low  = leakage["total_recoverable"]["low"]
        rev_high = leakage["total_recoverable"]["high"]
        tone     = narrative.get("tone_profile", "?")
        print(f"  Score: {dim_scores['overall']}/10  Tone: {tone}  "
              f"Recoverable: ${rev_low:,}-${rev_high:,}")

        configs_generated += 1

    # Save — merge with existing, never overwrite all
    slug     = city.lower().replace(" ", "_").replace(",", "")
    out_file = f"{slug}_configs.json"
    with open(out_file, "w") as f:
        json.dump(existing_configs, f, indent=2)

    total = len(businesses)
    print()
    print("=" * 50)
    print("ANALYSIS COMPLETE")
    print("=" * 50)
    print(f"Configs generated: {configs_generated} of {total}")
    print(f"Configs skipped:   {configs_skipped} (already current)")
    print()
    print("SCORES + TONE PROFILES")
    print("-" * 50)
    for pid, cfg in sorted(existing_configs.items(),
                           key=lambda x: x[1].get("dimension_scores", {}).get("overall", 0)):
        score = cfg.get("dimension_scores", {}).get("overall", 0)
        tone  = cfg.get("tone_profile", "?")
        leakage_low = cfg.get("revenue_leakage", {}).get("total_recoverable", {}).get("low", 0)
        bname = cfg.get("name", pid)[:35]
        print(f"  {score}/10  [{tone:<10}]  ${leakage_low:>8,}  {bname}")

    print(f"\nSaved to {out_file}")
    print(f"All configs saved to: {out_file}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--city",    default="seattle_wa")
    parser.add_argument("--force",   action="store_true",
                        help="Re-analyse all regardless of version")
    args = parser.parse_args()
    analyse_businesses(args.city, force=args.force)
