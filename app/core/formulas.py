
from __future__ import annotations

def clean_number(value):
    try:
        if value is None:
            return 0.0
        txt = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
        if not txt or txt.lower() in {"nan", "none"}:
            return 0.0
        return float(txt)
    except Exception:
        return 0.0

def calc_bid(comp, rehab, profit, sale_net=0.96, max_closing=0.06, maxs_closing=0.05, multiplier=1000):
    comp = clean_number(comp) * multiplier
    rehab = clean_number(rehab) * multiplier
    profit = clean_number(profit) * multiplier

    if comp <= 0:
        return "", "", ""

    max_bid = ((comp * sale_net) - profit - rehab) / (1 + max_closing)
    bid_pct = max_bid / comp
    maxs = ((comp * sale_net) - profit - rehab) / (1 + maxs_closing)
    return max_bid, bid_pct, maxs
