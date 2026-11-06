"""Runtime configuration for SkipTrace V1.

Every secret comes from the environment. Nothing here is committed with a
value, and the module never writes configuration to disk.
"""

import os

# Required. bot.py exits with a clear error when this is missing.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# SQLite audit log location.
DB_PATH = os.environ.get("DB_PATH", "skiptrace.db")

# Maigret search behaviour.
MAIGRET_TIMEOUT = 12
MAIGRET_MAX_CONNECTIONS = 4
MAIGRET_TAGS = ["social", "dating", "forum", "misc"]

# Scoring / reporting thresholds.
MIN_SCORE_TO_REPORT = 40

# Name variation caps.
MAX_NAME_VARIATIONS = 6
MAX_SURNAME_VARIATIONS = 6
MAX_USERNAMES = 25
FUZZY_THRESHOLD = 0.80

# Politeness delay between batches of username lookups.
BATCH_SLEEP_SECONDS = 1.2
