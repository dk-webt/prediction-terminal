import os
from pathlib import Path

# Load .env file if present
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass

KALSHI_API_KEY: str | None = os.environ.get("KALSHI_API_KEY")
KALSHI_API_EMAIL: str | None = os.environ.get("KALSHI_API_EMAIL")
KALSHI_API_PASSWORD: str | None = os.environ.get("KALSHI_API_PASSWORD")
GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")
