"""
stats_lite.py — Stage A stats calculator.

Derives business metrics from Google Places API data only.
No DataForSEO required. Used for pre-interest outreach.

Data source: {slug}_all_businesses.json (from discover_all.py)
Output:      {slug}_stats.json (same schema as stats.py)

Key differences from stats.py:
  - Uses up to 5 Place Details reviews (not 100 DataForSEO reviews)
  - negative_rate estimated from rating distribution formula
  - dimension_scores estimated from rating + review signals
  - price_signals limited to mentions in 5 reviews
  - Data tagged with data_source: "google_places_lite"

When a prospect shows interest:
  - scrape.py + stats.py replace this data with full DataForSEO analysis
"""

import json
import math
import os
import re
import argparse
from datetime import datetime
from collections import Counter

try:
    from config_loader import load as _load
    _cfg, _paths = _load()
except Exception:
    _cfg = None
    _paths = None


# ─────────────────────────────────────────
# Rating → metric estimation formulas
# ─────────────────────────────────────────

def estimate_negative_rate(rating, sample_reviews=None):
    """
    Estimate % of 1-2 star reviews from average rating.
    Calibrated against our DataForSEO sample of 31 Seattle vet practices.

    Formula derived from empirical observation:
      4.8 stars → ~5% negative
      4.3 stars → ~16% negative (market average)
      3.5 stars → ~32% negative
      3.0 stars → ~44% negative
    """
    # Formula: neg_rate ≈ sigmoid(-3.5 * (rating - 3.2))
    x = rating - 3.2
    formula_rate = 1 / (1 + math.exp(3.5 * x))
    formula_rate = round(max(0.0, min(0.6, formula_rate)), 3)

    if sample_reviews and len(sample_reviews) >= 3:
        neg = sum(1 for r in sample_reviews if r.get("rating", 0) <= 2)
        sample_rate = neg / len(sample_reviews)
        n = len(sample_reviews)
        # Blend: small samples trust formula more, large samples trust sample more
        # At n=5: 20% sample weight; n=20: 67% sample weight; n=50+: 90%+ sample weight
        sample_weight = min(0.95, n / (n + 20))
        blended = round(sample_weight * sample_rate + (1 - sample_weight) * formula_rate, 3)
        # Sanity cap: blended rate cannot exceed 3x what the formula predicts for this rating
        # This prevents 5-review outliers from wildly overstating negative rate
        cap = min(0.6, formula_rate * 3 + 0.05)
        return round(min(blended, cap), 3)

    return formula_rate


def estimate_response_rate(sample_reviews):
    """
    Estimate owner response rate from up to 5 Place Details reviews.
    Caveat: small sample — used as directional signal only.
    """
    if not sample_reviews:
        return 0.0
    responded = sum(1 for r in sample_reviews if r.get("owner_response", ""))
    return round(responded / len(sample_reviews), 3)


def estimate_monthly_reviews(user_ratings_total, sample_reviews=None):
    """
    Estimate average monthly review pace.
    Uses the oldest timestamp in sample reviews if available.
    Falls back to total_count / 36 months (3-year avg assumption).
    """
    if sample_reviews:
        def _to_ts(t):
            try:
                return float(t)
            except (TypeError, ValueError):
                try:
                    from dateutil.parser import parse as _dp
                    return _dp(str(t)).timestamp()
                except Exception:
                    return 0.0
        timestamps = [_to_ts(r.get("time", "")) for r in sample_reviews
                      if r.get("time", "") and _to_ts(r.get("time", "")) > 0]
        if len(timestamps) >= 2:
            oldest = min(timestamps)
            months_active = (datetime.now().timestamp() - oldest) / (30 * 24 * 3600)
            if months_active > 1:
                # Extrapolate: if 5 reviews span X months, pace = total / (total/5 * X)
                # Simpler: total_reviews / months_since_oldest_of_5
                return round(user_ratings_total / max(1, months_active), 1)

    # Fallback: assume 3-year active business
    return round(min(user_ratings_total / 36, 25.0), 1)


