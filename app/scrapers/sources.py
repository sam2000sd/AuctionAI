
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sys
import subprocess
from urllib.parse import urljoin, urlparse, parse_qs, unquote

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

from app.core.config import SCRAPED_DIR
from app.core.utils import clean_text, is_cancelled_text, loose_address_key
import json

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36 Auction Intelligence"}
REQUEST_TIMEOUT = 18
PLAYWRIGHT_GOTO_TIMEOUT = 20000
PLAYWRIGHT_TEXT_TIMEOUT = 8000
URLS = {
    "AC": "https://realestate.alexcooper.com/foreclosures?limit=200",
    "TW": "https://www.tidewaterauctions.com/upcoming-real-estate-auctions",
    "HW": "https://www.hwestauctions.com/schedule.v4.php",
    "MWC_MD": "https://apps.mwc-law.com/SalesLists/MD.html",
    "MWC_DC": "https://apps.mwc-law.com/SalesLists/DC.html",
    "BL": "https://ajbillig.com/auction-list/",
    "TW_AD": "https://www.tidewaterauctions.com/default.aspx/GetAd",
    "ADC_GRAPH": "https://graph.auction.com/graphql",
    "ADC_SITE": "https://www.auction.com",
}

MONTHS = {
    "JANUARY":"01","FEBRUARY":"02","MARCH":"03","APRIL":"04","MAY":"05","JUNE":"06",
    "JULY":"07","AUGUST":"08","SEPTEMBER":"09","OCTOBER":"10","NOVEMBER":"11","DECEMBER":"12"
}

COUNTIES = [
    "Allegany County","Anne Arundel County","Baltimore City","Baltimore County","Calvert County",
    "Caroline County","Carroll County","Cecil County","Charles County","Dorchester County",
    "Frederick County","Harford County","Howard County","Montgomery County","Prince Georges County",
    "Prince George's County","Queen Anne's County","Somerset County","St. Mary's County",
    "Washington County","Wicomico County","Worcester County","Washington, DC","District of Columbia"
]

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text

def write_rows(source, rows):
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        return None
    path = SCRAPED_DIR / f"{source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path

def clear_cache():
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    for p in SCRAPED_DIR.glob("*.csv"):
        p.unlink(missing_ok=True)



def normalize_ad_url(raw, base_url):
    """Return a real auction ad URL. Never return Google search/map/redirect URLs."""
    href = str(raw or "").strip()
    if not href or href.startswith("javascript:") or href.startswith("#"):
        return ""
    full = urljoin(base_url, href)

    # Some pages wrap outbound links through google.com/url?q=<real url>. Unwrap it.
    for _ in range(2):
        try:
            parsed = urlparse(full)
            host = (parsed.netloc or "").lower()
            if host.endswith("google.com") or host.endswith("googleusercontent.com"):
                qs = parse_qs(parsed.query)
                target = (qs.get("q") or qs.get("url") or qs.get("u" ) or [""])[0]
                if target:
                    full = unquote(target)
                    continue
                # Google maps/search/calendar links are not auction ads.
                return ""
        except Exception:
            return ""
        break

    low = full.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return ""
    if "google.com/search" in low or "google.com/maps" in low or "maps.google" in low:
        return ""
    return full

def row_direct_link(tr, base_url):
    """Return the best row-level advertisement/detail link, not the source homepage."""
    links = []
    base_clean = base_url.rstrip("/").lower()
    for a in tr.find_all("a", href=True):
        text = clean_text(a.get_text(" ")).lower()
        href = (a.get("href") or "").strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        full = normalize_ad_url(href, base_url)
        if not full:
            continue
        full_clean = full.rstrip("/").lower()
        if full_clean == base_clean:
            continue
        score = 0
        hay = (text + " " + href).lower()
        if "view" in hay and "ad" in hay:
            score += 100
        elif "view" in hay:
            score += 70
        if any(token in hay for token in ["ad", "sale", "foreclosure", "auction", "property", "detail"]):
            score += 25
        # Prefer links that look property-specific, not navigation/category links.
        if re.search(r"\d", href):
            score += 10
        if score:
            links.append((score, full))
    if links:
        return sorted(links, reverse=True)[0][1]
    return ""

def row_has_view_ad(tr):
    for a in tr.find_all("a", href=True):
        txt = clean_text(a.get_text(" ")).lower()
        href = (a.get("href") or "").lower()
        if ("view" in txt and "ad" in txt) or "viewad" in href or "view-ad" in href:
            return True
    return False


def _addr_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

def _street_num(value: str) -> str:
    m = re.match(r"\s*(\d+)", str(value or ""))
    return m.group(1) if m else ""

