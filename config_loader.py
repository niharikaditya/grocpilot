"""
config_loader.py — Single source of truth for all pipeline paths.

Every script imports this instead of hardcoding city/industry strings.

Usage:
    from config_loader import cfg, paths

    cfg.city        → "seattle_wa"
    cfg.industry    → "veterinary"
    cfg.run_date    → "2026-03-21"
    cfg.run_type    → "original" or "refresh"

    paths.data()        → data/seattle_wa/veterinary/original/2026-03-21/
    paths.reports()     → reports/seattle_wa/veterinary/original/2026-03-21/
    paths.outreach()    → outreach/seattle_wa/veterinary/original/2026-03-21/
    paths.latest_data() → data/seattle_wa/veterinary/latest/
    paths.data_file("stats.json")
                        → data/seattle_wa/veterinary/original/2026-03-21/stats.json

    paths.benchmarks_industry()
                        → benchmarks/industries/veterinary.json
    paths.benchmarks_city()
                        → benchmarks/cities/seattle_wa.json
"""

import json
import os
import shutil
import sqlite3
from datetime import datetime

# Optional yaml support — falls back to manual parse if not installed
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

CONFIG_FILE = "config.yaml"
DB_PATH     = "pipeline.db"


# ---------------------------------------------------------------------------
# Config reader
# ---------------------------------------------------------------------------

def _read_yaml(path):
    """Read config.yaml — works with or without PyYAML installed."""
    if HAS_YAML:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    # Manual fallback parser for simple key: value yaml
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val:
                    result[key] = val
    return result


class Config:
    """Holds the active city, industry, and run metadata."""

    def __init__(self, override_city=None, override_industry=None,
                 override_mode=None):
        raw = {}
        if os.path.exists(CONFIG_FILE):
            raw = _read_yaml(CONFIG_FILE)

        self.city     = override_city     or raw.get("city", "seattle_wa")
        self.industry = override_industry or raw.get("industry", "veterinary")
        self.reviews_per_biz = int(raw.get("reviews_per_biz", 100))
        self.refresh_days    = int(raw.get("refresh_days", 30))
        self.notify_email    = raw.get("notify_email", "")

        # Determine if this is an original or refresh run
        self.run_date = datetime.now().strftime("%Y-%m-%d")
        self.run_type = override_mode or self._detect_run_type()

    def _detect_run_type(self):
        """
        If original folder exists for this city/industry → refresh.
        Otherwise → original.
        """
        original_path = os.path.join(
            "data", self.city, self.industry, "original"
        )
        if os.path.isdir(original_path) and os.listdir(original_path):
            return "refresh"
        return "original"

    def __repr__(self):
        return (f"Config(city={self.city}, industry={self.industry}, "
                f"run_type={self.run_type}, run_date={self.run_date})")


# ---------------------------------------------------------------------------
# Path builder
# ---------------------------------------------------------------------------

class PathBuilder:
    """Builds all file paths for the active config."""

    def __init__(self, config):
        self.cfg = config

    def _run_root(self, base):
        """
        Returns the dated run folder.
        e.g. data/seattle_wa/veterinary/original/2026-03-21/
             data/seattle_wa/veterinary/refreshes/2026-04-01/
        """
        run_folder = "refreshes" if self.cfg.run_type == "refresh" else "original"
        return os.path.join(
            base,
            self.cfg.city,
            self.cfg.industry,
            run_folder,
            self.cfg.run_date,
        )

    def _latest_root(self, base):
        """
        Returns the latest/ folder.
        e.g. data/seattle_wa/veterinary/latest/
        """
        return os.path.join(
            base, self.cfg.city, self.cfg.industry, "latest"
        )

    # ── Data paths ──

    def data(self):
        return self._run_root("data")

    def latest_data(self):
        return self._latest_root("data")

    def data_file(self, filename):
        return os.path.join(self.latest_data(), filename)

    def run_data_file(self, filename):
        """File in the current dated run folder."""
        return os.path.join(self.data(), filename)

    # ── Report paths ──

    def reports(self):
        return self._run_root("reports")

    def latest_reports(self):
        return self._latest_root("reports")

    def briefs(self):
        return os.path.join(self.reports(), "briefs")

    def latest_briefs(self):
        return os.path.join(self.latest_reports(), "briefs")

    # ── Outreach paths ──

    def outreach(self):
        return self._run_root("outreach")

    def latest_outreach(self):
        return self._latest_root("outreach")

    # ── Benchmark paths ──

    def benchmarks_industry(self):
        return os.path.join(
            "benchmarks", "industries", f"{self.cfg.industry}.json"
        )

    def benchmarks_city(self):
        return os.path.join(
            "benchmarks", "cities", f"{self.cfg.city}.json"
        )

    def benchmarks_universal(self):
        return os.path.join("benchmarks", "universal.json")

    # ── Legacy flat file paths (for backward compat during transition) ──
    # These return the latest/ path for files that scripts currently
    # look for in the root directory.

    def legacy(self, filename):
        """
        Returns latest_data path for a file.
        During migration scripts can call paths.legacy("seattle_wa_stats.json")
        and get the right path.
        """
        return self.data_file(filename)

    # ── Create all folders ──

    def setup_folders(self):
        """Create all required directories for this run."""
        folders = [
            self.data(),
            self.latest_data(),
            self.reports(),
            self.latest_reports(),
            self.briefs(),
            self.latest_briefs(),
            self.outreach(),
            self.latest_outreach(),
        ]
        for folder in folders:
            os.makedirs(folder, exist_ok=True)
        return folders

    def __repr__(self):
        return (f"PathBuilder("
                f"data={self.data()}, "
                f"reports={self.reports()})")