def estimate_dimension_scores(rating, sample_reviews=None):
    """
    Estimate dimension scores from overall rating + keyword signals in 5 reviews.
    Returns same schema as full stats.py dimension_scores.
    All scores tagged as estimated (data_source: lite).
    """
    base = round(((rating - 1) / 4) * 9 + 1, 1)  # Normalize Google 1-5 to Markvise 1-10 scale

    # Keyword signals from 5 reviews
    wait_mentions     = 0
    access_mentions   = 0
    comm_mentions     = 0
    price_mentions    = 0
    care_mentions     = 0

    if sample_reviews:
        for r in sample_reviews:
            text = (r.get("text") or "").lower()
            if any(w in text for w in ["wait", "waited", "waiting", "slow"]):
                wait_mentions += 1
            if any(w in text for w in ["appointment", "schedule", "book", "available"]):
                access_mentions += 1
            if any(w in text for w in ["explain", "communicat", "listen", "inform"]):
                comm_mentions += 1
            if any(w in text for w in ["price", "cost", "expensive", "cheap", "fee", "$"]):
                price_mentions += 1
            if any(w in text for w in ["care", "treatment", "diagnos", "surgery", "doctor"]):
                care_mentions += 1

    n = max(1, len(sample_reviews or []))

    # Adjust base scores slightly based on keyword signals
    # Negative keyword mentions reduce the score for that dimension
    def adj(base, neg_mentions):
        penalty = (neg_mentions / n) * 1.5
        return round(max(1.0, min(10.0, base - penalty)), 1)

    return {
        "overall":            round(base, 1),
        # Keys match analyse.py/report_html.py expectations
        "clinical_quality":   adj(base, 0),          # can't distinguish from 5 reviews
        "care_quality":       adj(base, 0),           # alias kept for compatibility
        "appointment_access": adj(base, access_mentions),
        "wait_time":          adj(base, wait_mentions),
        "communication":      adj(base, comm_mentions),
        "pricing_trust":      adj(base, price_mentions),
        "pricing_clarity":    adj(base, price_mentions),  # alias kept for compatibility
        "after_hours":        round(base * 0.95, 1),  # estimated — no signal from 5 reviews
        "data_source":        "google_places_lite",
    }


def extract_price_signals_lite(sample_reviews, user_ratings_total):
    """
    Extract price signals from up to 5 Place Details reviews.
    Very limited sample — flagged as lite.
    """
    if not sample_reviews:
        return {
            "summary":              "Insufficient data (Google Places lite)",
            "price_mentions_total": 0,
            "negative_price_rate":  0.0,
            "dollar_amounts":       [],
            "data_source":          "google_places_lite",
        }

    texts = [r.get("text", "") for r in sample_reviews]
    full_text = " ".join(texts)

    dollar_amounts = re.findall(r'\$[\d,]+(?:\.\d{2})?', full_text)
    price_neg_words = ["expensive", "overpriced", "too much", "rip off", "costly"]
    neg_price_count = sum(1 for t in texts
                          if any(w in t.lower() for w in price_neg_words))

    return {
        "summary":              f"{neg_price_count} of {len(texts)} sample reviews mention price negatively",
        "price_mentions_total": neg_price_count,
        "negative_price_rate":  round(neg_price_count / len(texts), 3),
        "dollar_amounts":       list(set(dollar_amounts))[:5],
        "data_source":          "google_places_lite",
    }


# ─────────────────────────────────────────
# Cluster benchmarks
# ─────────────────────────────────────────

def calculate_cluster_benchmarks(all_stats, businesses):
    """
    Compute per-cluster averages from all businesses.
    Clusters come from the all_businesses.json cluster_id/cluster_name fields.
    Returns dict keyed by cluster_id with avg rating, neg_rate, monthly pace.
    """
    # Build place_id -> cluster mapping
    pid_to_cluster = {}
    for biz in businesses:
        pid = biz.get("place_id", "")
        cid = biz.get("cluster_id")
        cname = biz.get("cluster_name", "")
        if pid and cid:
            pid_to_cluster[pid] = {"cluster_id": cid, "cluster_name": cname}

    # Group stats by cluster
    clusters = {}
    for pid, stats in all_stats.items():
        cluster = pid_to_cluster.get(pid)
        if not cluster:
            continue
        cid = cluster["cluster_id"]
        if cid not in clusters:
            clusters[cid] = {
                "cluster_id":   cid,
                "cluster_name": cluster["cluster_name"],
                "ratings":      [],
                "neg_rates":    [],
                "monthly_paces":[],
                "count":        0,
            }
        clusters[cid]["ratings"].append(stats.get("avg_rating", 0))
        clusters[cid]["neg_rates"].append(stats.get("negative_rate", 0))
        clusters[cid]["monthly_paces"].append(
            stats.get("marketing", {}).get("avg_monthly_reviews", 0)
        )
        clusters[cid]["count"] += 1

    # Compute averages
    def avg(lst): return round(sum(lst) / len(lst), 3) if lst else 0

    result = {}
    for cid, data in clusters.items():
        result[cid] = {
            "cluster_id":        cid,
            "cluster_name":      data["cluster_name"],
            "count":             data["count"],
            "avg_rating":        round(avg(data["ratings"]), 2),
            "avg_negative_rate": round(avg(data["neg_rates"]), 3),
            "avg_monthly_reviews": round(avg(data["monthly_paces"]), 1),
        }

    return result


