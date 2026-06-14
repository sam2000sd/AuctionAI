from pathlib import Path
import os

ROOT_DIR = Path(__file__).resolve().parents[2]

# Default local storage lives inside the package for desktop use.
# On hosted apps, set AUCTIONAI_DATA_DIR to a mounted/persistent disk path when available.
DATA_DIR = Path(os.getenv("AUCTIONAI_DATA_DIR", str(ROOT_DIR / "data"))).expanduser().resolve()
SCRAPED_DIR = DATA_DIR / "input" / "scraped"
LOCAL_DIR = DATA_DIR / "local"
EXPORT_DIR = ROOT_DIR / "exports"

BIDS_PATH = LOCAL_DIR / "saved_bids.csv"
HIDDEN_PATH = LOCAL_DIR / "hidden_properties.txt"
BLOCKED_CITIES_PATH = LOCAL_DIR / "blocked_cities.txt"
LAYOUT_PATH = LOCAL_DIR / "layout_defaults.json"
FAVORITE_PROPERTIES_PATH = LOCAL_DIR / "favorite_properties.txt"
