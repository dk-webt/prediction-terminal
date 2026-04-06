"""
Deribit IV poller — fetches BTC implied volatility and de-annualizes to 15-minute sigma.

Sources (in priority order):
  1. 0DTE ATM option IV (shortest-dated, most relevant for 15-min)
  2. DVOL index (Deribit aggregate, fallback)

Usage:
    poller = DeribitPoller(interval_s=300)
    await poller.start()   # begins background polling
    sigma = poller.sigma_15m  # latest value (or None)
    await poller.stop()
"""

import asyncio
import logging
import math
import time

import aiohttp

log = logging.getLogger(__name__)

DERIBIT_API = "https://www.deribit.com/api/v2/public"
INTERVALS_PER_YEAR = 35_040  # (365.25 * 24 * 60) / 15
SQRT_INTERVALS = math.sqrt(INTERVALS_PER_YEAR)


def _iv_annual_to_sigma_15m(iv_annual_pct: float) -> float:
    """Convert annualized IV (in %) to 15-minute sigma (decimal)."""
    return (iv_annual_pct / 100.0) / SQRT_INTERVALS


async def _fetch_dvol(session: aiohttp.ClientSession) -> dict | None:
    """Fetch DVOL (Deribit Volatility Index) for BTC."""
    url = f"{DERIBIT_API}/get_volatility_index_data"
    params = {
        "currency": "BTC",
        "resolution": 1,
        "start_timestamp": int((time.time() - 60) * 1000),
        "end_timestamp": int(time.time() * 1000),
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            result = data.get("result", {}).get("data", [])
            if not result:
                return None
            latest = result[-1]
            return {
                "source": "DVOL",
                "iv_annual_pct": latest[4],
            }
    except Exception as e:
        log.warning("DVOL fetch error: %s", e)
        return None


async def _fetch_0dte_iv(session: aiohttp.ClientSession) -> dict | None:
    """Fetch IV from the shortest-dated ATM BTC option on Deribit."""
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        # Get BTC index price
        async with session.get(
            f"{DERIBIT_API}/get_index_price",
            params={"index_name": "btc_usd"},
            timeout=timeout,
        ) as resp:
            data = await resp.json()
            btc_price = data["result"]["index_price"]

        # Get instruments
        async with session.get(
            f"{DERIBIT_API}/get_instruments",
            params={"currency": "BTC", "kind": "option", "expired": "false"},
            timeout=timeout,
        ) as resp:
            data = await resp.json()
            instruments = data.get("result", [])

        if not instruments:
            return None

        now_ms = int(time.time() * 1000)
        instruments = [i for i in instruments if i["expiration_timestamp"] > now_ms]
        if not instruments:
            return None

        min_expiry = min(i["expiration_timestamp"] for i in instruments)
        nearest = [
            i for i in instruments
            if i["expiration_timestamp"] == min_expiry and i["option_type"] == "call"
        ]
        if not nearest:
            return None

        atm = min(nearest, key=lambda i: abs(i["strike"] - btc_price))

        # Get ticker for ATM option
        async with session.get(
            f"{DERIBIT_API}/ticker",
            params={"instrument_name": atm["instrument_name"]},
            timeout=timeout,
        ) as resp:
            data = await resp.json()
            ticker = data.get("result", {})

        iv = ticker.get("mark_iv")
        if iv is None:
            return None

        hours_to_expiry = (min_expiry - now_ms) / (1000 * 3600)

        return {
            "source": f"0DTE ATM ({atm['instrument_name']})",
            "iv_annual_pct": iv,
            "hours_to_expiry": round(hours_to_expiry, 2),
        }
    except Exception as e:
        log.warning("0DTE IV fetch error: %s", e)
        return None


class DeribitPoller:
    """
    Periodically polls Deribit for BTC implied volatility.

    Prefers 0DTE ATM option IV; falls back to DVOL index.
    Exposes the de-annualized 15-minute sigma for Model A.
    """

    def __init__(self, interval_s: float = 300):
        """
        Args:
            interval_s: polling interval in seconds (default 300 = 5 min)
        """
        self.interval_s = interval_s

        # Latest values
        self.sigma_15m: float | None = None      # 15-min sigma (decimal)
        self.iv_annual_pct: float | None = None   # annualized IV (%)
        self.source: str = ""                     # which source provided the IV
        self.last_fetch: float = 0.0              # epoch time of last successful fetch
        self.hours_to_expiry: float | None = None # for 0DTE source only

        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the background polling loop."""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        """Stop polling."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def fetch_once(self):
        """Fetch IV once and update state. Can be called manually."""
        # Disable auto-decompress to avoid brotli issues on some platforms
        async with aiohttp.ClientSession(
            headers={"Accept-Encoding": "gzip, deflate"},
            auto_decompress=True,
        ) as session:
            # Try 0DTE first (more relevant for short-dated), fall back to DVOL
            dte = await _fetch_0dte_iv(session)
            dvol = await _fetch_dvol(session)

        # Prefer 0DTE if available
        chosen = dte or dvol
        if chosen:
            self.iv_annual_pct = chosen["iv_annual_pct"]
            self.sigma_15m = _iv_annual_to_sigma_15m(chosen["iv_annual_pct"])
            self.source = chosen["source"]
            self.last_fetch = time.time()
            self.hours_to_expiry = chosen.get("hours_to_expiry")
            log.info(
                "Deribit IV: %.2f%% annual → σ_15m=%.6f (%s)",
                self.iv_annual_pct, self.sigma_15m, self.source,
            )
        else:
            log.warning("Deribit IV fetch failed — both sources unavailable")

    async def _poll_loop(self):
        """Background polling loop."""
        while self._running:
            try:
                await self.fetch_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("Deribit poll error: %s", e)
            await asyncio.sleep(self.interval_s)

    STALE_THRESHOLD = 600  # 10 min — sigma should refresh every 5 min

    @property
    def is_stale(self) -> bool:
        """True if sigma hasn't been updated within threshold."""
        if self.last_fetch <= 0:
            return True
        return (time.time() - self.last_fetch) > self.STALE_THRESHOLD

    def get_status(self) -> dict:
        """Return current state as a dict."""
        age = time.time() - self.last_fetch if self.last_fetch > 0 else None
        return {
            "sigma_15m": self.sigma_15m,
            "iv_annual_pct": self.iv_annual_pct,
            "source": self.source,
            "age_s": round(age, 1) if age is not None else None,
            "is_stale": self.is_stale,
            "hours_to_expiry": self.hours_to_expiry,
        }
