"""
Beat the Streak - Configuration
Reads credentials from environment variables or .env file.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env if present (pip install python-dotenv)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # dotenv not installed; rely on env vars

# ---------------------------------------------------------------------------
# MySQL connection
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "host":     os.getenv("BTS_DB_HOST"),
    "port":     int(os.getenv("BTS_DB_PORT", 3306)),
    "user":     os.getenv("BTS_DB_USER"),
    "password": os.getenv("BTS_DB_PASS"),
    "database": os.getenv("BTS_DB_NAME"),
    "charset":  "utf8mb4",
    "sql_mode": "STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION",
}

# ---------------------------------------------------------------------------
# Seasons / model
# ---------------------------------------------------------------------------
CURRENT_SEASON = int(os.getenv("BTS_SEASON", 2026))
PREVIOUS_SEASON = CURRENT_SEASON - 1  # for preseason baseline stats

# How many prior seasons of stats to cache for features
LOOKBACK_SEASONS = 3

# Model version tag (bump when retraining)
MODEL_VERSION = os.getenv("BTS_MODEL_VERSION", "v0.1.0")

# ---------------------------------------------------------------------------
# Expected PAs by batting order position (MLB averages)
# Position 1 (leadoff) gets ~4.8 PA/game, position 9 ~3.9
# ---------------------------------------------------------------------------
EXPECTED_PA_BY_ORDER = {
    1: 4.8, 2: 4.7, 3: 4.5, 4: 4.4, 5: 4.3,
    6: 4.2, 7: 4.1, 8: 4.0, 9: 3.9,
}

# ---------------------------------------------------------------------------
# Confidence tier thresholds for P(at least 1 hit)
# ---------------------------------------------------------------------------
TIER_THRESHOLDS = {
    "A": 0.82,  # strong pick
    "B": 0.77,  # solid pick
    "C": 0.72,  # average — proceed with caution
    # Below C = tier D (avoid)
}

# ---------------------------------------------------------------------------
# Model Baselines (League Averages)
# Update these via monitor_baselines.py
# ---------------------------------------------------------------------------
LEAGUE_BA_BASELINE = 0.249
LEAGUE_BAA_BASELINE = 0.282

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
MODELS_DIR = PROJECT_DIR / "models"
LOGS_DIR = PROJECT_DIR / "logs"
CHADWICK_REGISTER_PATH = DATA_DIR / "chadwick_register.csv"

TEAM_ABBR_MAP = {
    "Athletics": "OAK",
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}

for d in [DATA_DIR, MODELS_DIR, LOGS_DIR]:
    d.mkdir(exist_ok=True)
