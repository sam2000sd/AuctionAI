"""Scrape every auction source and stage the results for publishing.

Runs inside GitHub Actions every 30 minutes (see .github/workflows/scrape.yml).
The Streamlit app never scrapes on a schedule itself; it just downloads whatever
this job last published to the `data` branch (see app/storage/github_data.py).

Usage:
    python scripts/scrape_all.py            # scrape all sources
    python scripts/scrape_all.py AC TW      # scrape a subset
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import SCRAPED_DIR  # noqa: E402
from app.scrapers.sources import scrape_many  # noqa: E402

ALL_SOURCES = ["AC", "TW", "HW", "MWC", "BL", "ADC"]
# A source that comes back with fewer rows than this is treated as a failed
# scrape (site down, bot-blocked, layout changed). We keep the previous good
# CSV for it instead of publishing an empty list and wiping the app's data.
MIN_ROWS = {"AC": 20, "TW": 5, "HW": 5, "MWC": 0, "BL": 3, "ADC": 0}


def main(argv: list[str]) -> int:
    sources = [s.upper() for s in argv] or ALL_SOURCES
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)

    # Remember the newest previous CSV per source so we can fall back to it.
    previous = {}
    for p in sorted(SCRAPED_DIR.glob("*.csv")):
        src = p.name.split("_", 1)[0]
        previous[src] = p

    results = scrape_many(sources, clear_old=False)

    kept = {}
    for src in sources:
        r = results.get(src, {})
        ok = bool(r.get("ok")) and r.get("rows", 0) >= MIN_ROWS.get(src, 1)
        new_path = Path(r["path"]) if r.get("path") else None
        if ok and new_path and new_path.exists():
            kept[src] = new_path
            status = f"OK   {r['rows']:>4} rows"
        else:
            # Failed / suspiciously small: discard the new file, keep the old one.
            if new_path and new_path.exists() and new_path != previous.get(src):
                new_path.unlink(missing_ok=True)
            if previous.get(src):
                kept[src] = previous[src]
            status = f"FAIL {r.get('rows', 0):>4} rows -> keeping previous ({r.get('error', '')[:80]})"
        print(f"{src:4} {status}")

    # Sources not scraped this run keep whatever was already there.
    for src, p in previous.items():
        kept.setdefault(src, p)

    # Drop superseded CSVs so the published set is exactly one file per source.
    keep_paths = set(kept.values())
    for p in SCRAPED_DIR.glob("*.csv"):
        if p not in keep_paths:
            p.unlink(missing_ok=True)

    files = sorted(p.name for p in SCRAPED_DIR.glob("*.csv"))
    for extra in ("values.json", "avm_adc.json"):
        if (SCRAPED_DIR / extra).exists():
            files.append(extra)
    ads_dir = SCRAPED_DIR / "ads"
    if ads_dir.exists():
        files.extend(f"ads/{p.name}" for p in sorted(ads_dir.glob("*.html")))

    manifest = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
        "rows": {src: int(results.get(src, {}).get("rows", 0)) for src in sources},
    }
    (SCRAPED_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"manifest: {len(files)} files, updated {manifest['updated']}")

    # Fail the job only if every requested source failed (nothing new to publish).
    return 0 if any(src in kept and kept[src] != previous.get(src) for src in sources) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
