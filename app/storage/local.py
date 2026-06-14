from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from app.core.config import BIDS_PATH, LOCAL_DIR, HIDDEN_PATH, BLOCKED_CITIES_PATH, LAYOUT_PATH, FAVORITE_PROPERTIES_PATH
from app.core.utils import clean_text, address_key

BID_COLUMNS = [
    "Auction ID", "Saved At", "Sale Date & Time", "Auctioneer", "County", "Address",
    "Deposit", "Occupied", "Look", "Comp", "Rehab", "Profit", "Max", "%", "MaxS", "My Note"
]

def _comp_to_number(value) -> float:
    try:
        if value is None:
            return 0.0
        txt = str(value).replace(',', '').replace('$', '').strip()
        if txt.lower() in {'', 'nan', 'none', 'null'}:
            return 0.0
        return float(txt)
    except Exception:
        return 0.0


def _archive_nonzero_comp_only(df: pd.DataFrame) -> pd.DataFrame:
    """Saved Bid Archive rule: never keep rows unless Comp is filled and > 0.

    This prevents blank/zero comp rows from being auto-saved during scrape,
    refresh, restore, or visible-grid autosave. User fields can still appear in
    the live grid, but the archive only stores analyzed deals.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=BID_COLUMNS)
    out = df.copy()
    if 'Comp' not in out.columns:
        return pd.DataFrame(columns=BID_COLUMNS)
    comp = out['Comp'].apply(_comp_to_number)
    out = out[comp > 0].copy()
    return out

DEFAULT_BLOCKED_CITIES = {
    "Brentwood",
    "Capitol Heights",
    "District Heights",
    "Cheverly",
    "Camp Spring",
    "Camp Springs",
    "Temple Hills",
    "Landover",
    "Glen Burnie",
    "Hyattsville",
    "Oxon Hill",
    "Pasadena",
    "Riverdale",
    "Union Bridge",
}


def _normalize_city_name(value: str) -> str:
    txt = clean_text(value)
    if not txt:
        return ""
    # Normalize legal ad wording such as “a/r/t/a Camp Springs” so the block
    # survives scraped address variations.
    low = txt.lower()
    if "a/r/t/a" in low:
        txt = txt[:low.index("a/r/t/a")].strip()
    txt = txt.replace("  ", " ").strip(" ,")
    aliases = {
        "Camp Spring": "Camp Springs",
        "Temple Hills A/R/T/A Camp Springs": "Temple Hills",
        "Landover A/R/T/A Cheverly": "Landover",
    }
    titled = txt.title()
    return aliases.get(titled, titled)


# Optional durable cloud storage.
# For Streamlit Cloud, add these in App settings -> Secrets:
# GITHUB_TOKEN = "ghp_..."        # fine-grained token with Gist read/write permission
# GIST_ID = "xxxxxxxxxxxxxxxx"    # private gist id used only for this app
REMOTE_FILENAMES = {
    str(BIDS_PATH.name): BIDS_PATH,
    str(HIDDEN_PATH.name): HIDDEN_PATH,
    str(BLOCKED_CITIES_PATH.name): BLOCKED_CITIES_PATH,
    str(LAYOUT_PATH.name): LAYOUT_PATH,
    str(FAVORITE_PROPERTIES_PATH.name): FAVORITE_PROPERTIES_PATH,
}


def _secret(name: str) -> str:
    """Read secret from environment or Streamlit secrets without requiring Streamlit at import time."""
    if os.getenv(name):
        return os.getenv(name, "").strip()
    try:
        import streamlit as st  # type: ignore
        val = st.secrets.get(name, "")
        return str(val).strip() if val else ""
    except Exception:
        return ""


def remote_enabled() -> bool:
    return bool(_secret("GITHUB_TOKEN") and _secret("GIST_ID"))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_secret('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def ensure():
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    (LOCAL_DIR / "backups").mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    ensure()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)


def _backup(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > 0:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup_path = LOCAL_DIR / "backups" / f"{path.stem}-{stamp}{path.suffix}"
            backup_path.write_bytes(path.read_bytes())
    except Exception:
        pass


def _recover_latest_backup(path: Path) -> bool:
    try:
        backups = sorted((LOCAL_DIR / "backups").glob(f"{path.stem}-*{path.suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
        for b in backups:
            if b.stat().st_size > 0:
                path.write_bytes(b.read_bytes())
                return True
    except Exception:
        pass
    return False


def _remote_get(filename: str) -> Optional[str]:
    if not remote_enabled():
        return None
    try:
        url = f"https://api.github.com/gists/{_secret('GIST_ID')}"
        res = requests.get(url, headers=_headers(), timeout=15)
        if res.status_code != 200:
            return None
        files = res.json().get("files", {})
        if filename not in files:
            return None
        raw_url = files[filename].get("raw_url")
        if not raw_url:
            return files[filename].get("content", "")
        raw = requests.get(raw_url, headers=_headers(), timeout=15)
        return raw.text if raw.status_code == 200 else None
    except Exception:
        return None


def _remote_put(filename: str, content: str) -> None:
    if not remote_enabled():
        return
    try:
        url = f"https://api.github.com/gists/{_secret('GIST_ID')}"
        payload = {"files": {filename: {"content": content}}}
        requests.patch(url, headers=_headers(), json=payload, timeout=20)
    except Exception:
        # Never break the app if cloud backup is temporarily unavailable.
        pass


def _load_text(path: Path) -> str:
    ensure()
    if not path.exists() or path.stat().st_size == 0:
        remote = _remote_get(path.name)
        if remote:
            _atomic_write(path, remote)
    if (not path.exists() or path.stat().st_size == 0) and path.name == BIDS_PATH.name:
        _recover_latest_backup(path)
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        return ""


def _save_text(path: Path, content: str) -> None:
    ensure()
    _backup(path)
    _atomic_write(path, content)
    _remote_put(path.name, content)


def load_bids():
    ensure()
    txt = _load_text(BIDS_PATH)
    if not txt.strip():
        return pd.DataFrame(columns=BID_COLUMNS)
    try:
        from io import StringIO
        df = pd.read_csv(StringIO(txt))
        for c in BID_COLUMNS:
            if c not in df.columns:
                df[c] = ""
        return _archive_nonzero_comp_only(df)
    except Exception:
        if _recover_latest_backup(BIDS_PATH):
            try:
                return _archive_nonzero_comp_only(pd.read_csv(BIDS_PATH))
            except Exception:
                pass
        return pd.DataFrame(columns=BID_COLUMNS)


def save_bids(df):
    ensure()
    if df is None:
        df = pd.DataFrame(columns=BID_COLUMNS)
    for c in BID_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    # Saved Bid Archive must contain only real analyzed bids.
    # Blank/zero Comp rows are live-grid drafts, not archive records.
    df = _archive_nonzero_comp_only(df)

    # keep only the newest row per auction id, then save atomically and to cloud backup if configured
    if not df.empty and "Auction ID" in df.columns and "Saved At" in df.columns:
        df = df.sort_values("Saved At").drop_duplicates("Auction ID", keep="last")
    csv_text = df.to_csv(index=False)
    _save_text(BIDS_PATH, csv_text)


def _property_merge_key(df):
    if df is None or df.empty:
        return pd.Series(dtype=str)
    addr = df.get("Address", pd.Series([""] * len(df))).apply(address_key)
    auct = df.get("Auctioneer", pd.Series([""] * len(df))).fillna("").astype(str).str.upper().str.strip()
    return addr + "|" + auct


def merge_bids(auctions, bids):
    """Merge saved user fields back into fresh scrape results.

    Primary match is Auction ID. Fallback match is normalized address + auctioneer.
    The fallback protects old archives created before the ID was made stable and
    protects against source date/postponement changes.
    """
    if auctions.empty or bids.empty:
        return auctions
    out = auctions.copy()
    latest = bids.copy()
    if "Saved At" in latest.columns:
        latest = latest.sort_values("Saved At")

    user_cols = ["Occupied", "Look", "Comp", "Rehab", "Profit", "My Note"]

    # First merge by current stable Auction ID.
    if "Auction ID" in latest.columns and "Auction ID" in out.columns:
        by_id = latest.drop_duplicates("Auction ID", keep="last")
        cols = ["Auction ID"] + [c for c in user_cols if c in by_id.columns]
        out = out.merge(by_id[cols], on="Auction ID", how="left", suffixes=("", "_saved"))
        for col in user_cols:
            sc = col + "_saved"
            if sc in out.columns:
                out[col] = out[sc].where(out[sc].notna(), out[col])
                out = out.drop(columns=[sc])

    # Then fill remaining blanks from old archive rows by normalized address + auctioneer.
    latest["_Merge Key"] = _property_merge_key(latest)
    out["_Merge Key"] = _property_merge_key(out)
    by_key = latest[latest["_Merge Key"].astype(str).str.len() > 1].drop_duplicates("_Merge Key", keep="last")
    cols = ["_Merge Key"] + [c for c in user_cols if c in by_key.columns]
    if cols != ["_Merge Key"]:
        out = out.merge(by_key[cols], on="_Merge Key", how="left", suffixes=("", "_key_saved"))
        for col in user_cols:
            sc = col + "_key_saved"
            if sc in out.columns:
                current = out[col]
                missing = current.isna() | current.astype(str).str.strip().str.lower().isin(["", "nan", "none", "0"])
                out[col] = out[sc].where(missing & out[sc].notna(), out[col])
                out = out.drop(columns=[sc])
    return out.drop(columns=["_Merge Key"], errors="ignore")


def load_set(path):
    txt = _load_text(path)
    return {clean_text(x) for x in txt.splitlines() if clean_text(x)}


def save_set(path, values):
    _save_text(path, "\n".join(sorted(values)))


def load_hidden():
    return load_set(HIDDEN_PATH)


def save_hidden(values):
    save_set(HIDDEN_PATH, values)


def load_blocked_cities():
    values = {_normalize_city_name(v) for v in load_set(BLOCKED_CITIES_PATH)}
    values = {v for v in values if v}
    # First launch default. Once the file exists, respect whatever the user saved.
    if not values and not BLOCKED_CITIES_PATH.exists():
        values = set(DEFAULT_BLOCKED_CITIES)
        save_blocked_cities(values)
    return values


def save_blocked_cities(values):
    save_set(BLOCKED_CITIES_PATH, {_normalize_city_name(v) for v in values if _normalize_city_name(v)})


def load_favorite_properties():
    return {clean_text(v) for v in load_set(FAVORITE_PROPERTIES_PATH) if clean_text(v)}


def save_favorite_properties(values):
    save_set(FAVORITE_PROPERTIES_PATH, {clean_text(v) for v in values if clean_text(v)})


def toggle_favorite_property(auction_id):
    favorites = load_favorite_properties()
    aid = clean_text(auction_id)
    if aid in favorites:
        favorites.remove(aid)
    elif aid:
        favorites.add(aid)
    save_favorite_properties(favorites)
    return favorites


def hide_address(address):
    from app.core.utils import address_key
    hidden = load_hidden()
    hidden.add(address_key(address))
    save_hidden(hidden)


def clear_hidden():
    save_hidden(set())


def load_layout_defaults():
    txt = _load_text(LAYOUT_PATH)
    if not txt.strip():
        return {}
    try:
        return json.loads(txt)
    except Exception:
        return {}


def save_layout_defaults(values):
    _save_text(LAYOUT_PATH, json.dumps(values, indent=2))
