
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
SCRAPED_DIR = DATA_DIR / "input" / "scraped"
LOCAL_DIR = DATA_DIR / "local"
EXPORT_DIR = ROOT_DIR / "exports"

BIDS_PATH = LOCAL_DIR / "saved_bids.csv"
HIDDEN_PATH = LOCAL_DIR / "hidden_properties.txt"
BLOCKED_CITIES_PATH = LOCAL_DIR / "blocked_cities.txt"
LAYOUT_PATH = LOCAL_DIR / "layout_defaults.json"
