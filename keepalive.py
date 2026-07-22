"""
Keeps a Streamlit Community Cloud app awake.

Why this is needed: Streamlit Cloud apps are single-page apps. A plain HTTP
GET (curl, requests, urllib) only ever returns a static HTML shell - the
Python app itself doesn't start until a real browser executes the page's
JavaScript and opens a WebSocket connection. So simple "ping" scripts return
HTTP 200 while doing absolutely nothing to wake a sleeping app.

This script uses Playwright (a headless, real Chromium browser) to actually
visit the app, wait for it to render, and click the
"Yes, get this app back up!" button if the app is asleep.

Usage:
    python keepalive.py
"""

import sys
from playwright.sync_api import sync_playwright

# Add every app URL you want to keep awake here.
URLS = [
    "https://simoauction.streamlit.app/",
]

WAKE_BUTTON_TEXT = "Yes, get this app back up!"
PAGE_LOAD_TIMEOUT_MS = 120_000   # 2 minutes - cold starts can be slow
POST_CLICK_WAIT_MS = 60_000      # give the app a minute to actually boot


def visit(page, url: str) -> str:
    """Visit a single app URL. Returns 'OK' or 'WOKE' or 'ERROR'."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
    except Exception as e:
        print(f"  ERROR  {url}  ({e})")
        return "ERROR"

    # Let the JS render and the "asleep" banner (if any) appear.
    page.wait_for_timeout(5_000)

    wake_button = page.get_by_role("button", name=WAKE_BUTTON_TEXT)
    if wake_button.count() > 0:
        print(f"  WAKE   {url}  (was asleep, clicking wake button)")
        wake_button.first.click()
        # Give the app time to actually spin up before moving on.
        page.wait_for_timeout(POST_CLICK_WAIT_MS)
        return "WOKE"

    print(f"  OK     {url}  (already awake)")
    return "OK"


def main() -> int:
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            )
        )
        page = context.new_page()

        for url in URLS:
            results[url] = visit(page, url)

        browser.close()

    print("\nSummary:")
    for url, status in results.items():
        print(f"  {status:6s} {url}")

    # Non-zero exit if anything errored, so GitHub Actions flags it.
    return 1 if "ERROR" in results.values() else 0


if __name__ == "__main__":
    sys.exit(main())
