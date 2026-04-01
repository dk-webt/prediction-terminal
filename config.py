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
KALSHI_PRIVATE_KEY_PATH: str | None = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
KALSHI_API_EMAIL: str | None = os.environ.get("KALSHI_API_EMAIL")
KALSHI_API_PASSWORD: str | None = os.environ.get("KALSHI_API_PASSWORD")
GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")

# Coinbase CDP (for BRTI tracker L2 order book feed)
COINBASE_CDP_API_KEY: str | None = os.environ.get("COINBASE_CDP_API_KEY")
COINBASE_CDP_API_SECRET: str | None = os.environ.get("COINBASE_CDP_API_SECRET")

# Polymarket execution (CLOB)
# POLYMARKET_PRIVATE_KEY: MetaMask private key (signer — signs orders, does NOT hold funds)
# POLYMARKET_WALLET_ADDRESS: Safe/proxy wallet address from Polymarket Settings (holds funds)
# L2 API creds: derived from private key via create_or_derive_api_creds() — store after first run
# Builder keys: from Polymarket Settings → Builder (enables gasless trading, no POL needed)
POLYMARKET_PRIVATE_KEY: str | None = os.environ.get("POLYMARKET_PRIVATE_KEY")
POLYMARKET_WALLET_ADDRESS: str | None = os.environ.get("POLYMARKET_WALLET_ADDRESS")
POLYMARKET_API_KEY: str | None = os.environ.get("POLYMARKET_API_KEY")
POLYMARKET_API_SECRET: str | None = os.environ.get("POLYMARKET_API_SECRET")
POLYMARKET_API_PASSPHRASE: str | None = os.environ.get("POLYMARKET_API_PASSPHRASE")
POLYMARKET_BUILDER_KEY: str | None = os.environ.get("POLYMARKET_BUILDER_KEY")
POLYMARKET_BUILDER_SECRET: str | None = os.environ.get("POLYMARKET_BUILDER_SECRET")
POLYMARKET_BUILDER_PASSPHRASE: str | None = os.environ.get("POLYMARKET_BUILDER_PASSPHRASE")
