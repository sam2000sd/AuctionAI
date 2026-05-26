FINAL WORKING BUILD

Upload the CONTENTS of this folder to GitHub root.

Streamlit main file path:
app/Auction_Intelligence.py

Included sources:
AC, TW, HW, MWC

Confirmed:
TW and HW scrape live online.
AC is included and uses Playwright browser rendering. First AC scrape can take longer on Streamlit Cloud while Chromium installs.
MWC remains included, but may return 0 if the page/table structure changes.

This package starts clean, with no bundled stale scraped CSV files.
