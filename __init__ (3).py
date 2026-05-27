
from __future__ import annotations

from pathlib import Path
import pandas as pd

from app.core.utils import normalize_county, normalize_deposit, parse_datetime, auction_id, is_cancelled_text
from app.core.formulas import calc_bid

def normalize_files(paths):
    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p, dtype=str)
        except Exception:
            continue
        if df.empty:
            continue

        out = pd.DataFrame()
        out["Auctioneer"] = df.get("auctioneer", df.get("source", "")).fillna("").astype(str).str.upper()
        out["Sale Date & Time"] = [parse_datetime(d, t) for d, t in zip(df.get("sale date", ""), df.get("sale time", ""))]
        out["County"] = df.get("county", "").apply(normalize_county)
        out["Address"] = df.get("address", "").fillna("").astype(str)
        dep = df.get("deposit", "SEE AD").apply(normalize_deposit)
        out["Deposit"] = dep.apply(lambda x: x[0])
        out["Deposit Amount"] = dep.apply(lambda x: x[1])
        out["Status"] = df.get("status", "Active")
        out["Ad Link"] = df.get("ad link", "")
        # Hard safety net: if any raw row text says cancelled/postponed/withdrawn,
        # exclude it even if the scraper accidentally labeled it Active.
        raw_text = df.fillna("").map(lambda x: str(x)).agg(" ".join, axis=1)
        out["_Raw Cancelled"] = raw_text.apply(is_cancelled_text)
        out["Auction ID"] = out.apply(auction_id, axis=1)
        out["Occupied"] = False
        out["Look"] = ""
        out["Comp"] = ""
        out["Rehab"] = ""
        out["Profit"] = ""
        out["Max"] = ""
        out["%"] = ""
        out["MaxS"] = ""
        out["My Note"] = ""
        # Estimate columns are intentionally not populated without a real data/API provider.

        out = out[out["Address"].astype(str).str.len() > 0]
        out = out[~out["_Raw Cancelled"]]
        out = out[~out["Status"].astype(str).apply(is_cancelled_text)]
        out = out.drop(columns=["_Raw Cancelled"], errors="ignore")
        frames.append(out)

    if not frames:
        return pd.DataFrame()

    final = pd.concat(frames, ignore_index=True)
    final = final.drop_duplicates(subset=["Auction ID"], keep="first")
    return final
