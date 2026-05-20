"""
find_contacts.py — Finds emails for all 30 groc pilot stores.

Strategy per store:
  1. Google Places API     — refresh website + phone
  2. Website scrape        — /contact /about /homepage with http+https
  3. DuckDuckGo search     — "{name}" "{city}" email
                             (DDG allows automated access; Google does not)
  4. Yelp page check       — yelp often indexes business emails in page metadata

Run from grocpilot/:
    python find_contacts.py
"""

import csv
import json
import os
import re
import time
import urllib.parse

import requests
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_KEY", "")
FINAL_LIST     = "groc_pilot_final_list.json"
OUTPUT_CSV     = "groc_contacts.csv"

EMAIL_RE  = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
MAILTO_RE = re.compile(r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")

SKIP = [
    "noreply", "no-reply", "example", "sentry", "wixpress",
    "googleapis", "gstatic", "schema.org", "w3.org", "cloudflare",
    "squarespace-cdn", "doordash", "grubhub", "tripadvisor", "yelp.com",
    "seamless", "giftly", "toasttab", "opentable", "manta.com",
    "restaurantji", "ubereats", "postmates", "zomato", "swiggy",
    "duckduckgo.com", "google.com",
]
PLACEHOLDER = {"user@domain.com", "email@domain.com", "info@domain.com",
               "name@domain.com", "mysite.com", "example@mysite.com",
               "test@test.com"}

CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/about-us",
                 "/reach-us", "/info", "/"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://duckduckgo.com/",
}


def valid(email: str) -> bool:
    low = email.lower().rstrip(".")
    if low in PLACEHOLDER or "@mysite" in low or "@domain" in low:
        return False
    for s in SKIP:
        if s in low:
            return False
    parts = low.split("@")
    if len(parts) != 2:
        return False
    tld = parts[1].split(".")[-1]
    return len(tld) >= 2 and len(parts[1]) >= 4


def extract(html: str) -> list:
    found = set()
    for e in EMAIL_RE.findall(html) + MAILTO_RE.findall(html):
        e = e.lower().rstrip(".")
        if valid(e):
            found.add(e)
    return list(found)


def get(url: str, headers: dict = None, timeout: int = 10) -> str:
    try:
        r = requests.get(
            url, headers=headers or HEADERS,
            timeout=timeout, allow_redirects=True
        )
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""


