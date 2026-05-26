
from __future__ import annotations

import pandas as pd
from app.core.config import BIDS_PATH, LOCAL_DIR, HIDDEN_PATH, BLOCKED_CITIES_PATH, LAYOUT_PATH
import json
from app.core.utils import address_key, clean_text

BID_COLUMNS = [
    "Auction ID", "Saved At", "Sale Date & Time", "Auctioneer", "County", "Address",
    "Deposit", "Occupied", "Look", "Comp", "Rehab", "Profit", "Max", "%", "MaxS", "My Note"
]

def ensure():
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

def load_bids():
    ensure()
    if not BIDS_PATH.exists():
        return pd.DataFrame(columns=BID_COLUMNS)
    try:
        return pd.read_csv(BIDS_PATH)
    except Exception:
        return pd.DataFrame(columns=BID_COLUMNS)

def save_bids(df):
    ensure()
    df.to_csv(BIDS_PATH, index=False)

def merge_bids(auctions, bids):
    if auctions.empty or bids.empty or "Auction ID" not in bids.columns:
        return auctions
    latest = bids.sort_values("Saved At").drop_duplicates("Auction ID", keep="last")
    cols = ["Auction ID", "Occupied", "Look", "Comp", "Rehab", "Profit", "My Note"]
    cols = [c for c in cols if c in latest.columns]
    out = auctions.merge(latest[cols], on="Auction ID", how="left", suffixes=("", "_saved"))
    for col in ["Occupied", "Look", "Comp", "Rehab", "Profit", "My Note"]:
        sc = col + "_saved"
        if sc in out.columns:
            out[col] = out[sc].where(out[sc].notna(), out[col])
            out = out.drop(columns=[sc])
    return out

def load_set(path):
    ensure()
    if not path.exists():
        return set()
    return {clean_text(x) for x in path.read_text(encoding="utf-8").splitlines() if clean_text(x)}

def save_set(path, values):
    ensure()
    path.write_text("\n".join(sorted(values)), encoding="utf-8")

def load_hidden():
    return load_set(HIDDEN_PATH)

def save_hidden(values):
    save_set(HIDDEN_PATH, values)

def load_blocked_cities():
    return load_set(BLOCKED_CITIES_PATH)

def save_blocked_cities(values):
    save_set(BLOCKED_CITIES_PATH, {clean_text(v).title() for v in values if clean_text(v)})

def hide_address(address):
    hidden = load_hidden()
    hidden.add(address_key(address))
    save_hidden(hidden)

def clear_hidden():
    save_hidden(set())


def load_layout_defaults():
    ensure()
    if not LAYOUT_PATH.exists():
        return {}
    try:
        return json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_layout_defaults(values):
    ensure()
    LAYOUT_PATH.write_text(json.dumps(values, indent=2), encoding="utf-8")
