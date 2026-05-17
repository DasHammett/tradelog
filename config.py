import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'tradelog.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # IBKR Flex Query
    FLEX_TOKEN = os.environ.get("FLEX_TOKEN", "")
    FLEX_QUERY_ID = os.environ.get("FLEX_QUERY_ID", "")

    # Scheduler — run Flex sync daily at this hour (24h)
    FLEX_SYNC_HOUR = int(os.environ.get("FLEX_SYNC_HOUR", 18))
