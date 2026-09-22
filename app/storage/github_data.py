"""Pull the latest scraped listings from the repo's `data` branch.

GitHub Actions (.github/workflows/scrape.yml) scrapes every source every 30 min
and force-pushes the CSVs + manifest.json to the `data` branch. This module is
the read side: on app load we compare the published manifest's `updated` stamp
with what we last synced and, if newer, download the whole set into SCRAPED_DIR.

This also solves the "listings vanished after a reboot" problem: Streamlit
Cloud's disk is wiped on restart, but the data branch is not, so the very next
page load repopulates everything.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import requests

from app.core.config import SCRAPED_DIR

REPO = "sam2000sd/AuctionAI"
BRANCH = "data"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/scraped/"
MARKER = SCRAPED_DIR / ".synced_manifest.json"
TIMEOUT = 20


def _get(name: str) -> requests.Response:
    # Cache-bust the raw.githubusercontent CDN (it otherwise caches ~5 min).
    return requests.get(f"{RAW_BASE}{name}?t={int(time.time())}", timeout=TIMEOUT)


def last_synced() -> dict:
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except Exception:
        return {}


def sync_scraped_from_github(force: bool = False) -> dict:
    """Download the published data set if it is newer than what we have.

    Returns a small status dict; never raises (the app must load regardless).
    """
    status = {"changed": False, "updated": "", "files": 0, "error": ""}
    try:
        r = _get("manifest.json")
        if r.status_code != 200:
            status["error"] = f"manifest HTTP {r.status_code}"
            return status
        manifest = r.json()
    except Exception as e:  # network down, branch missing, bad JSON
        status["error"] = str(e)[:200]
        return status

    updated = str(manifest.get("updated", ""))
    status["updated"] = updated
    files = [str(f) for f in manifest.get("files", []) if str(f).endswith((".csv", ".json", ".html"))]
    if not files:
        status["error"] = "manifest lists no files"
        return status

    # Nothing new since our last successful sync (and we still have the CSVs).
    if not force and last_synced().get("updated") == updated and any(SCRAPED_DIR.glob("*.csv")):
        return status

    tmp = SCRAPED_DIR / ".incoming"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for name in files:
            resp = _get(name)
            if resp.status_code != 200:
                raise RuntimeError(f"{name}: HTTP {resp.status_code}")
            dest = tmp / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        status["error"] = str(e)[:200]
        return status

    # All downloaded: swap in atomically-ish. Old CSVs go first so we never show
    # a mix of two scrape generations.
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    for p in SCRAPED_DIR.glob("*.csv"):
        p.unlink(missing_ok=True)
    for src in tmp.rglob("*"):
        if src.is_file():
            dest = SCRAPED_DIR / src.relative_to(tmp)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)
    MARKER.write_text(json.dumps({"updated": updated, "files": len(files)}), encoding="utf-8")
    status.update(changed=True, files=len(files))
    return status
