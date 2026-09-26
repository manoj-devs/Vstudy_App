# ─────────────────────────────────────────
#  VSTUDY / TELEGRAM SETTINGS (from environment)
# ─────────────────────────────────────────
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent

VSTUDY_URL = os.getenv("VSTUDY_URL", "https://vstudy.saveetha.com/")
VSTUDY_PROFILE_DIR = os.getenv(
    "VSTUDY_PROFILE_DIR",
    "/data/vstudy_chrome_profile",
)
CHROME_RUNTIME_DIR = os.getenv("CHROME_RUNTIME_DIR", "/tmp/chrome")

# ─────────────────────────────────────────
#  TELEGRAM BOT SETTINGS (from environment)
# ─────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────
#  MONITOR SETTINGS
# ─────────────────────────────────────────
CHECK_INTERVAL = 5 * 60
DATABASE_NAME = os.getenv("DATABASE_NAME", str(BASE_DIR / "results.db"))
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", str(BASE_DIR / "monitor.heartbeat"))
HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", str(15 * 60)))
HEADLESS = os.getenv("HEADLESS", "false").strip().lower() in {"1", "true", "yes"}
UNATTENDED = os.getenv("UNATTENDED", "false").strip().lower() in {"1", "true", "yes"}
RUN_FOREVER = os.getenv("RUN_FOREVER", "false").strip().lower() in {"1", "true", "yes"}
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", str(CHECK_INTERVAL)))
SAVE_DEBUG_ARTIFACTS = os.getenv("SAVE_DEBUG_ARTIFACTS", "false").strip().lower() in {"1", "true", "yes"}
LOG_COURSE_RESULTS = os.getenv("LOG_COURSE_RESULTS", "true").strip().lower() in {"1", "true", "yes"}

# Debug: Print environment variable presence without exposing secrets
print(f"DEBUG -> VSTUDY_PROFILE_DIR: {VSTUDY_PROFILE_DIR}")
print(f"DEBUG -> TELEGRAM_TOKEN set: {bool(TELEGRAM_TOKEN)}")
print(f"DEBUG -> TELEGRAM_CHAT_ID set: {bool(TELEGRAM_CHAT_ID)}")