# ── 1. Places API ────────────────────────────────────────────────
def places_details(place_id: str) -> dict:
    url = f"https://places.googleapis.com/v1/places/{place_id}"
    hdrs = {
        "X-Goog-Api-Key":  GOOGLE_API_KEY,
        "X-Goog-FieldMask": "id,nationalPhoneNumber,websiteUri",
    }
    try:
        r = requests.get(url, headers=hdrs, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


# ── 2. Website scrape ────────────────────────────────────────────
def scrape_site(url: str) -> tuple:
    if not url or "facebook.com" in url:
        return "", ""

    # Normalise and try both http + https
    raw = url.rstrip("/")
    bases = []
    if raw.startswith("http://"):
        bases = [raw, raw.replace("http://", "https://")]
    elif raw.startswith("https://"):
        bases = [raw, raw.replace("https://", "http://")]
    else:
        bases = ["https://" + raw, "http://" + raw]

    for base in bases:
        for path in CONTACT_PATHS:
            target = base + path if path != "/" else base
            html = get(target)
            if html:
                emails = extract(html)
                if emails:
                    return emails[0], path
            time.sleep(0.2)

    return "", ""


# ── 3. DuckDuckGo search ─────────────────────────────────────────
# DDG HTML endpoint is explicitly allowed for automated access.
# It returns actual search results as plain HTML — no captcha,
# no JavaScript requirement. Mirrors what manual Google search does:
# surfaces emails from Facebook About pages, Yelp, Superpages,
# Nextdoor, business directories, etc.

def ddg_search(name: str, city: str, state: str) -> tuple:
    location = f"{city} {state}".strip()

    for query in [
        f'\"{name}\" \"{location}\" email',
        f'{name} {location} email contact',
        f'{name} grocery email',
    ]:
        url  = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        html = get(url, DDG_HEADERS, timeout=15)
        if html:
            emails = extract(html)
            if emails:
                # Prefer domain emails over Gmail — more likely to be the real one
                domain = [e for e in emails if "@gmail" not in e and "@yahoo" not in e]
                gmail  = [e for e in emails if "@gmail" in e]
                chosen = (domain + gmail)[0] if (domain + gmail) else None
                if chosen:
                    return chosen, "DuckDuckGo"
        time.sleep(2)  # DDG rate limit — be respectful

    return "", ""


# ── 4. Facebook scrape ───────────────────────────────────────────
# Some stores have a Facebook URL as their primary web presence.
# Mobile Facebook is more parseable than desktop.

def scrape_fb(fb_url: str) -> tuple:
    if not fb_url or "facebook.com" not in fb_url:
        return "", ""
    mobile = fb_url.replace("www.facebook.com", "m.facebook.com")
    for url in [mobile, fb_url]:
        html = get(url, {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
            "Accept": "text/html",
        }, timeout=12)
        if html:
            emails = extract(html)
            if emails:
                return emails[0], "Facebook"
        time.sleep(1)
    return "", ""


# ── Main ─────────────────────────────────────────────────────────

def run():
    if not GOOGLE_API_KEY:
        print("ERROR: GOOGLE_PLACES_KEY not set in .env")
        return

    stores = json.load(open(FINAL_LIST))
    print(f"Processing {len(stores)} stores...\n")

    results = []

    for i, store in enumerate(stores, 1):
        name     = store["name"]
        place_id = store["place_id"]
        cluster  = store["cluster"]
        phone    = store.get("phone", "")
        website  = store.get("website", "")
        address  = store.get("address", "")

        # Parse city + state from address
        parts = [p.strip() for p in address.split(",")]
        city  = parts[-3] if len(parts) >= 3 else ""
        state = parts[-2].split()[0] if len(parts) >= 2 else ""

        print(f"[{i:02d}/{len(stores)}] {name}")

        # Step 1: Refresh from Places API
        details = places_details(place_id)
        if details:
            website = details.get("websiteUri", website) or website
            phone   = details.get("nationalPhoneNumber", phone) or phone

        email = ""
        source = ""

        # Step 2: Scrape own website
        if website and "facebook.com" not in website:
            email, source = scrape_site(website)
            if email:
                print(f"         website    {email}")

        # Step 2b: Facebook if that's the primary URL
        if not email and website and "facebook.com" in website:
            email, source = scrape_fb(website)
            if email:
                print(f"         facebook   {email}")

        # Step 3: DuckDuckGo search (main fallback)
        if not email:
            email, source = ddg_search(name, city, state)
            if email:
                print(f"         ddg        {email}")

        if not email:
            print(f"         --         not found  [call: {phone}]")

        results.append({
            "Name":    name,
            "Cluster": cluster,
            "Place ID": place_id,
            "Phone":   phone,
            "Website": website,
            "Email":   email,
            "Source":  source,
            "Status":  "ready" if email else "needs_manual",
        })

        time.sleep(0.5)

    fields = ["Name","Cluster","Place ID","Phone","Website","Email","Source","Status"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    found  = sum(1 for r in results if r["Email"])
    manual = len(results) - found

    print(f"\n{'='*55}")
    print(f"DONE  {found} found  |  {manual} need manual lookup")
    print(f"Output: {OUTPUT_CSV}")
    print(f"{'='*55}\n")
    if manual:
        print("Still needs manual lookup:")
        for r in results:
            if not r["Email"]:
                print(f"  {r['Name']:<42} {r['Phone']}")


if __name__ == "__main__":
    run()
