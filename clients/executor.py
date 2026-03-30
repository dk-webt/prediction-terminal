"""
Trading execution layer for BTC 15-min binary options.

Supports order placement on both Kalshi (RSA-PSS auth) and Polymarket (CLOB client).
"""

import base64
import json
import logging
import time
import uuid

import requests

from config import (
    KALSHI_API_KEY,
    KALSHI_PRIVATE_KEY_PATH,
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_WALLET_ADDRESS,
    POLYMARKET_API_KEY,
    POLYMARKET_API_SECRET,
    POLYMARKET_API_PASSPHRASE,
)

log = logging.getLogger(__name__)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


# ── Kalshi RSA-PSS auth (reuses signing pattern from btc_watcher) ────────────


def _load_kalshi_private_key():
    """Load the RSA private key from the path in KALSHI_PRIVATE_KEY_PATH."""
    if not KALSHI_PRIVATE_KEY_PATH:
        return None
    from pathlib import Path
    path = Path(KALSHI_PRIVATE_KEY_PATH).expanduser()
    if not path.exists():
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        return load_pem_private_key(path.read_bytes(), password=None)
    except Exception as e:
        log.warning("Failed to load Kalshi private key: %s", e)
        return None


def _kalshi_rest_auth_headers(method: str, path: str) -> dict | None:
    """
    Build RSA-PSS auth headers for Kalshi REST API.
    method: "GET", "POST", "DELETE", etc.
    path: e.g. "/trade-api/v2/portfolio/orders"
    """
    if not KALSHI_API_KEY or not KALSHI_PRIVATE_KEY_PATH:
        return None

    private_key = _load_kalshi_private_key()
    if not private_key:
        return None

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp_ms = str(int(time.time() * 1000))
        message = timestamp_ms + method + path

        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }
    except Exception as e:
        log.warning("Kalshi REST auth signing failed: %s", e)
        return None


def kalshi_auth_available() -> bool:
    return bool(KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH)


def polymarket_auth_available() -> bool:
    return bool(POLYMARKET_PRIVATE_KEY and POLYMARKET_WALLET_ADDRESS)


# ── Kalshi order placement ───────────────────────────────────────────────────


