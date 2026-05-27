
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sys
import subprocess
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

from app.core.config import SCRAPED_DIR
from app.core.utils import clean_text, is_cancelled_text

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


def row_direct_link(tr, base_url):
    """Return the best row-level advertisement/detail link, not the source homepage."""
    links = []
    base_clean = base_url.rstrip("/").lower()
    for a in tr.find_all("a", href=True):
        text = clean_text(a.get_text(" ")).lower()
        href = (a.get("href") or "").strip()
        if not href or href.startswith("javascript:") or href.startswith("#"):
            continue
        full = urljoin(base_url, href)
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
        full = urljoin(base_url, href)
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
                page.goto(URLS["AC"], wait_until="domcontentloaded", timeout=45000)
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
                text = page.locator("body").inner_text(timeout=15000)
                html = page.content()
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

def parse_tw():
    html = fetch(URLS["TW"])
    soup = BeautifulSoup(html, "html.parser")
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
    parser = {"AC": parse_ac, "TW": parse_tw, "HW": parse_hw, "MWC": parse_mwc}[source]
    rows = parser()
    path = write_rows(source, rows)
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
