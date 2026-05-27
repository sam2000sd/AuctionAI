Streamlit Cloud deploy:
1. Push this package to GitHub.
2. Main file path: app/Auction_Intelligence.py
3. Do NOT add packages.txt for now. The prior packages.txt caused Debian libcups/libgtk conflicts before the app could even start.
4. The app installs Playwright's Chromium browser at runtime with: python -m playwright install chromium

This package intentionally changes only deployment dependency handling, not the AC scraping/parser logic.
