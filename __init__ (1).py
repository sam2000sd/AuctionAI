
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from urllib.parse import quote_plus

import pandas as pd
from dateutil import parser


MD_COUNTIES = [
    "Allegany County",
    "Anne Arundel County",
    "Baltimore City",
    "Baltimore County",
    "Calvert County",
    "Caroline County",
    "Carroll County",
    "Cecil County",
    "Charles County",
    "Dorchester County",
    "Frederick County",
    "Garrett County",
    "Harford County",
    "Howard County",
    "Kent County",
    "Montgomery County",
    "Prince George's County",
    "Queen Anne's County",
    "Somerset County",
    "St. Mary's County",
    "Talbot County",
    "Washington County",
    "Wicomico County",
    "Worcester County",
    "Washington, DC",
]

COUNTY_ALIASES = {
    "PRINCE GEORGES COUNTY": "Prince George's County",
    "PRINCE GEORGE'S COUNTY": "Prince George's County",
    "PRINCE GEORGE COUNTY": "Prince George's County",
    "PRINCE GEORGES": "Prince George's County",
    "PRINCE GEORGE'S": "Prince George's County",
    "PG COUNTY": "Prince George's County",
    "PG": "Prince George's County",

    "QUEEN ANNES COUNTY": "Queen Anne's County",
    "QUEEN ANNE'S COUNTY": "Queen Anne's County",
    "QUEEN ANNES": "Queen Anne's County",

    "ST MARYS COUNTY": "St. Mary's County",
    "ST MARY'S COUNTY": "St. Mary's County",
    "SAINT MARYS COUNTY": "St. Mary's County",
    "ST MARYS": "St. Mary's County",

    "BALTO CITY": "Baltimore City",
    "BALTIMORE CITY COUNTY": "Baltimore City",
    "BALTIMORE CITY": "Baltimore City",

    "BALTO COUNTY": "Baltimore County",
    "BALTIMORE CO": "Baltimore County",
    "BALTCO": "Baltimore County",
    "BALTIMORE COUNTY": "Baltimore County",

    "MONT": "Montgomery County",
    "MONTGOMERY": "Montgomery County",
    "MONTGOMERY COUNTY": "Montgomery County",

    "HOW": "Howard County",
    "HOWARD": "Howard County",
    "HOWARD COUNTY": "Howard County",

    "AA": "Anne Arundel County",
    "ANNE ARUNDEL": "Anne Arundel County",
    "ANNE ARUNDEL COUNTY": "Anne Arundel County",

    "CARR": "Carroll County",
    "CARROLL": "Carroll County",
    "CARROLL COUNTY": "Carroll County",

    "CHAS": "Charles County",
    "CHARLES": "Charles County",
    "CHARLES COUNTY": "Charles County",

    "FRED": "Frederick County",
    "FREDERICK": "Frederick County",
    "FREDERICK COUNTY": "Frederick County",

    "HARF": "Harford County",
    "HARFORD": "Harford County",
    "HARFORD COUNTY": "Harford County",

    "CALV": "Calvert County",
    "CALVERT": "Calvert County",
    "CALVERT COUNTY": "Calvert County",

    "CECIL": "Cecil County",
    "CECIL COUNTY": "Cecil County",

    "DORCH": "Dorchester County",
    "DORCHESTER": "Dorchester County",
    "DORCHESTER COUNTY": "Dorchester County",

    "SOM": "Somerset County",
    "SOMERSET": "Somerset County",
    "SOMERSET COUNTY": "Somerset County",

    "ALLEGANY": "Allegany County",
    "ALLEGANY COUNTY": "Allegany County",
    "CAROLINE": "Caroline County",
    "CAROLINE COUNTY": "Caroline County",
    "GARRETT": "Garrett County",
    "GARRETT COUNTY": "Garrett County",
    "KENT": "Kent County",
    "KENT COUNTY": "Kent County",
    "TALBOT": "Talbot County",
    "TALBOT COUNTY": "Talbot County",
    "WICOMICO": "Wicomico County",
    "WICOMICO COUNTY": "Wicomico County",
    "WORCESTER": "Worcester County",
    "WORCESTER COUNTY": "Worcester County",

    "WASH": "Washington County",
    "WASHINGTON": "Washington County",
    "WASHINGTON COUNTY": "Washington County",

    "DC": "Washington, DC",
    "D C": "Washington, DC",
    "D.C.": "Washington, DC",
    "WASHINGTON DC": "Washington, DC",
    "WASHINGTON, DC": "Washington, DC",
    "WASHINGTON D C": "Washington, DC",
    "DISTRICT OF COLUMBIA": "Washington, DC",
    "WASHINGTON DC OFFICE": "Washington, DC",
    "WASHINGTON D.C. OFFICE": "Washington, DC",
}

def _compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

_CANONICAL_LOOKUP = {_compact(c): c for c in MD_COUNTIES}
_ALIAS_LOOKUP = {_compact(k): v for k, v in COUNTY_ALIASES.items()}

