Auction Intelligence deployment notes

Main file path:
app/Auction_Intelligence.py

What was fixed in this version:
1. Saved bids, archive, hidden properties, blocked cities, and layout are now written atomically, with local timestamped backups.
2. The app now supports durable GitHub Gist cloud backup. This is the real fix for Streamlit Cloud sleep/restart wiping local files.
3. The UI now has a Backup Archive CSV button and a Restore Archive CSV uploader.
4. The app includes a keep-awake browser ping while the tab is open. This can reduce idle sleeping, but it cannot defeat a host-level hard restart. Durable storage is still required.

IMPORTANT: Streamlit Cloud local files are not reliable storage.
If the app sleeps/restarts/redeploys, files written under data/local can disappear. That is a hosting issue, not a button/save issue. Use one of these two durable options:

OPTION A, recommended for Streamlit Cloud: GitHub Gist backup
1. Create a private GitHub Gist.
2. Add one blank file to it called saved_bids.csv.
3. Copy the Gist ID from the URL.
4. Create a GitHub fine-grained token with Gist read/write access.
5. In Streamlit Cloud, open App settings > Secrets and add:

GITHUB_TOKEN = "your_token_here"
GIST_ID = "your_gist_id_here"

6. Reboot the app. The sidebar should say: Storage: durable GitHub Gist backup is ON.

OPTION B: host with persistent disk
If using Render/Railway/VPS/Docker with mounted disk, set:
AUCTIONAI_DATA_DIR=/path/to/persistent/disk/auctionai-data

Then saved_bids.csv, hidden_properties.txt, blocked_cities.txt, and layout_defaults.json will live there.

Manual backup safety:
Use the Backup Archive button regularly. If anything looks missing, use Restore archive from backup CSV.

Streamlit Cloud deploy:
1. Push this package to GitHub.
2. Main file path: app/Auction_Intelligence.py
3. Do NOT add packages.txt for now. The prior packages.txt caused Debian libcups/libgtk conflicts before the app could even start.
4. The app installs Playwright's Chromium browser at runtime with: python -m playwright install chromium