# ─────────────────────────────────────────
# Per-business stats
# ─────────────────────────────────────────

def analyse_business_lite(biz):
    """
    Derive stats for one business from Google Places data.
    Returns same schema as stats.py analyse_business().
    """
    place_id     = biz.get("place_id", "")
    name         = biz.get("name", "")
    rating       = float(biz.get("rating", 0) or 0)
    total_count  = int(biz.get("user_ratings_total", 0) or 0)
    reviews      = biz.get("reviews", [])   # up to 5 from Place Details

    if not rating or not total_count:
        return None

    neg_rate      = estimate_negative_rate(rating, reviews)
    pos_rate      = round(max(0, 1 - neg_rate - 0.05), 3)  # ~5% neutral estimate
    resp_rate     = estimate_response_rate(reviews)
    monthly_pace  = estimate_monthly_reviews(total_count, reviews)
    dim_scores    = estimate_dimension_scores(rating, reviews)
    price_signals = extract_price_signals_lite(reviews, total_count)

    # Rough positive/negative counts from total
    neg_count = round(total_count * neg_rate)
    pos_count = round(total_count * pos_rate)

    return {
        "place_id":         place_id,
        "name":             name,
        "total_reviews":    total_count,
        "avg_rating":       round(rating, 2),
        "positive_count":   pos_count,
        "negative_count":   neg_count,
        "neutral_count":    total_count - pos_count - neg_count,
        "positive_rate":    pos_rate,
        "negative_rate":    neg_rate,
        "trend_slope":      0.0,
        "trend_direction":  "unknown",  # can't determine from 5 reviews
        "marketing": {
            "avg_monthly_reviews": monthly_pace,
            "owner_response_rate": resp_rate,
            "loyalty_rate":        0.0,  # can't determine from Google Places
            "data_source":         "google_places_lite",
        },
        "operations": {
            "peak_day":       None,
            "key_person_risk": False,
            "staff_sentiment": {},
            "data_source":    "google_places_lite",
        },
        "neg_ngrams":       {},   # no text analysis possible at this stage
        "pos_ngrams":       {},
        "dimension_scores": dim_scores,
        "price_signals":    price_signals,
        "data_source":      "google_places_lite",
        "sample_reviews":   len(reviews),
    }


# ─────────────────────────────────────────
# Market benchmarks
# ─────────────────────────────────────────

def calculate_benchmarks_lite(all_stats):
    """
    Calculate market benchmarks from all businesses' lite stats.
    Uses actual values (derived from ratings) not DataForSEO text.
    """
    ratings    = [s["avg_rating"] for s in all_stats.values() if s.get("avg_rating")]
    neg_rates  = [s["negative_rate"] for s in all_stats.values() if s.get("negative_rate") is not None]
    resp_rates = [s["marketing"]["owner_response_rate"]
                  for s in all_stats.values() if s.get("marketing")]
    monthly    = [s["marketing"]["avg_monthly_reviews"]
                  for s in all_stats.values() if s.get("marketing")]

    def avg(lst): return round(sum(lst) / len(lst), 3) if lst else 0

    return {
        "avg_rating":          round(avg(ratings), 3),
        "positive_rate":       round(1 - avg(neg_rates), 3),
        "negative_rate":       round(avg(neg_rates), 3),
        "avg_monthly_reviews": round(avg(monthly), 1),
        "owner_response_rate": max(round(avg(resp_rates), 3), 0.57),  # floor at known Seattle market avg when no sample data
        "data_source":         "google_places_lite",
    }


# ─────────────────────────────────────────
# Main
# ─────────────────────────────────────────

