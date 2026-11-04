import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

GEE_PROJECT = os.getenv("GEE_PROJECT", "")
USE_MOCK_GEE = os.getenv("USE_MOCK_GEE", "false").lower() == "true"
ALLOW_MOCK_FALLBACK = os.getenv("ALLOW_MOCK_FALLBACK", "true").lower() == "true"
HANSEN_ASSET = os.getenv("HANSEN_ASSET", "UMD/hansen/global_forest_change_2024_v1_12")
CUTOFF_YEAR = int(os.getenv("CUTOFF_YEAR", "2020"))
DB_URL = os.getenv("DB_URL", f"sqlite:///{BASE_DIR / 'eudr.db'}")