def place_kalshi_order(
    ticker: str,
    action: str,       # "buy" or "sell"
    side: str,          # "yes" or "no"
    count: int,
    price: float | None = None,    # dollar price (0.01-0.99), None for market
    order_type: str = "limit",     # "limit" or "market"
) -> dict:
    """
    Place an order on Kalshi.
    Returns {"success": bool, "data": {...}, "error": str|None}
    """
    if not kalshi_auth_available():
        return {"success": False, "error": "Kalshi API keys not configured"}

    path = "/trade-api/v2/portfolio/orders"
    headers = _kalshi_rest_auth_headers("POST", path)
    if not headers:
        return {"success": False, "error": "Failed to sign Kalshi request"}

    body: dict = {
        "ticker": ticker,
        "action": action,
        "side": side,
        "count": count,
        "type": order_type,
        "client_order_id": str(uuid.uuid4()),
    }

    if price is not None and order_type == "limit":
        # Kalshi v2 API accepts dollar prices
        if side == "yes":
            body["yes_price_dollars"] = f"{price:.6f}"
        else:
            body["no_price_dollars"] = f"{price:.6f}"

    try:
        resp = requests.post(
            f"{KALSHI_BASE}/portfolio/orders",
            headers=headers,
            json=body,
            timeout=10,
        )
        data = resp.json()

        if resp.status_code in (200, 201):
            return {"success": True, "data": data}
        else:
            return {
                "success": False,
                "error": data.get("message", data.get("error", f"HTTP {resp.status_code}")),
                "data": data,
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_kalshi_positions() -> dict:
    """Fetch Kalshi positions for KXBTC15M series."""
    if not kalshi_auth_available():
        return {"success": False, "error": "Kalshi API keys not configured"}

    path = "/trade-api/v2/portfolio/positions"
    headers = _kalshi_rest_auth_headers("GET", path)
    if not headers:
        return {"success": False, "error": "Failed to sign Kalshi request"}

    try:
        resp = requests.get(
            f"{KALSHI_BASE}/portfolio/positions",
            headers=headers,
            params={"series_ticker": "KXBTC15M", "limit": 20},
            timeout=10,
        )
        data = resp.json()

        if resp.status_code == 200:
            positions = data.get("market_positions", [])
            return {"success": True, "data": positions}
        else:
            return {"success": False, "error": data.get("message", f"HTTP {resp.status_code}")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_kalshi_balance() -> dict:
    """Fetch Kalshi account balance."""
    if not kalshi_auth_available():
        return {"success": False, "error": "Kalshi API keys not configured"}

    path = "/trade-api/v2/portfolio/balance"
    headers = _kalshi_rest_auth_headers("GET", path)
    if not headers:
        return {"success": False, "error": "Failed to sign request"}

    try:
        resp = requests.get(
            f"{KALSHI_BASE}/portfolio/balance",
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200:
            return {"success": True, "data": data}
        else:
            return {"success": False, "error": data.get("message", f"HTTP {resp.status_code}")}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Polymarket order placement ───────────────────────────────────────────────

_pm_client = None


def _get_pm_client():
    """Lazy-init the Polymarket CLOB client."""
    global _pm_client
    if _pm_client is not None:
        return _pm_client

    if not polymarket_auth_available():
        return None

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        # Use explicit API creds from .env if available (most reliable).
        # Fall back to deriving from private key if not set.
        if POLYMARKET_API_KEY and POLYMARKET_API_SECRET and POLYMARKET_API_PASSPHRASE:
            creds = ApiCreds(
                api_key=POLYMARKET_API_KEY,
                api_secret=POLYMARKET_API_SECRET,
                api_passphrase=POLYMARKET_API_PASSPHRASE,
            )
            log.info("Polymarket API creds loaded from .env")
        else:
            temp = ClobClient(
                "https://clob.polymarket.com",
                key=POLYMARKET_PRIVATE_KEY,
                chain_id=137,
            )
            creds = temp.derive_api_key()
            log.info("Polymarket API creds derived from private key")

        # signature_type=1 (POLY_PROXY) for Polymarket proxy wallets
        _pm_client = ClobClient(
            "https://clob.polymarket.com",
            key=POLYMARKET_PRIVATE_KEY,
            chain_id=137,
            creds=creds,
            signature_type=1,  # POLY_PROXY wallet
            funder=POLYMARKET_WALLET_ADDRESS,
        )
        return _pm_client
    except Exception as e:
        log.warning("Failed to init Polymarket client: %s", e)
        return None


def place_polymarket_order(
    token_id: str,
    side: str,          # "BUY" or "SELL"
    size: float,        # number of shares
    price: float | None = None,    # 0.01-0.99, None for market
    order_type: str = "limit",     # "limit" or "market"
) -> dict:
    """
    Place an order on Polymarket CLOB.
    Returns {"success": bool, "data": {...}, "error": str|None}
    """
    client = _get_pm_client()
    if not client:
        return {"success": False, "error": "Polymarket keys not configured or client init failed"}

    try:
        from py_clob_client.clob_types import (
            OrderArgs, MarketOrderArgs, OrderType, PartialCreateOrderOptions,
        )
        from py_clob_client.order_builder.constants import BUY, SELL

        pm_side = BUY if side.upper() == "BUY" else SELL
        opts = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)

        if order_type == "market" or price is None:
            # Market order — create+sign, then post separately
            signed = client.create_market_order(
                MarketOrderArgs(
                    token_id=token_id,
                    amount=size,
                    side=pm_side,
                ),
                options=opts,
            )
            resp = client.post_order(signed, orderType=OrderType.FOK)
        else:
            # Limit order — create_and_post_order always posts as GTC
            resp = client.create_and_post_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=pm_side,
                ),
                options=opts,
            )

        if isinstance(resp, dict) and resp.get("errorMsg"):
            return {"success": False, "error": resp["errorMsg"], "data": resp}

        return {"success": True, "data": resp if isinstance(resp, dict) else {"response": str(resp)}}

    except Exception as e:
        return {"success": False, "error": str(e)}


def get_polymarket_positions() -> dict:
    """Fetch Polymarket open positions."""
    client = _get_pm_client()
    if not client:
        return {"success": False, "error": "Polymarket keys not configured"}

    try:
        # The CLOB client may not have a direct positions endpoint;
        # use the Polygon subgraph or balance check
        # For now, return empty with success
        return {"success": True, "data": []}
    except Exception as e:
        return {"success": False, "error": str(e)}