# ---------------------------------------------------------------------------
# Latest folder sync
# ---------------------------------------------------------------------------

def sync_to_latest(paths, verbose=True):
    """
    Copy all files from the current dated run folder into latest/.
    Called at the end of pipeline.py after all steps complete.
    Writes _latest_meta.json so you always know when latest/ was updated.
    """
    synced = 0
    pairs  = [
        (paths.data(),     paths.latest_data()),
        (paths.reports(),  paths.latest_reports()),
        (paths.briefs(),   paths.latest_briefs()),
        (paths.outreach(), paths.latest_outreach()),
    ]
    for src, dst in pairs:
        if not os.path.isdir(src):
            continue
        os.makedirs(dst, exist_ok=True)
        for fname in os.listdir(src):
            src_file = os.path.join(src, fname)
            dst_file = os.path.join(dst, fname)
            if os.path.isfile(src_file):
                shutil.copy2(src_file, dst_file)
                synced += 1
            elif os.path.isdir(src_file):
                shutil.copytree(src_file, dst_file, dirs_exist_ok=True)
                synced += 1

    # Write metadata file so you always know when latest/ was updated
    # and exactly which dated folder it was sourced from
    meta = {
        "last_updated":    datetime.now().isoformat(),
        "run_date":        paths.cfg.run_date,
        "run_type":        paths.cfg.run_type,
        "city":            paths.cfg.city,
        "industry":        paths.cfg.industry,
        "source_folder":   paths.data(),
        "reports_folder":  paths.reports(),
        "outreach_folder": paths.outreach(),
    }
    meta_path = os.path.join(paths.latest_data(), "_latest_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    if verbose:
        print(f"  Synced {synced} items to latest/")
        print(f"  Metadata: {meta_path}")
        print(f"    Source:  {paths.data()}")
        print(f"    Updated: {meta[chr(108)+chr(97)+chr(115)+chr(116)+chr(95)+chr(117)+chr(112)+chr(100)+chr(97)+chr(116)+chr(101)+chr(100)][:19]}")
    return synced


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

def log_run(config, status="complete", error_count=0, db_path=DB_PATH):
    """Log this pipeline run to the database."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                city         TEXT,
                industry     TEXT,
                run_type     TEXT,
                run_date     TEXT,
                status       TEXT,
                error_count  INTEGER DEFAULT 0,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT INTO pipeline_runs
                (city, industry, run_type, run_date, status, error_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (config.city, config.industry, config.run_type,
              config.run_date, status, error_count))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [config_loader] Failed to log run: {e}")


def get_run_history(city=None, industry=None, db_path=DB_PATH):
    """Return pipeline run history."""
    try:
        conn  = sqlite3.connect(db_path)
        query = "SELECT * FROM pipeline_runs WHERE 1=1"
        params = []
        if city:
            query += " AND city = ?"
            params.append(city)
        if industry:
            query += " AND industry = ?"
            params.append(industry)
        query += " ORDER BY completed_at DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Module-level singletons — imported by all scripts
# ---------------------------------------------------------------------------

def load(override_city=None, override_industry=None, override_mode=None):
    """
    Load config and path builder.
    Returns (cfg, paths) tuple.

    Usage in any script:
        from config_loader import load
        cfg, paths = load()

    Override from command line args:
        cfg, paths = load(override_city=args.city)
    """
    config = Config(override_city, override_industry, override_mode)
    pb     = PathBuilder(config)
    return config, pb


# ---------------------------------------------------------------------------
# CLI — show current config and paths
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg, paths = load()
    paths.setup_folders()

    print()
    print("=" * 60)
    print("  ACTIVE CONFIGURATION")
    print("=" * 60)
    print(f"  City:          {cfg.city}")
    print(f"  Industry:      {cfg.industry}")
    print(f"  Run type:      {cfg.run_type}")
    print(f"  Run date:      {cfg.run_date}")
    print(f"  Reviews/biz:   {cfg.reviews_per_biz}")
    print(f"  Refresh days:  {cfg.refresh_days}")
    print()
    print("  PATHS")
    print("  " + "-" * 45)
    print(f"  Data (run):    {paths.data()}")
    print(f"  Data (latest): {paths.latest_data()}")
    print(f"  Reports:       {paths.reports()}")
    print(f"  Briefs:        {paths.briefs()}")
    print(f"  Outreach:      {paths.outreach()}")
    print()
    print("  BENCHMARKS")
    print("  " + "-" * 45)
    print(f"  Industry:  {paths.benchmarks_industry()}")
    print(f"  City:      {paths.benchmarks_city()}")
    print(f"  Universal: {paths.benchmarks_universal()}")
    print()
    print("  To change city or industry:")
    print("  Edit config.yaml and run python pipeline.py")
    print()
