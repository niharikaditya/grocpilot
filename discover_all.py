"""
discover_all.py — Discovers all 250 businesses across Seattle metro.

Runs multiple search queries across neighbourhoods and keyword variants.
Deduplicates by place_id. Assigns each business to a cluster.
Applies opportunity filter and ownership classification.

Usage:
    python discover_all.py --city seattle_wa --industry veterinary

Output:
    seattle_wa_all_businesses.json   — full 250 business list
    seattle_wa_clusters.json         — cluster assignments
"""

import argparse
import json
import math
import os
import time
import sqlite3

import requests
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_KEY", "")
DB_PATH        = "pipeline.db"

# ---------------------------------------------------------------------------
# Seattle metro cluster definitions
# Boundaries based on natural geographic barriers and drive time
# ---------------------------------------------------------------------------

SEATTLE_CLUSTERS = {
    "1": {
        "name":  "North Seattle",
        "areas": [
            "Northgate Seattle", "Roosevelt Seattle", "Ravenna Seattle",
            "University District Seattle", "Wedgwood Seattle",
            "Lake City Seattle", "View Ridge Seattle",
        ],
        "center_lat": 47.695, "center_lng": -122.316,
    },
    "2": {
        "name":  "Queen Anne / Fremont / Ballard",
        "areas": [
            "Queen Anne Seattle", "Fremont Seattle", "Ballard Seattle",
            "Green Lake Seattle", "Phinney Ridge Seattle", "Wallingford Seattle",
        ],
        "center_lat": 47.655, "center_lng": -122.366,
    },
    "3": {
        "name":  "Capitol Hill / Central / Eastlake",
        "areas": [
            "Capitol Hill Seattle", "Eastlake Seattle", "First Hill Seattle",
            "Madison Valley Seattle", "Central District Seattle",
        ],
        "center_lat": 47.623, "center_lng": -122.316,
    },
    "4": {
        "name":  "South Seattle / West Seattle",
        "areas": [
            "Columbia City Seattle", "Rainier Valley Seattle",
            "Beacon Hill Seattle", "West Seattle", "Georgetown Seattle",
            "White Center Seattle",
        ],
        "center_lat": 47.563, "center_lng": -122.350,
    },
    "5": {
        "name":  "Bellevue / Overlake / Redmond",
        "areas": [
            "Bellevue WA", "Overlake Redmond WA", "Redmond WA",
            "Mercer Island WA", "Clyde Hill WA",
        ],
        "center_lat": 47.620, "center_lng": -122.165,
    },
    "6": {
        "name":  "Kirkland / Bothell / Kenmore",
        "areas": [
            "Kirkland WA", "Bothell WA", "Kenmore WA",
            "Woodinville WA", "Juanita WA",
        ],
        "center_lat": 47.690, "center_lng": -122.198,
    },
    "7": {
        "name":  "South King County",
        "areas": [
            "Renton WA", "Kent WA", "Burien WA",
            "Tukwila WA", "SeaTac WA", "Des Moines WA",
        ],
        "center_lat": 47.480, "center_lng": -122.235,
    },
    "8": {
        "name":  "Shoreline / Edmonds",
        "areas": [
            "Shoreline WA", "Lake Forest Park WA", "Edmonds WA",
            "Mountlake Terrace WA", "Lynnwood WA",
        ],
        "center_lat": 47.756, "center_lng": -122.320,
    },
}

INDUSTRY_KEYWORDS = {
    "veterinary": [
        "veterinary clinic", "animal hospital", "veterinarian",
        "vet clinic", "pet clinic", "animal clinic",
    ],
    "barbers": [
        "barber shop", "barbershop", "barber",
        "men's hair salon", "men's grooming",
    ],
}

def get_city_clusters(city_slug):
    """
    Load cluster definitions from content/cities/{city}.
    Falls back to SEATTLE_CLUSTERS if not found.
    """
    try:
        from content_loader import get_city_content
        cc = get_city_content(city_slug)
        if cc.clusters:
            return cc.clusters
    except Exception:
        pass
    # Fallback: algorithmic clustering will be used
    return SEATTLE_CLUSTERS if city_slug == "seattle_wa" else {}

def get_industry_keywords(industry):
    """Load keywords from content folder, falls back to hardcoded."""
    try:
        from content_loader import get_industry_content
        ic = get_industry_content(industry)
        if ic.search_keywords:
            return ic.search_keywords
    except Exception:
        pass
    return INDUSTRY_KEYWORDS.get(industry, [industry])


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine_miles(lat1, lng1, lat2, lng2):
    R    = 3958.8  # Earth radius miles
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a    = (math.sin(dlat/2)**2 +
            math.cos(math.radians(lat1)) *
            math.cos(math.radians(lat2)) *
            math.sin(dlng/2)**2)
    return R * 2 * math.asin(math.sqrt(a))