def build_link_candidates(html, base_url):
    """Build row/card-level link candidates once.

    This replaces the slow old behavior that re-scanned the entire AC DOM once
    for every property row. That was the likely reason full scraping could sit
    for many minutes. We accept a blank link over a wrong or slow link.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    base_clean = base_url.rstrip("/").lower()
    candidates = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        full = normalize_ad_url(href, base_url)
        if not full:
            continue
        if full.rstrip("/").lower() == base_clean:
            continue
        a_text = clean_text(a.get_text(" ")).lower()
        hay = (a_text + " " + href).lower()
        if not any(tok in hay for tok in ["view", "ad", "auction", "foreclosure", "property", "detail", "sale"]):
            continue
        node = a
        for depth in range(0, 7):
            if not node:
                break
            text = clean_text(node.get_text(" "))
            if 20 <= len(text) <= 1200 and re.search(r"\d", text):
                key = (_addr_key(text)[:80], full)
                if key not in seen:
                    seen.add(key)
                    candidates.append({"text": text, "key": _addr_key(text), "url": full, "depth": depth, "href": href, "link_text": a_text})
                break
            node = node.parent
    return candidates

def best_link_from_candidates(candidates, address):
    addr_key = _addr_key(address)
    if len(addr_key) < 8:
        return ""
    sn = _street_num(address)
    best = []
    for c in candidates:
        ck = c.get("key", "")
        if not ck:
            continue
        score = 0
        # Require at least the street number or a solid prefix overlap to avoid wrong AC links.
        if sn and sn in ck:
            score += 50
        if addr_key[:14] and addr_key[:14] in ck:
            score += 80
        elif addr_key[:10] and addr_key[:10] in ck:
            score += 55
        else:
            continue
        hay = (c.get("link_text", "") + " " + c.get("href", "")).lower()
        if "view" in hay and "ad" in hay:
            score += 60
        if any(tok in hay for tok in ["auction", "foreclosure", "property", "detail", "sale"]):
            score += 20
        score -= int(c.get("depth", 0))
        best.append((score, c.get("url", "")))
    if not best:
        return ""
    best.sort(reverse=True)
    return best[0][1]

def link_near_text(html, needle, base_url):
    """Fast conservative AC link lookup. Returns blank rather than wrong/slow link."""
    try:
        return best_link_from_candidates(build_link_candidates(html, base_url), needle)
    except Exception:
        return ""

def is_gray_or_cancelled_row(tr, text=""):
    """Detect TW/auction cancelled rows without killing active rows.

    Tidewater commonly shows cancelled rows in gray and removes the View Ad link.
    Active rows may sometimes be parsed without visible "View Ad" text, so the
    safe rule is: explicit cancelled/withdrawn text always drops; strike tags drop;
    gray styling drops only when paired with no row-level View Ad link.
    """
    txt = clean_text(text)
    if is_cancelled_text(txt):
        return True
    if tr.find(["s", "strike", "del"]):
        return True

    # Check row and cell styling/classes, because TW often grays individual TDs.
    style_blob = []
    class_blob = []
    for node in [tr] + list(tr.find_all(["td", "th", "span", "a", "div"])):
        style_blob.append((node.get("style") or "").lower().replace(" ", ""))
        class_blob.append(" ".join(node.get("class") or []).lower())
    styles = " ".join(style_blob)
    classes = " ".join(class_blob)

    gray_tokens = [
        "gray", "grey", "#ccc", "#cccccc", "#ddd", "#dddddd", "#eee", "#eeeeee",
        "#999", "#999999", "#aaa", "#aaaaaa", "lightgray", "lightgrey", "darkgray", "darkgrey",
        "opacity:", "text-decoration:line-through", "line-through"
    ]
    class_tokens = ["gray", "grey", "cancel", "inactive", "disabled", "strike", "strikethrough"]
    looks_gray = any(tok in styles for tok in gray_tokens) or any(tok in classes for tok in class_tokens)
    return bool(looks_gray and not row_has_view_ad(tr))

def _cell_by_header(cells, headers, names, default_index=None):
    for i, h in enumerate(headers):
        hh = h.lower()
        if any(n in hh for n in names) and i < len(cells):
            return cells[i]
    if default_index is not None and default_index < len(cells):
        return cells[default_index]
    return ""

def _tw_row_to_record(tr, sale_date, county, base_url):
    txt = clean_text(tr.get_text(" "))
    if is_gray_or_cancelled_row(tr, txt):
        return None
    # TW active sales consistently have a View Ad link. Cancelled gray rows do not.
    # This check is TW-only and prevents cancelled rows from reaching the cache/grid.
    if not row_has_view_ad(tr):
        return None
    cells = [clean_text(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
    if not cells:
        return None

    # Flexible extraction. Some TW tables shift columns or include hidden cells.
    sale_time = ""
    for c in cells:
        m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM)?)\b", c, flags=re.I)
        if m:
            sale_time = m.group(1).upper()
            break
    if not sale_time:
        m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM)?)\b", txt, flags=re.I)
        sale_time = m.group(1).upper() if m else ""
    if not sale_time:
        return None

    deposit = ""
    for c in cells:
        if "$" in c or re.search(r"\b\d{1,3}%\b", c):
            deposit = c
            break

    # Prefer the cell after time that looks like a street address.
    address = ""
    bad_words = {"view ad", "deposit", "time", "address", "county"}
    for c in cells:
        lc = c.lower()
        if c == sale_time or any(b in lc for b in bad_words):
            continue
        if "$" in c or re.fullmatch(r"\d{1,2}:\d{2}\s*(?:AM|PM)?", c, flags=re.I):
            continue
        if re.search(r"\d", c) and re.search(r"\b(st|street|ave|avenue|rd|road|dr|drive|ln|lane|ct|court|cir|circle|way|pl|place|ter|terrace|blvd|boulevard|pkwy|parkway|hwy|highway)\b", c, flags=re.I):
            address = c
            break
    if not address and len(cells) >= 2:
        # Original TW format: time, address, deposit.
        address = cells[1]
    address = clean_text(address.replace("HUD SALE:", ""))
    if not address or not re.search(r"\d", address):
        return None

    link = row_direct_link(tr, base_url)
    return {
        "source": "TW", "auctioneer": "TW", "sale date": sale_date, "sale time": sale_time,
        "county": county, "address": address, "deposit": deposit or "SEE AD", "status": "Active",
        "ad link": link,
    }

def date_from_heading(txt):
    m = re.search(r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(\d{1,2}),\s*(\d{4})", txt, re.I)
    if m:
        return f"{MONTHS[m.group(1).upper()]}/{int(m.group(2)):02d}/{m.group(3)}"
    m = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", txt)
    return m.group(0) if m else ""

def _ensure_playwright_chromium():
    """Best-effort browser install for Streamlit Cloud. Safe locally."""
    if sync_playwright is None:
        return False
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            timeout=180,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    return True


def _ac_parse_visible_text(text, html=""):
    """Parse the exact AC visible-text format that works in the local app.

    This intentionally avoids broad DOM guessing. AC active rows are the lines
    that contain a sale time, deposit, and VIEW AD. Cancelled/no-ad rows are skipped.
    """
    lines = [clean_text(x) for x in str(text or "").splitlines() if clean_text(x)]
    link_candidates = build_link_candidates(html or "", URLS["AC"])
    county_set = {c.lower(): c for c in COUNTIES}
    current_date = ""
    current_county = ""
    rows = []

    for line in lines:
        # Date header example: "TUESDAY | MAY 26, 2026"
        if re.search(r"\b(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\b", line, re.I):
            d = date_from_heading(line)
            if d:
                current_date = d
            continue

        county_key = line.lower()
        if county_key in county_set:
            current_county = county_set[county_key]
            continue

        if line.upper().startswith("LOCATION:"):
            continue

        # AC row example:
        # 11:09 am 5604 Ramblewood Avenue, Clinton, 20735 Dep. $39,000 APL MAP VIEW AD
        m = re.match(
            r"^(\d{1,2}:\d{2}\s*(?:am|pm)|\d{1,2}\s*(?:am|pm))\s+(.+?)\s+Dep\.?\s+(?:\$?([0-9][0-9,]*)|SEE\s+AD)(.*)$",
            line,
            re.I,
        )
        if not m:
            continue

        if is_cancelled_text(line) or "VIEW AD" not in line.upper():
            continue

        sale_time = m.group(1).upper()
        address = clean_text(m.group(2))
        amount = m.group(3)
        tail = m.group(4) or ""
        pct = re.search(r"increased\s+to\s+(\d+(?:\.\d+)?)\s*%", tail, re.I)
        if amount:
            deposit = f"${amount} → {pct.group(1)}%" if pct else f"${amount}"
        else:
            deposit = "SEE AD"

        rows.append({
            "source": "AC",
            "auctioneer": "AC",
            "sale date": current_date,
            "sale time": sale_time,
            "county": current_county,
            "address": address,
            "deposit": deposit,
            "status": "Active",
            "ad link": best_link_from_candidates(link_candidates, address),
        })

    return dedupe(rows)


def parse_ac():
    """Scrape AC only. Keeps local working logic, with Streamlit Cloud browser support."""
    text = ""
    html = ""

    if sync_playwright is not None:
        _ensure_playwright_chromium()
        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                page = browser.new_page(viewport={"width": 1700, "height": 3000})
                # AC's backend caps each page at 100 lots regardless of ?limit=, so
                # a single load silently drops everything past lot 100. Walk pages
                # until a page renders no active rows (or a hard cap is reached).
                texts, htmls = [], []
                for page_no in range(1, 8):
                    url = URLS["AC"] if page_no == 1 else f"{URLS['AC']}&page={page_no}"
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    # Wait for AC's JS-rendered foreclosure list, but do not hang forever.
                    try:
                        page.wait_for_selector("text=Foreclosures", timeout=12000)
                    except Exception:
                        pass
                    try:
                        page.wait_for_selector("text=VIEW AD", timeout=12000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2500)
                    t = page.locator("body").inner_text(timeout=15000)
                    if "VIEW AD" not in t.upper():
                        break
                    if texts and t == texts[-1]:
                        break
                    texts.append(t)
                    htmls.append(page.content())
                text = "\n".join(texts)
                html = "\n".join(htmls)
                browser.close()
        except Exception as e:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            # Leave a debug file so the app does not silently fail.
            try:
                Path("ac_last_error.txt").write_text(str(e), encoding="utf-8")
            except Exception:
                pass

    # Save exactly what Streamlit Cloud rendered. This is the first file to inspect
    # if AC changes the page again.
    try:
        Path("ac_last_rendered_text.txt").write_text(text or "", encoding="utf-8")
        Path("ac_last_rendered_html.html").write_text(html or "", encoding="utf-8")
    except Exception:
        pass

    return _ac_parse_visible_text(text, html)

def parse_tw_text_fallback(soup, html=""):
    """Parse Tidewater from the visible text when the site is not real <tr> rows.

    TW's current upcoming-sales page can render as a stream of text/blocks.
    The earlier parser only trusted tables/TR tags and therefore could return
    zero active rows. This fallback now requires a View Ad line before the next row, because Sam
    confirmed TW cancelled rows are the gray rows without View Ad.
    """
    lines = [clean_text(x) for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]
    rows = []
    link_candidates = build_link_candidates(html or str(soup), URLS["TW"])
    current_date = ""
    current_county = ""
    county_lookup = {c.lower().replace("'", ""): c for c in COUNTIES}

    def is_county_line(x):
        k = x.lower().replace("'", "")
        if k in county_lookup:
            return county_lookup[k]
        if k == "washington dc":
            return "Washington, DC"
        return ""

    def is_noise(x):
        lx = x.lower()
        return (
            lx in {"time", "address", "deposit", "client", "view ad", "download", "print"}
            or lx.startswith("unless otherwise noted")
            or lx.startswith("all cancellations")
            or lx.startswith("week of")
            or lx.startswith("disclaimer")
        )

    i = 0
    while i < len(lines):
        line = lines[i]
        d = date_from_heading(line)
        if d:
            current_date = d
            i += 1
            continue
        c = is_county_line(line)
        if c:
            current_county = c
            i += 1
            continue

        mt = re.fullmatch(r"(\d{1,2}:\d{2}\s*(?:AM|PM))", line, flags=re.I)
        if not mt:
            i += 1
            continue

        sale_time = mt.group(1).upper()
        j = i + 1
        # Skip headers/noise between time and address.
        while j < len(lines) and is_noise(lines[j]):
            j += 1
        if j >= len(lines):
            i += 1
            continue
        address = lines[j].replace("HUD SALE:", "").strip()
        # Address must contain a number and not be another date/county/time/header.
        if (not re.search(r"\d", address)) or date_from_heading(address) or is_county_line(address) or re.fullmatch(r"\d{1,2}:\d{2}\s*(?:AM|PM)", address, flags=re.I):
            i += 1
            continue

        j += 1
        while j < len(lines) and is_noise(lines[j]):
            j += 1
        deposit = "SEE AD"
        if j < len(lines) and ("$" in lines[j] or re.search(r"\b\d{1,3}%\b", lines[j])):
            deposit = lines[j]
            j += 1

        # TW cancelled rows are the gray rows without a View Ad. In text fallback,
        # require a View Ad before the next time/date/county block. This keeps active
        # rows and removes cancelled rows even when CSS color is not available.
        scan_end = j
        has_view_ad = False
        while scan_end < len(lines):
            nxt = lines[scan_end]
            if scan_end > j and (
                re.fullmatch(r"\d{1,2}:\d{2}\s*(?:AM|PM)", nxt, flags=re.I)
                or date_from_heading(nxt)
                or is_county_line(nxt)
            ):
                break
            if "view ad" in nxt.lower().replace("  ", " "):
                has_view_ad = True
                scan_end += 1
                break
            scan_end += 1
        if not has_view_ad:
            i = max(scan_end, i + 1)
            continue

        link = best_link_from_candidates(link_candidates, address)
        rows.append({
            "source": "TW", "auctioneer": "TW", "sale date": current_date, "sale time": sale_time,
            "county": current_county, "address": clean_text(address), "deposit": deposit, "status": "Active",
            "ad link": link,
        })
        i = max(scan_end, i + 1)

    return rows


def _tw_fetch_ad(ad_id):
    """Tidewater ads are loaded by JS (GetAd(id) -> POST webmethod), so there is no
    standalone ad URL. Fetch the ad HTML once at scrape time and cache it to disk.
    The row's ad link becomes 'tw-ad:<id>' which the app renders as an in-app ad viewer.
    """
    ad_dir = SCRAPED_DIR / "ads"
    ad_dir.mkdir(parents=True, exist_ok=True)
    path = ad_dir / f"TW_{ad_id}.html"
    if path.exists() and path.stat().st_size > 50:
        return f"tw-ad:{ad_id}"
    try:
        r = requests.post(
            URLS["TW_AD"],
            headers={**HEADERS, "Content-Type": "application/json; charset=utf-8", "X-Requested-With": "XMLHttpRequest"},
            data="{'id':'%s'}" % ad_id,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json().get("d", "")
        # TW double-encodes smart quotes (UTF-8 bytes read as cp1252). Undo when it round-trips cleanly.
        if "\u00e2\u20ac" in body or "\u00c3" in body:
            try:
                # cp1252 leaves a few control bytes (0x81,0x8d,0x8f,0x90,0x9d) undefined; map them through latin-1.
                raw_bytes = body.encode("cp1252", errors="ignore") if all(ord(c) not in (0x81, 0x8D, 0x8F, 0x90, 0x9D) for c in body) else "".join(
                    c if ord(c) < 0x80 or ord(c) in (0x81, 0x8D, 0x8F, 0x90, 0x9D) else c.encode("cp1252", errors="ignore").decode("latin-1") for c in body
                ).encode("latin-1", errors="ignore")
                fixed = raw_bytes.decode("utf-8", errors="ignore")
                if fixed and len(fixed) > len(body) * 0.8:
                    body = fixed
            except Exception:
                pass
        if body and len(body) > 50:
            # Strip scripts defensively before caching.
            body = re.sub(r"<script.*?</script>", "", body, flags=re.S | re.I)
            path.write_text(body, encoding="utf-8")
            return f"tw-ad:{ad_id}"
    except Exception:
        pass
    return ""


def parse_tw_blocks(soup):
    """Current Tidewater layout: div.us-block-header (date + county) followed by
    div.us-sales-block containing div.us-sale-item rows with us-sale-time/address/
    deposit/client/ad cells, a hidden hdnCancelled input, and a 'View ad' link that
    calls GetAd(<id>)."""
    rows = []
    for header in soup.select("div.us-block-header"):
        date_el = header.select_one(".us-date")
        county_el = header.select_one(".us-countyname")
        sale_date = date_from_heading(clean_text(date_el.get_text(" "))) if date_el else ""
        county_raw = clean_text(county_el.get_text(" ")) if county_el else ""
        county = ""
        for c in COUNTIES:
            if c.lower().replace("'", "") == county_raw.lower().replace("'", ""):
                county = c
                break
        if not county and county_raw:
            county = county_raw
        block = header.find_next_sibling("div", class_="us-sales-block")
        if not block:
            continue
        for item in block.select("div.us-sale-item"):
            if "us-sale-header" in (item.get("class") or []):
                continue
            hidden = item.find("input", attrs={"name": re.compile("hdnCancelled")})
            if hidden is not None and str(hidden.get("value", "0")).strip() not in {"0", "", "False", "false"}:
                continue
            txt = clean_text(item.get_text(" "))
            if is_cancelled_text(txt) or item.find(["s", "strike", "del"]):
                continue
            time_el = item.select_one(".us-sale-time")
            addr_el = item.select_one(".us-sale-address")
            dep_el = item.select_one(".us-sale-deposit")
            sale_time = clean_text(time_el.get_text(" ")) if time_el else ""
            address = ""
            if addr_el is not None:
                link_el = addr_el.select_one('a[id*="lnkMap_"]') or addr_el.select_one("a[href*='maps.google']")
                address = clean_text(link_el.get_text(" ")) if link_el else clean_text(addr_el.get_text(" "))
            address = clean_text(address.replace("HUD SALE:", ""))
            # Drop trailing sale notes TW appends after the address (e.g. HUD deposit instructions).
            address = re.split(r"\s+-\s+-?\s*|\s+-\s+ALL\b|\bALL DEPOSITS\b", address, maxsplit=1)[0].strip(" -")
            deposit = clean_text(dep_el.get_text(" ")) if dep_el else ""
            if not re.search(r"\d{1,2}:\d{2}", sale_time) or not re.search(r"\d", address):
                continue
            ad_link = ""
            ad_a = item.select_one(".us-sale-ad a[href]")
            if ad_a is not None:
                m = re.search(r"GetAd\((\d+)\)", ad_a.get("href", ""))
                if m:
                    ad_link = _tw_fetch_ad(m.group(1))
                else:
                    ad_link = normalize_ad_url(ad_a.get("href", ""), URLS["TW"])
            rows.append({
                "source": "TW", "auctioneer": "TW", "sale date": sale_date, "sale time": sale_time.upper(),
                "county": county, "address": address, "deposit": deposit or "SEE AD", "status": "Active",
                "ad link": ad_link,
            })
    return rows


def parse_tw():
    html = fetch(URLS["TW"])
    soup = BeautifulSoup(html, "html.parser")

    # Current TW layout (div blocks). This is the primary parser as of Sep 2026.
    rows = parse_tw_blocks(soup)
    if rows:
        return dedupe(rows)

    rows = []

    # First pass: real table/TR rows, where gray/cancelled styling is detectable.
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        all_text = clean_text(table.get_text(" ")).lower()
        if not any(x in all_text for x in ["address", "deposit", "view ad", "client"]):
            continue

        context_parts = []
        node = table
        for _ in range(45):
            node = node.find_previous()
            if not node:
                break
            t = clean_text(node.get_text(" "))
            if t and len(t) < 220 and t not in context_parts:
                context_parts.append(t)
        context = " ".join(reversed(context_parts[-16:]))
        sale_date = date_from_heading(context) or date_from_heading(clean_text(table.get_text(" ")))

        county = ""
        for c in COUNTIES:
            if c.lower().replace("'", "") in context.lower().replace("'", ""):
                county = c
                break

        for tr in trs:
            rec = _tw_row_to_record(tr, sale_date, county, URLS["TW"])
            if rec:
                rows.append(rec)

    # Second pass: any TR rows outside normal tables.
    if not rows:
        current_date = ""
        current_county = ""
        for tr in soup.find_all("tr"):
            txt = clean_text(tr.get_text(" "))
            d = date_from_heading(txt)
            if d:
                current_date = d
            for c in COUNTIES:
                if c.lower().replace("'", "") in txt.lower().replace("'", ""):
                    current_county = c
                    break
            rec = _tw_row_to_record(tr, current_date, current_county, URLS["TW"])
            if rec:
                rows.append(rec)

    # Final pass: current TW page text layout. This is the key fix for TW: 0 rows.
    if not rows:
        rows = parse_tw_text_fallback(soup, html)

    return dedupe(rows)

def parse_hw():
    html = fetch(URLS["HW"])
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        head = clean_text(trs[0].get_text(" ")).lower()
        if "sale time" not in head or "deposit" not in head:
            continue
        for tr in trs[1:]:
            cells = [clean_text(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
            if len(cells) < 6:
                continue
            raw, deposit, addr, city, zipc, loc = cells[:6]
            if not re.search(r"\d{4}-\d{2}-\d{2}", raw):
                continue
            d = re.search(r"(\d{4}-\d{2}-\d{2})", raw).group(1)
            tm = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", raw, re.I)
            time = tm.group(1) if tm else ""
            full = ", ".join([addr, city, "MD " + zipc])
            link = row_direct_link(tr, URLS["HW"])
            rows.append({"source":"HW","auctioneer":"HW","sale date":d,"sale time":time,"county":loc,"address":full,"deposit":deposit,"status":"Active","ad link":link})
    return dedupe(rows)

def parse_mwc():
    rows = []
    for url, state in [(URLS["MWC_MD"], "MD"), (URLS["MWC_DC"], "DC")]:
        try:
            tables = pd.read_html(fetch(url))
        except Exception:
            continue
        for df in tables:
            df.columns = [clean_text(c).lower() for c in df.columns]
            if "sale date" not in df.columns or "address" not in df.columns:
                continue
            for _, r in df.iterrows():
                county = "Washington, DC" if state == "DC" else clean_text(r.get("county", ""))
                address = ", ".join([x for x in [clean_text(r.get("address","")), clean_text(r.get("city","")), state] if x])
                rows.append({"source":"MWC","auctioneer":"MWC","sale date":clean_text(r.get("sale date","")),"sale time":clean_text(r.get("sale time","")),"county":county,"address":address,"deposit":"SEE AD","status":"Active","ad link":url})
    return dedupe(rows)

def parse_bl():
    """A. J. Billig & Co. (ajbillig.com). Adds coverage that AC/TW/HW/MWC miss,
    including Howard County. Cards are static HTML: <div class="grid-cell">.
    """
    html = fetch(URLS["BL"])
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    now = datetime.now()
    for cell in soup.select("div.grid-cell"):
        addr_p = None
        for p in cell.find_all("p"):
            if p.find("br") and re.search(r"\d", clean_text(p.get_text(" "))):
                addr_p = p
                break
        if addr_p is None:
            continue
        lines = [clean_text(x) for x in addr_p.get_text("\n").split("\n") if clean_text(x)]
        if len(lines) < 2:
            continue
        street = lines[0]
        city_line = lines[1] if len(lines) > 1 else ""
        county = lines[2] if len(lines) > 2 else ""
        if not county:
            for c in COUNTIES:
                if c.lower().replace(" county", "") in cell.get_text(" ").lower():
                    county = c
                    break
        address = ", ".join([x for x in [street, city_line] if x])
        if not re.search(r"\d", address):
            continue

        sale_date, sale_time = "", ""
        for sub in cell.select("div.subheading"):
            txt = clean_text(sub.get_text(" "))
            m = re.search(
                r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(\d{1,2})\D+(\d{1,2}:\d{2}\s*(?:AM|PM))",
                txt, re.I,
            )
            if m:
                month = MONTHS[m.group(1).upper()]
                day = int(m.group(2))
                year = now.year if int(month) >= now.month else now.year + 1
                sale_date = f"{month}/{day:02d}/{year}"
                sale_time = m.group(3).upper().replace(" ", "")
                break
        if not sale_date or not sale_time:
            continue

        link = ""
        a = cell.select_one("a.button[href]") or cell.select_one("a[href]")
        if a:
            link = normalize_ad_url(a.get("href", ""), URLS["BL"])

        rows.append({
            "source": "BL", "auctioneer": "BL", "sale date": sale_date, "sale time": sale_time,
            "county": county, "address": address, "deposit": "SEE AD", "status": "Active",
            "ad link": link,
        })
    return dedupe(rows)

ADC_QUERY = """query resiSearch_blueprint_seekListingsFromFilters($filters: ListingCompatabilityFilters!) {
  seek_listings_from_filters(filters: $filters) { total_count content { ... on Listing {
    listing_id listing_status listing_page_path formatted_address(format: DOUBLE_LINE)
    listing_configuration { product_type asset_type occupancy_status trustee_sales_channel }
    trustee { sale_time }
    venue { venue_type venue_name venue_address { street_number street_name municipality country_primary_subdivision postal_code } }
    auction { auction_date }
    seller_property { street_description municipality country_primary_subdivision country_secondary_subdivision postal_code }
    selling_method(resolvePolicy: CACHE_ONLY) { __typename ... on LiveAuctionSegment { starting_bid_amount configuration { state_deposit_rule } } }
  } } } }"""

ADC_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Auction-Graph-Source": "auctioncom",
    "user-agent": "adc/fetch/resi_search",
}


def parse_adc(include_concierge=False):
    """Auction.com in-person foreclosure (trustee) sales for MD + DC via graph.auction.com.

    Only sales Auction.com conducts itself (trustee_sales_channel == AUCTIONEERING) are
    imported by default. CONCIERGE rows are third-party sales that Auction.com merely
    lists; those are already covered by AC/TW/HW/MWC/BL and would duplicate them.
    The app also de-duplicates across sources by street address as a safety net.
    """
    rows = []
    for state in ["MD", "DC"]:
        variables = {"filters": {"property_state": state, "listing_type": "active", "sort": "auction_date_order", "limit": 500, "version": 1, "offset": 0}}
        try:
            r = requests.post(URLS["ADC_GRAPH"], headers=ADC_HEADERS, json={"query": ADC_QUERY, "variables": variables}, timeout=REQUEST_TIMEOUT + 12)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        items = (((data or {}).get("data") or {}).get("seek_listings_from_filters") or {}).get("content") or []
        for it in items:
            cfg = it.get("listing_configuration") or {}
            venue = it.get("venue") or {}
            if (venue.get("venue_type") or "").upper() != "LIVE":
                continue
            if (cfg.get("product_type") or "").upper() != "TRUSTEE":
                continue
            channel = (cfg.get("trustee_sales_channel") or "").upper()
            if not include_concierge and channel != "AUCTIONEERING":
                continue
            if is_cancelled_text(str(it.get("listing_status") or "")):
                continue
            sale_date = (it.get("auction") or {}).get("auction_date") or ""
            if not sale_date:
                continue
            sale_time = ((it.get("trustee") or {}).get("sale_time") or "").strip()
            if sale_time:
                try:
                    hh, mm = sale_time.split(":")[:2]
                    hh = int(hh)
                    sale_time = f"{(hh % 12) or 12}:{mm} {'PM' if hh >= 12 else 'AM'}"
                except Exception:
                    sale_time = ""
            fa = it.get("formatted_address") or []
            sp = it.get("seller_property") or {}
            street = clean_text(fa[0]) if fa else clean_text(sp.get("street_description", ""))
            city = clean_text(sp.get("municipality", "")).title()
            st_abbr = clean_text(sp.get("country_primary_subdivision", "")) or state
            zipc = clean_text(sp.get("postal_code", ""))
            address = ", ".join([x for x in [street, city, f"{st_abbr} {zipc}".strip()] if x])
            county = ""
            if len(fa) > 1 and "County" in fa[1]:
                county = clean_text(fa[1].split(",")[-1])
            if not county:
                county = clean_text(sp.get("country_secondary_subdivision", ""))
                if county and not county.lower().endswith("county") and county.lower() != "baltimore city":
                    county = county.title() + " County"
            if state == "DC":
                county = "Washington, DC"
            sm = it.get("selling_method") or {}
            deposit = ((sm.get("configuration") or {}).get("state_deposit_rule") or "").strip()
            deposit = deposit if deposit else "SEE AD"
            va = venue.get("venue_address") or {}
            venue_txt = " ".join([x for x in [venue.get("venue_name", ""), "—", va.get("street_number", ""), va.get("street_name", ""), va.get("municipality", "")] if x]).strip(" —")
            link = URLS["ADC_SITE"] + str(it.get("listing_page_path") or "")
            rows.append({
                "source": "ADC", "auctioneer": "ADC", "sale date": sale_date, "sale time": sale_time,
                "county": county, "address": address, "deposit": deposit, "status": "Active",
                "ad link": link, "venue": venue_txt, "occupancy": (cfg.get("occupancy_status") or "").title(),
            })
    return dedupe(rows)

ADC_AVM_QUERY = """query resiSearch_blueprint_seekListingsFromFilters($filters: ListingCompatabilityFilters!) {
  seek_listings_from_filters(filters: $filters) { content { ... on Listing {
    listing_id listing_page_path formatted_address(format: DOUBLE_LINE)
    listing_configuration { product_type occupancy_status }
    venue { venue_type }
    seller_property { street_description municipality postal_code }
    external_information(resolvePolicy: CACHE_ONLY) { collateral { summary { estimated low high type } } }
    primary_property { summary { square_footage year_built lot_size } }
  } } } }"""

AVM_PATH = SCRAPED_DIR / "avm_adc.json"


def _first(x):
    if isinstance(x, list):
        return x[0] if x else {}
    return x or {}


def refresh_avm():
    """Estimated market values (Cotality AVM via Auction.com) for every live trustee sale
    in MD/DC, keyed by loose street address so rows from AC/TW/HW/MWC/BL match too.
    Writes SCRAPED_DIR/avm_adc.json. Cheap: two requests."""
    out = {}
    for state in ["MD", "DC"]:
        variables = {"filters": {"property_state": state, "listing_type": "active", "sort": "auction_date_order", "limit": 500, "version": 1, "offset": 0}}
        try:
            r = requests.post(URLS["ADC_GRAPH"], headers=ADC_HEADERS, json={"query": ADC_AVM_QUERY, "variables": variables}, timeout=REQUEST_TIMEOUT + 12)
            r.raise_for_status()
            items = (((r.json() or {}).get("data") or {}).get("seek_listings_from_filters") or {}).get("content") or []
        except Exception:
            continue
        for it in items:
            if (it.get("venue") or {}).get("venue_type") != "LIVE":
                continue
            fa = it.get("formatted_address") or []
            sp = it.get("seller_property") or {}
            street = clean_text(fa[0]) if fa else clean_text(sp.get("street_description", ""))
            address = ", ".join([x for x in [street, clean_text(sp.get("municipality", "")).title(), f"{state} {clean_text(sp.get('postal_code', ''))}".strip()] if x])
            key = loose_address_key(address)
            if not key:
                continue
            summaries = (_first(it.get("external_information")).get("collateral") or {})
            summaries = _first(summaries).get("summary") if isinstance(summaries, list) else summaries.get("summary")
            if not isinstance(summaries, list):
                summaries = [summaries] if summaries else []
            est = {}
            for sm in summaries:
                if not sm:
                    continue
                t = str(sm.get("type") or "").lower()
                if t == "rental":
                    est["rent"] = sm.get("estimated")
                else:
                    est["value"] = sm.get("estimated"); est["low"] = sm.get("low"); est["high"] = sm.get("high")
            ps = ((it.get("primary_property") or {}).get("summary") or {})
            out[key] = {
                "address": address,
                "value": est.get("value"), "low": est.get("low"), "high": est.get("high"), "rent": est.get("rent"),
                "sqft": ps.get("square_footage"), "year": ps.get("year_built"), "lot": ps.get("lot_size"),
                "occupancy": (it.get("listing_configuration") or {}).get("occupancy_status") or "",
                "adc_url": URLS["ADC_SITE"] + str(it.get("listing_page_path") or ""),
            }
    if out:
        SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
        AVM_PATH.write_text(json.dumps({"updated": datetime.now().isoformat(timespec="seconds"), "rows": out}), encoding="utf-8")
    return out


def load_avm():
    try:
        return json.loads(AVM_PATH.read_text(encoding="utf-8")).get("rows", {})
    except Exception:
        return {}

# ---------------------------------------------------------------------------
# Property values: Cotality AVM via Auction.com (market estimate, partial coverage)
# + Maryland SDAT assessment via opendata.maryland.gov (every MD parcel).
# ---------------------------------------------------------------------------
VALUES_PATH = SCRAPED_DIR / "values.json"
SDAT_URL = "https://opendata.maryland.gov/resource/ed4q-f8tm.json"
SDAT_HEADERS = {"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"}
SDAT_SELECT = ",".join([
    "mdp_street_address_mdp_field_address",
    "mdp_street_address_city_mdp_field_city",
    "current_assessment_year_total_assessment_sdat_field_172",
    "c_a_m_a_system_data_structure_area_sq_ft_mdp_field_sqftstrc_sdat_field_241",
    "c_a_m_a_system_data_year_built_yyyy_mdp_field_yearblt_sdat_field_235",
    "sales_segment_1_consideration_mdp_field_considr1_sdat_field_90",
    "sales_segment_1_transfer_date_yyyy_mm_dd_mdp_field_tradate_sdat_field_89",
    "record_key_owner_occupancy_code_mdp_field_ooi_sdat_field_6",
    "land_use_code_mdp_field_lu_desclu_sdat_field_50",
    "record_key_county_code_sdat_field_1",
    "record_key_district_ward_sdat_field_2",
    "record_key_account_number_sdat_field_3",
])


def _num(v):
    try:
        f = float(str(v).replace(",", "").replace("$", "").strip())
        return int(f) if f else 0
    except Exception:
        return 0


def sdat_lookup(address):
    """Maryland SDAT record for one street address (zip + house number, then street word match)."""
    key = loose_address_key(address)
    parts = key.split("|")
    if len(parts) != 3 or not all(parts):
        return None
    num, word, zipc = parts
    params = {
        "premise_address_zip_code_mdp_field_premzip_sdat_field_26": zipc,
        "premise_address_number_mdp_field_premsnum_sdat_field_20": num.zfill(5),
        "$limit": 50,
        "$select": SDAT_SELECT,
    }
    try:
        r = requests.get(SDAT_URL, params=params, headers=SDAT_HEADERS, timeout=30)
        if r.status_code != 200:
            return None
        rows = [x for x in r.json() if word in str(x.get("mdp_street_address_mdp_field_address", "")).upper().split()]
    except Exception:
        return None
    if not rows:
        return None
    x = rows[0]
    county = x.get("record_key_county_code_sdat_field_1", "")
    district = x.get("record_key_district_ward_sdat_field_2", "")
    acct = x.get("record_key_account_number_sdat_field_3", "")
    link = ""
    if county and acct:
        link = f"https://sdat.dat.maryland.gov/RealProperty/Pages/viewdetails.aspx?County={county}&SearchType=ACCT&District={district}&AccountNumber={acct}"
    return {
        "assessed": _num(x.get("current_assessment_year_total_assessment_sdat_field_172")),
        "sqft": _num(x.get("c_a_m_a_system_data_structure_area_sq_ft_mdp_field_sqftstrc_sdat_field_241")),
        "year": _num(x.get("c_a_m_a_system_data_year_built_yyyy_mdp_field_yearblt_sdat_field_235")),
        "last_sale": _num(x.get("sales_segment_1_consideration_mdp_field_considr1_sdat_field_90")),
        "last_sale_date": str(x.get("sales_segment_1_transfer_date_yyyy_mm_dd_mdp_field_tradate_sdat_field_89") or "")[:10],
        "owner_occupied": str(x.get("record_key_owner_occupancy_code_mdp_field_ooi_sdat_field_6") or "").upper() == "H",
        "land_use": str(x.get("land_use_code_mdp_field_lu_desclu_sdat_field_50") or ""),
        "sdat_url": link,
    }


def load_values():
    try:
        return json.loads(VALUES_PATH.read_text(encoding="utf-8")).get("rows", {})
    except Exception:
        return {}


def refresh_property_values(addresses, force=False, max_new=80):
    """Merge AVM (Auction.com) + SDAT assessment for the given addresses into values.json.
    Cached keys are not re-fetched unless force=True. Bounded to max_new lookups per call."""
    values = load_values()
    avm = refresh_avm() or load_avm()
    fetched = 0
    for addr in addresses:
        key = loose_address_key(addr)
        if not key:
            continue
        rec = values.get(key, {})
        a = avm.get(key)
        if a:
            rec.update({"est": a.get("value") or rec.get("est"), "est_low": a.get("low"), "est_high": a.get("high"), "rent": a.get("rent"),
                        "sqft": rec.get("sqft") or a.get("sqft"), "year": rec.get("year") or a.get("year"), "adc_url": a.get("adc_url")})
        needs_sdat = force or ("assessed" not in rec)
        if needs_sdat and fetched < max_new and "MD" in str(addr).upper():
            sd = sdat_lookup(addr)
            fetched += 1
            if sd:
                rec.update(sd)
            else:
                rec.setdefault("assessed", 0)  # remember the miss so we do not retry every scrape
        if rec:
            rec["address"] = addr
            values[key] = rec
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    VALUES_PATH.write_text(json.dumps({"updated": datetime.now().isoformat(timespec="seconds"), "rows": values}), encoding="utf-8")
    return values

def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        key = (r.get("sale date",""), r.get("sale time",""), r.get("address","").upper())
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out

def scrape_source(source, clear_old=False):
    if clear_old:
        clear_cache()
    parser = {"AC": parse_ac, "TW": parse_tw, "HW": parse_hw, "MWC": parse_mwc, "BL": parse_bl, "ADC": parse_adc}[source]
    rows = parser()
    path = write_rows(source, rows)
    try:
        refresh_property_values([r.get("address", "") for r in rows])
    except Exception:
        pass
    return {"ok": True, "rows": len(rows), "path": str(path or ""), "error": ""}

def scrape_many(sources, clear_old=True):
    """Scrape selected sources with bounded work.

    Important: no property-by-property external lookups here. Each source either
    returns cached CSV rows quickly or fails safely, so the app does not sit for
    10+ minutes.
    """
    if clear_old:
        clear_cache()
    out = {}
    for s in sources:
        try:
            out[s] = scrape_source(s, clear_old=False)
        except Exception as e:
            out[s] = {"ok": False, "rows": 0, "path": "", "error": str(e)}
    return out
