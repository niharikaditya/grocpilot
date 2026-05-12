"""
content_loader.py — Loads industry-specific content from the content/ folder.

This is the single interface between all pipeline scripts and
industry-specific content. Scripts never hardcode industry language.

Usage:
    from content_loader import get_industry_content, get_city_content

    ic = get_industry_content("veterinary")
    ic.label           # "independent veterinary practice"
    ic.plural          # "veterinary practices"
    ic.analyse_prompt  # full Claude prompt for analyse.py
    ic.email_1         # Email 1 template string
    ic.stopwords       # set of stopwords for stats.py
    ic.dimension_labels # dict of dimension name -> display label

    cc = get_city_content("portland_or")
    cc.clusters        # dict of cluster definitions
    cc.keywords        # list of search keywords for discovery
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "content")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IndustryContent:
    industry:         str
    label:            str   # "independent veterinary practice"
    plural:           str   # "veterinary practices"
    short:            str   # "vet clinic"
    owner_title:      str   # "practice owner"
    client_term:      str   # "patient" / "customer" / "client"
    service_term:     str   # "appointment" / "job" / "visit"
    review_context:   str   # phrase for review analysis context

    dimension_labels: Dict[str, str] = field(default_factory=dict)
    search_keywords:  List[str]      = field(default_factory=list)
    stopwords:        set            = field(default_factory=set)

    analyse_prompt:   str = ""
    brief_prompt:     str = ""
    email_1:          str = ""
    email_1b:         str = ""
    email_1c:         str = ""
    email_2:          str = ""
    email_3:          str = ""
    email_4:          str = ""
    nudge:            str = ""

    benchmarks:       Dict = field(default_factory=dict)


@dataclass
class CityContent:
    city:         str
    display_name: str  = ""   # e.g. "Seattle"
    metro_name:   str  = ""   # e.g. "Seattle-King County Metro"
    search_label: str  = ""   # e.g. "Seattle metro area WA" — used as Google search context
    clusters:     Dict    = field(default_factory=dict)
    keywords:     List[str] = field(default_factory=list)
    benchmarks:   Dict  = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _read(path, default=""):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return default


def _read_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                pass
    return default or {}


def get_industry_content(industry: str) -> IndustryContent:
    """
    Load all content for a given industry.
    Falls back to veterinary defaults if file missing.
    """
    ind_dir = os.path.join(CONTENT_DIR, industry)

    # Load industry.json — core labels and settings
    meta = _read_json(os.path.join(ind_dir, "industry.json"), {})

    # Load stopwords
    sw_data  = _read_json(os.path.join(ind_dir, "stopwords.json"), {})
    stopwords = set(sw_data.get("stopwords", []))

    # Load prompts and templates
    ic = IndustryContent(
        industry        = industry,
        label           = meta.get("label",         f"independent {industry} business"),
        plural          = meta.get("plural",         f"{industry} businesses"),
        short           = meta.get("short",          industry),
        owner_title     = meta.get("owner_title",    "owner"),
        client_term     = meta.get("client_term",    "customer"),
        service_term    = meta.get("service_term",   "appointment"),
        review_context  = meta.get("review_context", f"{industry} services"),
        dimension_labels = meta.get("dimension_labels", _default_dimension_labels(industry)),
        search_keywords = meta.get("search_keywords", [industry]),
        stopwords       = stopwords,
        benchmarks      = meta.get("benchmarks", {}),
        analyse_prompt  = _read(os.path.join(ind_dir, "analyse_prompt.txt")),
        brief_prompt    = _read(os.path.join(ind_dir, "brief_prompt.txt")),
        email_1         = _read(os.path.join(ind_dir, "email_1_template.txt")),
        email_1b        = _read(os.path.join(ind_dir, "email_1b_template.txt")),
        email_1c        = _read(os.path.join(ind_dir, "email_1c_template.txt")),
        email_2         = _read(os.path.join(ind_dir, "email_2_template.txt")),
        email_3         = _read(os.path.join(ind_dir, "email_3_template.txt")),
        email_4         = _read(os.path.join(ind_dir, "email_4_template.txt")),
        nudge           = _read(os.path.join(ind_dir, "nudge_template.txt")),
    )
    return ic


def get_city_content(city: str) -> CityContent:
    """
    Load city-specific content: clusters and search keywords.
    Falls back to algorithmic clustering if no city file.
    """
    city_dir = os.path.join(CONTENT_DIR, "cities", city)
    meta     = _read_json(os.path.join(city_dir, "city.json"), {})
    bench    = _read_json(
        os.path.join("benchmarks", "cities", f"{city}.json"), {}
    )

    # Derive sensible defaults if not explicitly set in city.json
    raw_display = meta.get("display_name", city.replace("_", " ").title())
    raw_metro   = meta.get("metro_name", raw_display)
    raw_label   = meta.get("search_label", raw_metro)

    return CityContent(
        city         = city,
        display_name = raw_display,
        metro_name   = raw_metro,
        search_label = raw_label,
        clusters     = meta.get("clusters", {}),
        keywords     = meta.get("extra_keywords", []),
        benchmarks   = bench,
    )


def _default_dimension_labels(industry):
    """Generic dimension labels usable for any industry."""
    return {
        "clinical_quality":   "Quality",
        "appointment_access": "Availability",
        "wait_time":          "Timeliness",
        "pricing_trust":      "Pricing",
        "communication":      "Communication",
        "after_hours":        "Responsiveness",
    }


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_template(template: str, variables: dict) -> str:
    """
    Simple {variable} substitution in template strings.
    Used by email generators to fill in business-specific data.
    """
    if not template:
        return ""
    try:
        return template.format(**variables)
    except KeyError as e:
        # Partial render — leave missing variables as-is
        import string
        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result


# ---------------------------------------------------------------------------
# Content folder scaffold creator
# ---------------------------------------------------------------------------

def create_industry_scaffold(industry: str):
    """
    Creates the content folder structure for a new industry.
    Copies veterinary as a starting template.

    Usage:
        python content_loader.py --scaffold barbers
    """
    import shutil

    src = os.path.join(CONTENT_DIR, "veterinary")
    dst = os.path.join(CONTENT_DIR, industry)

    if os.path.exists(dst):
        print(f"  Content folder already exists: {dst}")
        return

    if os.path.exists(src):
        shutil.copytree(src, dst)
        print(f"  Created {dst} (copied from veterinary)")
        print(f"  Edit these files to customise for {industry}:")
        for f in os.listdir(dst):
            print(f"    content/{industry}/{f}")
    else:
        os.makedirs(dst, exist_ok=True)
        print(f"  Created empty {dst}")
        print(f"  Add these files:")
        for f in ["industry.json", "stopwords.json",
                  "analyse_prompt.txt", "brief_prompt.txt",
                  "email_1_template.txt", "email_2_template.txt",
                  "email_3_template.txt", "email_4_template.txt",
                  "email_1b_template.txt", "email_1c_template.txt",
                  "nudge_template.txt"]:
            print(f"    content/{industry}/{f}")


def create_city_scaffold(city: str):
    """Creates the content folder for a new city."""
    city_dir = os.path.join(CONTENT_DIR, "cities", city)
    os.makedirs(city_dir, exist_ok=True)

    city_json = os.path.join(city_dir, "city.json")
    if not os.path.exists(city_json):
        with open(city_json, "w") as f:
            json.dump({
                "city":           city,
                "clusters":       {},
                "extra_keywords": [],
                "_note": "Add cluster definitions here or leave empty for algorithmic clustering"
            }, f, indent=2)
        print(f"  Created {city_json}")
        print(f"  Add cluster definitions or leave empty for auto-clustering")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scaffold",      help="Create content scaffold for industry")
    parser.add_argument("--scaffold-city", help="Create content scaffold for city")
    parser.add_argument("--test",          help="Test loading content for industry")
    args = parser.parse_args()

    if args.scaffold:
        create_industry_scaffold(args.scaffold)
    elif args.scaffold_city:
        create_city_scaffold(args.scaffold_city)
    elif args.test:
        ic = get_industry_content(args.test)
        print(f"\n  Industry: {ic.industry}")
        print(f"  Label:    {ic.label}")
        print(f"  Plural:   {ic.plural}")
        print(f"  Keywords: {ic.search_keywords}")
        print(f"  Stopwords: {len(ic.stopwords)} words")
        print(f"  Analyse prompt: {len(ic.analyse_prompt)} chars")
        print(f"  Email 1 template: {len(ic.email_1)} chars")
    else:
        parser.print_help()