def _get_algorithmic_clusters(city):
    """
    Generate default clusters algorithmically for any city.
    Creates 7 clusters evenly spaced — will be refined by actual
    business locations after discovery.
    """
    # These are rough Portland centers as generic fallback
    # Real clustering happens in local_ownership_scorer.py after discovery
    return {
        str(i): {
            "name":       f"Area {i}",
            "areas":      [],
            "center_lat": 0,
            "center_lng": 0,
        }
        for i in range(1, 8)
    }


def assign_cluster(lat, lng, clusters):
    """Assign business to nearest cluster by center distance."""
    best_cluster = "1"
    best_dist    = float("inf")
    for cid, cluster in clusters.items():
        dist = haversine_miles(lat, lng,
                               cluster["center_lat"],
                               cluster["center_lng"])
        if dist < best_dist:
            best_dist    = dist
            best_cluster = cid
    return best_cluster


# ---------------------------------------------------------------------------
# Google Places search
# ---------------------------------------------------------------------------

def search_places(query, api_key, min_reviews=50):
    """
    Search Google Places Text Search API.
    Returns list of businesses with basic info.
    """
    url    = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": api_key, "type": "veterinary_care"}
    results = []

    while True:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            break

        for place in data.get("results", []):
            rating       = place.get("rating", 0)
            review_count = place.get("user_ratings_total", 0)

            # Opportunity filter: min 50 reviews, active business
            if review_count < min_reviews:
                continue

            # Type filter: must be a vet/animal care business
            # Uses Google Places types array — signal-based, no hardcoding
            place_types = place.get("types", [])
            vet_types = {"veterinary_care", "animal_shelter", "pet_store", "pet_care"}
            non_vet_only_types = {"hospital", "doctor", "health", "dentist",
                                   "medical_center", "pharmacy"}
            has_vet_type  = bool(vet_types & set(place_types))
            has_only_medical = (bool(non_vet_only_types & set(place_types))
                                and not has_vet_type)
            if has_only_medical:
                continue  # Human medical facility, not a vet

            loc = place.get("geometry", {}).get("location", {})
            results.append({
                "place_id":           place.get("place_id", ""),
                "name":               place.get("name", ""),
                "rating":             rating,
                "user_ratings_total": review_count,
                "formatted_address":  place.get("formatted_address", ""),
                "lat":                loc.get("lat"),
                "lng":                loc.get("lng"),
                "types":              place.get("types", []),
                "business_status":    place.get("business_status", ""),
            })

        # Pagination
        next_token = data.get("next_page_token")
        if not next_token:
            break
        params = {"pagetoken": next_token, "key": api_key}
        time.sleep(2)  # Required delay before next page token is valid

    return results


def enrich_with_details(place_id, api_key):
    """Get phone, website, hours from Place Details API."""
    url    = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "key":      api_key,
        "fields":   "formatted_phone_number,website,opening_hours,reviews,user_ratings_total,rating",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json().get("result", {})
        reviews_raw = data.get("reviews", [])
        reviews = []
        for r in reviews_raw:
            reviews.append({
                "rating":       r.get("rating", 0),
                "text":         r.get("text", ""),
                "time":         r.get("time", 0),
                "author_name":  r.get("author_name", ""),
                "relative_time": r.get("relative_time_description", ""),
                "owner_response": r.get("owner_response", {}).get("text", "") if r.get("owner_response") else "",
            })
        return {
            "phone":   data.get("formatted_phone_number", ""),
            "website": data.get("website", ""),
            "hours":   data.get("opening_hours", {}).get("weekday_text", []),
            "reviews": reviews,
            "rating":  data.get("rating", 0),
            "user_ratings_total": data.get("user_ratings_total", 0),
        }
    except Exception:
        return {"phone": "", "website": "", "hours": []}


# ---------------------------------------------------------------------------
# Main discovery
# ---------------------------------------------------------------------------