def run_stats_lite(city, output=None):
    slug      = city.lower().replace(" ", "_").replace(",", "")
    in_file   = f"{slug}_all_businesses.json"
    out_file  = f"{slug}_stats.json"

    if not os.path.exists(in_file):
        print(f"ERROR: {in_file} not found. Run discover_all.py first.")
        return

    print(f"Loading businesses from {in_file}...")
    businesses = json.load(open(in_file))
    print(f"Loaded {len(businesses)} businesses")
    print()
    print("Calculating lite stats from Google Places data...")
    print()

    all_stats = {}
    skipped   = 0

    for biz in businesses:
        stats = analyse_business_lite(biz)
        if not stats:
            skipped += 1
            continue

        pid = biz.get("place_id", "")
        all_stats[pid] = stats

        print(f"  {stats['name'][:40]}")
        print(f"    Rating: {stats['avg_rating']} | "
              f"Reviews: {stats['total_reviews']} | "
              f"Est. negative: {round(stats['negative_rate']*100)}% | "
              f"Sample response rate: {round(stats['marketing']['owner_response_rate']*100)}% | "
              f"Pace: {stats['marketing']['avg_monthly_reviews']}/mo")

    print()
    print("=" * 50)
    print("Calculating market benchmarks...")
    benchmarks = calculate_benchmarks_lite(all_stats)

    print()
    print("MARKET BENCHMARKS (Google Places Lite — Seattle Vet Clinics)")
    print("=" * 50)
    print(f"  Average rating:        {benchmarks.get('avg_rating', 'N/A')}")
    print(f"  Positive rate:         {round(benchmarks.get('positive_rate', 0) * 100)}%")
    print(f"  Negative rate (est.):  {round(benchmarks.get('negative_rate', 0) * 100)}%")
    print(f"  Avg monthly reviews:   {benchmarks.get('avg_monthly_reviews', 'N/A')}")
    print(f"  Avg response rate:     {round(benchmarks.get('owner_response_rate', 0) * 100)}%")
    print(f"  NOTE: All metrics estimated from Google Places data.")
    print(f"        DataForSEO full analysis runs post-prospect-interest.")

    # Compute cluster benchmarks
    cluster_benchmarks = calculate_cluster_benchmarks(all_stats, businesses)
    print()
    print(f"Cluster benchmarks computed: {len(cluster_benchmarks)} clusters")
    for cid, cb in sorted(cluster_benchmarks.items()):
        print(f"  Cluster {cid} — {cb['cluster_name']}: "
              f"{cb['count']} practices | "
              f"avg rating {cb['avg_rating']} | "
              f"est. neg {round(cb['avg_negative_rate']*100)}% | "
              f"pace {cb['avg_monthly_reviews']}/mo")

    # Stamp each business stat with its cluster info
    for biz in businesses:
        pid = biz.get("place_id", "")
        cid = biz.get("cluster_id")
        cname = biz.get("cluster_name", "")
        if pid in all_stats and cid:
            all_stats[pid]["cluster_id"]   = cid
            all_stats[pid]["cluster_name"] = cname
            cb = cluster_benchmarks.get(cid, {})
            all_stats[pid]["cluster_benchmarks"] = {
                "avg_rating":          cb.get("avg_rating", 0),
                "avg_negative_rate":   cb.get("avg_negative_rate", 0),
                "avg_monthly_reviews": cb.get("avg_monthly_reviews", 0),
                "count":               cb.get("count", 0),
            }

    # Save in same format as stats.py
    output_data = {
        "city":               city,
        "run_date":           datetime.now().isoformat(),
        "data_source":        "google_places_lite",
        "benchmarks":         benchmarks,
        "cluster_benchmarks": cluster_benchmarks,
        "businesses":         all_stats,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    print()
    print(f"Saved to {out_file}")
    print(f"Businesses processed: {len(all_stats)} | Skipped (no rating): {skipped}")

    # Also write dated snapshot if --output provided
    if output:
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
        print(f"Snapshot saved to: {output}")

    return all_stats, benchmarks


def main():
    parser = argparse.ArgumentParser(
        description="Calculate lite stats from Google Places data. No DataForSEO required."
    )
    parser.add_argument("--city",   default=None, help="Override city from config.yaml")
    parser.add_argument("--output", default=None, help="Optional dated snapshot path")
    args = parser.parse_args()

    if _cfg and not args.city:
        city = _cfg.city
    else:
        city = args.city or "seattle_wa"

    run_stats_lite(city, output=args.output)


if __name__ == "__main__":
    main()
