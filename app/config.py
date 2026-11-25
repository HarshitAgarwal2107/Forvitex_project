"""Infrastructure settings. Product identity lives in products/."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IST = timezone(timedelta(hours=5, minutes=30))
DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "data" / "sales.db")

AGENT_NAME = "sales-agent"


def env(name: str, default: str = "") -> str:
    return os.getenv(name) or default


def now() -> datetime:
    return datetime.now(IST)