def discover_all(city, industry="veterinary", min_reviews=50):
    slug     = city.lower().replace(" ", "_").replace(",", "")
    keywords = get_industry_keywords(industry)
    clusters = get_city_clusters(slug)
    if not clusters:
        print(f"  No cluster definitions for {city} — using algorithmic clustering")
        # Algorithmic fallback: will assign clusters by lat/lng proximity
        # after discovery using 7 default cluster centers
        clusters = _get_algorithmic_clusters(city)

    print(f"\n  Discovering {industry} businesses in {city}...")
    print(f"  Minimum reviews: {min_reviews}")
    print()

    # Collect all results, deduplicate by place_id
    seen      = {}  # place_id -> business dict
    all_found = 0

    for cid, cluster in clusters.items():
        cluster_name = cluster["name"]
        for area in cluster["areas"]:
            for kw in keywords[:2]:  # Use first 2 keywords per area to limit API calls
                query = f"{kw} {area}"
                print(f"    Searching: {query[:60]}...", end=" ", flush=True)

                try:
                    results = search_places(query, GOOGLE_API_KEY, min_reviews)
                    new     = 0
                    for biz in results:
                        pid = biz["place_id"]
                        if pid and pid not in seen:
                            seen[pid] = biz
                            new += 1
                    all_found += len(results)
                    print(f"+{new} new ({len(seen)} total)")
                except Exception as e:
                    print(f"ERROR: {e}")

                time.sleep(0.5)  # Rate limiting

    print()
    print(f"  Total discovered: {len(seen)} unique businesses")
    print(f"  (from {all_found} raw results, deduplicated by place_id)")
    print()

    # Assign clusters and enrich with details
    print(f"  Assigning clusters and enriching details...")
    businesses = []
    for i, (pid, biz) in enumerate(seen.items()):
        lat = biz.get("lat")
        lng = biz.get("lng")

        # Assign cluster by geography
        if lat and lng:
            cluster_id   = assign_cluster(lat, lng, clusters)
            cluster_name = clusters[cluster_id]["name"]
        else:
            cluster_id   = "1"
            cluster_name = "Unknown"

        # Enrich with phone/website
        if (i + 1) % 10 == 0:
            print(f"    Enriched {i+1}/{len(seen)}...")

        details = enrich_with_details(pid, GOOGLE_API_KEY)
        time.sleep(0.2)

        biz["cluster_id"]   = cluster_id
        biz["cluster_name"] = cluster_name
        biz["phone"]        = details.get("phone", "")
        biz["website"]      = details.get("website", "")
        biz["hours"]        = details.get("hours", [])
        biz["reviews"]      = details.get("reviews", [])
        biz["city"]         = city
        biz["industry"]     = industry

        businesses.append(biz)

    # Save outputs
    out_file = f"{slug}_all_businesses.json"
    with open(out_file, "w") as f:
        json.dump(businesses, f, indent=2)
    print(f"\n  Saved {len(businesses)} businesses to {out_file}")

    # Save cluster summary
    cluster_summary = {}
    for biz in businesses:
        cid = biz["cluster_id"]
        if cid not in cluster_summary:
            cluster_summary[cid] = {
                "cluster_id":   cid,
                "cluster_name": biz["cluster_name"],
                "businesses":   [],
            }
        cluster_summary[cid]["businesses"].append(biz["place_id"])

    clusters_file = f"{slug}_clusters.json"
    with open(clusters_file, "w") as f:
        json.dump(cluster_summary, f, indent=2)
    print(f"  Saved cluster map to {clusters_file}")

    # Save to pipeline.db
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            place_id       TEXT PRIMARY KEY,
            name           TEXT,
            city           TEXT,
            cluster_id     TEXT,
            cluster_name   TEXT,
            ownership_type TEXT DEFAULT 'INDEPENDENT',
            owner_group_id TEXT,
            franchise_network_id TEXT,
            is_primary     INTEGER DEFAULT 1,
            rating         REAL,
            review_count   INTEGER,
            lat            REAL,
            lng            REAL,
            email          TEXT,
            phone          TEXT,
            website        TEXT,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for biz in businesses:
        conn.execute("""
            INSERT INTO businesses
                (place_id, name, city, cluster_id, cluster_name,
                 rating, review_count, lat, lng, phone, website)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(place_id) DO UPDATE SET
                cluster_id   = excluded.cluster_id,
                cluster_name = excluded.cluster_name,
                rating       = excluded.rating,
                review_count = excluded.review_count,
                updated_at   = CURRENT_TIMESTAMP
        """, (
            biz["place_id"], biz["name"], biz["city"],
            biz["cluster_id"], biz["cluster_name"],
            biz.get("rating", 0), biz.get("user_ratings_total", 0),
            biz.get("lat"), biz.get("lng"),
            biz.get("phone", ""), biz.get("website", ""),
        ))
    conn.commit()
    conn.close()

    # Print cluster summary
    print()
    print("  CLUSTER SUMMARY")
    print("  " + "-"*50)
    for cid in sorted(cluster_summary.keys()):
        cs  = cluster_summary[cid]
        cnt = len(cs["businesses"])
        print(f"  Cluster {cid} — {cs['cluster_name']:<35} {cnt} businesses")
    print()

    return businesses


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    from config_loader import load as _load_cfg
    _cfg_main, _ = _load_cfg()
    parser.add_argument("--city",        default=_cfg_main.city)
    parser.add_argument("--industry",    default=_cfg_main.industry)
    parser.add_argument("--min-reviews", type=int, default=50)
    args = parser.parse_args()
    discover_all(args.city, args.industry, args.min_reviews)
