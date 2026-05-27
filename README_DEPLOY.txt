Streamlit Cloud deployment

Main file: app/Auction_Intelligence.py

Important: packages.txt intentionally does NOT include libcups2. On Streamlit Cloud's current Debian image, libcups2 conflicts with libgtk-3-0/libcups2t64 and breaks the build before the app starts.

If AC loads but returns 0 rows, do not change the whole app. Only inspect the AC scraper in app/scrapers/sources.py.
