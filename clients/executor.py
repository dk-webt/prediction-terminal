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
    POLYMARKET_BUILDER_KEY,
    POLYMARKET_BUILDER_SECRET,
    POLYMARKET_BUILDER_PASSPHRASE,
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
                salt_length=padding.PSS.DIGEST_LENGTH,
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

    client_order_id = str(uuid.uuid4())
    body: dict = {
        "ticker": ticker,
        "action": action,
        "side": side,
        "count": count,
        "type": order_type,
        "client_order_id": client_order_id,
    }

    if order_type == "market":
        # Kalshi requires a price even for market orders.
        # Price cap must come from live data (best ask + buffer) via api_server.
        # Reject if no cap available — never send a blind high price.
        if price is None:
            return {"success": False, "error": "Market order rejected: no live price data to compute safe cap"}
        cap = f"{price:.2f}"
        if side == "yes":
            body["yes_price_dollars"] = cap
        else:
            body["no_price_dollars"] = cap
    elif price is not None:
        if side == "yes":
            body["yes_price_dollars"] = f"{price:.2f}"
        else:
            body["no_price_dollars"] = f"{price:.2f}"

    try:
        resp = requests.post(
            f"{KALSHI_BASE}/portfolio/orders",
            headers=headers,
            json=body,
            timeout=10,
        )
        data = resp.json()

        if resp.status_code in (200, 201):
            return {"success": True, "data": data, "client_order_id": client_order_id}
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
            params={"series_ticker": "KXBTC15M", "limit": 100, "settlement_status": "unsettled"},
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
_pm_approved_tokens: set[str] = set()  # token_ids with conditional allowance set


def _get_pm_client():
    """
    Lazy-init the Polymarket CLOB client.

    Architecture:
      - Signer (MetaMask private key) signs orders but does NOT hold funds
      - Funder (Safe/proxy wallet from PM Settings) holds USDC and positions
      - L2 API creds authenticate CLOB requests (derived from private key)
      - Builder keys enable gasless trading (optional, from PM Settings → Builder)
      - signature_type=2 (GNOSIS_SAFE) for accounts with proxy/safe wallet
    """
    global _pm_client
    if _pm_client is not None:
        return _pm_client

    if not polymarket_auth_available():
        return None

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        # L2 API creds: use from .env if stored, otherwise derive (one-time)
        if POLYMARKET_API_KEY and POLYMARKET_API_SECRET and POLYMARKET_API_PASSPHRASE:
            creds = ApiCreds(
                api_key=POLYMARKET_API_KEY,
                api_secret=POLYMARKET_API_SECRET,
                api_passphrase=POLYMARKET_API_PASSPHRASE,
            )
            log.info("Polymarket L2 API creds loaded from .env")
        else:
            temp = ClobClient(
                "https://clob.polymarket.com",
                key=POLYMARKET_PRIVATE_KEY,
                chain_id=137,
            )
            creds = temp.create_or_derive_api_creds()
            log.info("Polymarket L2 API creds derived — store these in .env for faster startup:")
            log.info("  POLYMARKET_API_KEY=%s", creds.api_key)
            log.info("  POLYMARKET_API_SECRET=%s", creds.api_secret)
            log.info("  POLYMARKET_API_PASSPHRASE=%s", creds.api_passphrase)

        # Builder keys for gasless trading (optional)
        builder_config = None
        if POLYMARKET_BUILDER_KEY and POLYMARKET_BUILDER_SECRET and POLYMARKET_BUILDER_PASSPHRASE:
            try:
                from py_builder_signing_sdk.config import BuilderConfig
                builder_config = BuilderConfig(
                    api_key=POLYMARKET_BUILDER_KEY,
                    api_secret=POLYMARKET_BUILDER_SECRET,
                    api_passphrase=POLYMARKET_BUILDER_PASSPHRASE,
                )
                log.info("Polymarket builder keys loaded (gasless trading enabled)")
            except ImportError:
                log.warning("py-builder-signing-sdk not installed — builder keys ignored")
            except Exception as e:
                log.warning("Failed to load builder config: %s", e)

        # signature_type=2 (GNOSIS_SAFE) for accounts with proxy/safe wallet
        # funder = Safe wallet address from Polymarket Settings (holds funds)
        kwargs = {
            "host": "https://clob.polymarket.com",
            "key": POLYMARKET_PRIVATE_KEY,
            "chain_id": 137,
            "creds": creds,
            "signature_type": 2,  # GNOSIS_SAFE
            "funder": POLYMARKET_WALLET_ADDRESS,
        }
        if builder_config:
            kwargs["builder_config"] = builder_config

        _pm_client = ClobClient(**kwargs)
        log.info("Polymarket client initialized (signature_type=GNOSIS_SAFE, funder=%s)",
                 POLYMARKET_WALLET_ADDRESS)
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

        # For sells, ensure conditional token allowance is set (once per token_id)
        if pm_side == SELL and token_id not in _pm_approved_tokens:
            try:
                from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                client.update_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
                )
                _pm_approved_tokens.add(token_id)
                log.info("PM conditional token allowance set for %s...", token_id[:20])
            except Exception as e:
                log.warning("PM conditional allowance failed: %s", e)

        # Fetch neg_risk and tick_size from the CLOB for this token
        neg_risk = False
        tick_size = "0.01"
        try:
            neg_risk = client.get_neg_risk(token_id)
        except Exception as e:
            log.warning("PM get_neg_risk failed: %s", e)
        try:
            tick_size = str(client.get_tick_size(token_id))
        except Exception as e:
            log.warning("PM get_tick_size failed: %s", e)
        log.info("PM order: token=%s...  side=%s size=%s price=%s neg_risk=%s tick=%s",
                 token_id[:20], pm_side, size, price, neg_risk, tick_size)
        opts = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

        if order_type == "market":
            if price is None:
                return {"success": False, "error": "PM market order requires price cap from live data"}
            # Market order as FOK limit at cap price — size is always share count
            signed = client.create_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
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


def set_pm_allowances() -> dict:
    """
    Set USDC.e and conditional token allowances for the Polymarket exchange.
    Must be called once before first trade. Requires POL for gas unless
    builder keys are configured (gasless).
    """
    client = _get_pm_client()
    if not client:
        return {"success": False, "error": "Polymarket keys not configured"}

    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

        # Approve USDC.e (collateral) — no token_id needed
        resp_collateral = client.update_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        log.info("PM collateral allowance set: %s", resp_collateral)

        return {"success": True, "data": {"collateral": resp_collateral}}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_polymarket_positions() -> dict:
    """Fetch Polymarket open positions from the Data API."""
    if not POLYMARKET_WALLET_ADDRESS:
        return {"success": False, "error": "POLYMARKET_WALLET_ADDRESS not configured"}

    try:
        resp = requests.get(
            "https://data-api.polymarket.com/positions",
            params={"user": POLYMARKET_WALLET_ADDRESS, "sizeThreshold": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            positions = resp.json()
            # Filter to non-zero positions
            active = [p for p in positions if float(p.get("size", 0)) > 0]
            return {"success": True, "data": active}
        else:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