def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def normalize_county(value) -> str:
    """
    Return only a whitelisted county:
    Maryland county, Baltimore City, or Washington, DC.
    Never return courthouse/location/address text.
    """
    raw = clean_text(value)
    if not raw or raw.lower() in {"nan", "none"}:
        return "Unknown County"

    up = raw.upper().replace(".", "")
    compact = _compact(up)

    # DC first so Washington DC is not treated as Washington County.
    if (
        "DISTRICTOFCOLUMBIA" in compact
        or "WASHINGTONDC" in compact
        or re.search(r"\bDC\b", up)
        or re.search(r"\bD C\b", up)
    ):
        return "Washington, DC"

    if compact in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[compact]

    if compact in _CANONICAL_LOOKUP:
        return _CANONICAL_LOOKUP[compact]

    # Extract leading county from courthouse strings.
    # Examples:
    # "Anne Arundel Courthouse At 8 Church Cir..." -> Anne Arundel County
    # "Baltimore City Courthouse At 100 N Calvert..." -> Baltimore City
    leading = re.split(r"\b(COURTHOUSE|COURT HOUSE|OFFICE|CHS|AT)\b", up, maxsplit=1)[0].strip()
    leading_compact = _compact(leading)

    if leading_compact in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[leading_compact]
    if leading_compact in _CANONICAL_LOOKUP:
        return _CANONICAL_LOOKUP[leading_compact]

    # Search canonical counties/aliases inside the raw text, longest first.
    candidates = []
    for county in MD_COUNTIES:
        if county == "Washington, DC":
            continue
        key = _compact(county)
        base = key.replace("COUNTY", "")
        candidates.append((len(key), key, county))
        if base:
            candidates.append((len(base), base, county))

    for _, key, county in sorted(candidates, reverse=True):
        if key and key in compact:
            return county

    for key, county in sorted(_ALIAS_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True):
        if county == "Washington, DC":
            continue
        if key and key in compact:
            return county

    return "Unknown County"

def parse_datetime(date_value, time_value=""):
    txt = f"{date_value or ''} {time_value or ''}".strip()
    if not txt:
        return pd.NaT
    try:
        return parser.parse(txt, fuzzy=True)
    except Exception:
        return pd.NaT

def normalize_deposit(value):
    txt = clean_text(value)
    if not txt or txt.lower() in {"nan", "none"}:
        return "SEE AD", 0

    if "→" in txt:
        left = txt.split("→")[0]
        _, amount = normalize_deposit(left)
        return txt, amount

    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", txt)
    if pct and "$" not in txt:
        return f"{float(pct.group(1)):g}%", 0

    k = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*K\b", txt, flags=re.I)
    if k:
        amount = float(k.group(1)) * 1000
        return f"${amount:,.0f}", amount

    money = re.search(r"\$?\s*([0-9][0-9,]*(?:\.\d+)?)", txt)
    if money:
        amount = float(money.group(1).replace(",", ""))
        return f"${amount:,.0f}", amount

    if "SEE" in txt.upper() and "AD" in txt.upper():
        return "SEE AD", 0

    return txt, 0

def auction_id(row) -> str:
    key = "|".join([
        str(row.get("Sale Date & Time", "")),
        str(row.get("Address", "")),
        str(row.get("Auctioneer", "")),
    ]).upper()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]

def money(value):
    try:
        if value == "" or pd.isna(value):
            return ""
        return f"${float(value):,.0f}"
    except Exception:
        return ""

def pct(value):
    try:
        if value == "" or pd.isna(value):
            return ""
        return f"{float(value):.1%}"
    except Exception:
        return ""

def this_or_next_week(today=None):
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=4)

def city_from_address(address):
    parts = [p.strip() for p in clean_text(address).split(",")]
    if len(parts) >= 2:
        return parts[1].title()
    return ""

def address_key(address):
    return re.sub(r"[^A-Z0-9]", "", str(address or "").upper())

def zillow_link(address):
    return "https://www.zillow.com/homes/" + quote_plus(str(address or "")) + "_rb/"

def redfin_link(address):
    # Redfin needs an internal property id for a true direct property URL.
    # This is the most reliable non-API fallback: a Redfin address search page,
    # not the generic redfin.com homepage.
    return "https://www.redfin.com/stingray/do/location-autocomplete?location=" + quote_plus(str(address or "")) + "&start=0&count=10&v=2"

def redfin_search_link(address):
    q = quote_plus('site:redfin.com "' + str(address or '').replace('"', '') + '"')
    return "https://www.google.com/search?q=" + q

def is_cancelled_text(value) -> bool:
    txt = clean_text(value).upper()
    return any(word in txt for word in [
        "CANCELLED", "CANCELED", "CANCELLED/", "CANCELED/",
        "POSTPONED", "WITHDRAWN", "REMOVED", "SOLD PRIOR", "NO SALE"
    ])
