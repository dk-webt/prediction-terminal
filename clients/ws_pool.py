"""
Redundant WebSocket connection pool.

Manages N parallel WebSocket connections to the same endpoint.
Any single live connection is sufficient for data delivery.
Messages are deduplicated before reaching the consumer callback.

Usage:
    pool = RedundantWSPool(WSPoolConfig(
        name="pm_market",
        url="wss://...",
        pool_size=2,
        subscribe_msgs=lambda: ['{"type": "market", ...}'],
        on_message=my_handler,
    ))
    await pool.start()
    await pool.swap_subscriptions(new_sub_fn, unsub_msgs=[...])
    await pool.stop()
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass
class WSPoolConfig:
    """Configuration for a redundant WebSocket pool."""
    name: str                    # human-readable label for logs
    url: str                     # wss://...
    pool_size: int = 2           # number of redundant connections

    # Auth — called per connect attempt, returns headers dict or None
    auth_headers: Callable[[], dict | None] | None = None

    # Subscribe — called after connect, returns list of raw strings to send
    subscribe_msgs: Callable[[], list[str]] | None = None

    # Ping — None = server-initiated (library auto-responds); string = client sends text
    ping_text: str | None = "PING"
    ping_interval: float = 8.0

    # Timeouts & reconnect
    recv_timeout: float = 20.0
    reconnect_base: float = 0.5
    reconnect_max: float = 5.0
    stagger_delay: float = 0.5   # seconds between starting each connection

    # Consumer callback — receives deduplicated raw messages
    on_message: Callable[[str], Awaitable[None]] | None = None

    # Dedup — extract key from raw message; None = hash full message
    # Return None from callable to skip dedup (always deliver)
    dedup_key: Callable[[str], str | None] | None = None

    # Max dedup ring size
    dedup_size: int = 512


@dataclass
class _ConnState:
    """Internal state for a single connection in the pool."""
    conn_id: int
    ws: object | None = None
    connected: bool = False
    last_recv: float = 0.0       # monotonic time
    total_messages: int = 0
    backoff: float = 0.5


class RedundantWSPool:
    """
    Manages N redundant WebSocket connections to the same endpoint.

    All connections subscribe to the same data. Messages are deduplicated
    via a ring buffer so the consumer callback fires at most once per
    unique message. If one connection drops, the others cover seamlessly.
    """

    def __init__(self, config: WSPoolConfig):
        self._config = config
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._connections = [
            _ConnState(conn_id=i, backoff=config.reconnect_base)
            for i in range(config.pool_size)
        ]

        # Dedup ring buffer + O(1) lookup set
        self._dedup_ring: collections.deque[str] = collections.deque(
            maxlen=config.dedup_size
        )
        self._dedup_set: set[str] = set()

    async def start(self):
        """Start all redundant connections (staggered)."""
        self._running = True
        for conn in self._connections:
            self._tasks.append(asyncio.create_task(
                self._conn_loop(conn),
                name=f"{self._config.name}-conn-{conn.conn_id}",
            ))

    async def stop(self):
        """Gracefully close all connections and cancel tasks."""
        self._running = False
        for conn in self._connections:
            if conn.ws:
                try:
                    await conn.ws.close()
                except Exception:
                    pass
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def swap_subscriptions(
        self,
        new_sub_fn: Callable[[], list[str]],
        unsub_msgs: list[str] | None = None,
    ):
        """
        Hot-swap subscriptions on all live connections.
        Updates stored subscribe_msgs so future reconnects use the new value.
        """
        self._config.subscribe_msgs = new_sub_fn
        for conn in self._connections:
            if conn.ws and conn.connected:
                try:
                    if unsub_msgs:
                        for msg in unsub_msgs:
                            await conn.ws.send(msg)
                    for msg in new_sub_fn():
                        await conn.ws.send(msg)
                    log.info("%s[%d] subscription swapped",
                             self._config.name, conn.conn_id)
                except Exception as e:
                    log.warning("%s[%d] swap failed, closing for reconnect: %s",
                                self._config.name, conn.conn_id, e)
                    try:
                        await conn.ws.close()
                    except Exception:
                        pass

    async def send_all(self, message: str):
        """Send a raw message to every live connection."""
        for conn in self._connections:
            if conn.ws and conn.connected:
                try:
                    await conn.ws.send(message)
                except Exception:
                    pass

    @property
    def is_live(self) -> bool:
        """True if at least one connection has recent data."""
        now = time.monotonic()
        return any(
            c.connected and (now - c.last_recv) < self._config.recv_timeout
            for c in self._connections
        )

    @property
    def health(self) -> dict:
        """Pool health summary."""
        now = time.monotonic()
        conns = []
        live = 0
        for c in self._connections:
            is_live = c.connected and (now - c.last_recv) < self._config.recv_timeout
            if is_live:
                live += 1
            conns.append({
                "id": c.conn_id,
                "connected": c.connected,
                "last_recv_age": round(now - c.last_recv, 1) if c.last_recv > 0 else None,
                "messages": c.total_messages,
            })
        return {"name": self._config.name, "live": live, "total": len(self._connections), "connections": conns}

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _conn_loop(self, conn: _ConnState):
        """Lifecycle for a single redundant connection. Reconnects forever."""
        import websockets

        cfg = self._config

        # Stagger startup to avoid thundering herd
        if conn.conn_id > 0:
            await asyncio.sleep(conn.conn_id * cfg.stagger_delay)

        while self._running:
            try:
                # Build auth headers if configured
                headers = cfg.auth_headers() if cfg.auth_headers else None

                log.info("%s[%d] connecting to %s",
                         cfg.name, conn.conn_id, cfg.url[:60])

                async with websockets.connect(
                    cfg.url,
                    additional_headers=headers or {},
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    conn.ws = ws
                    conn.connected = True

                    # Send subscribe messages
                    if cfg.subscribe_msgs:
                        for msg in cfg.subscribe_msgs():
                            await ws.send(msg)

                    log.info("%s[%d] connected and subscribed",
                             cfg.name, conn.conn_id)
                    conn.backoff = cfg.reconnect_base  # reset on success

                    # Run recv + optional ping concurrently
                    tasks = [asyncio.create_task(self._recv_loop(conn, ws))]
                    if cfg.ping_text is not None:
                        tasks.append(asyncio.create_task(self._ping_loop(conn, ws)))

                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    # Propagate exceptions for logging
                    for t in done:
                        try:
                            t.result()
                        except Exception:
                            pass

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("%s[%d] error, reconnecting in %.1fs: %s",
                            cfg.name, conn.conn_id, conn.backoff, e)
                await asyncio.sleep(conn.backoff)
                conn.backoff = min(conn.backoff * 2, cfg.reconnect_max)
            finally:
                conn.ws = None
                conn.connected = False

    async def _recv_loop(self, conn: _ConnState, ws):
        """Receive messages, deduplicate, forward to consumer."""
        import websockets

        cfg = self._config
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=cfg.recv_timeout)
            except asyncio.TimeoutError:
                log.warning("%s[%d] recv timeout (%.0fs), reconnecting",
                            cfg.name, conn.conn_id, cfg.recv_timeout)
                return
            except websockets.exceptions.ConnectionClosed:
                log.info("%s[%d] connection closed", cfg.name, conn.conn_id)
                return

            conn.last_recv = time.monotonic()
            conn.total_messages += 1

            # Dedup check
            if self._should_deliver(raw):
                if cfg.on_message:
                    try:
                        await cfg.on_message(raw)
                    except Exception as e:
                        log.warning("%s[%d] on_message error: %s",
                                    cfg.name, conn.conn_id, e)

    async def _ping_loop(self, conn: _ConnState, ws):
        """Send client-initiated pings at configured interval."""
        cfg = self._config
        while self._running:
            try:
                await ws.send(cfg.ping_text)
            except Exception:
                return
            await asyncio.sleep(cfg.ping_interval)

    def _should_deliver(self, raw: str) -> bool:
        """Returns True if this message should be delivered (not a duplicate)."""
        cfg = self._config

        # Compute dedup key
        if cfg.dedup_key:
            key = cfg.dedup_key(raw)
            if key is None:
                return True  # skip dedup, always deliver
        else:
            key = hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

        if key in self._dedup_set:
            return False

        # Add to ring; evict oldest if full
        if len(self._dedup_ring) == self._dedup_ring.maxlen:
            evicted = self._dedup_ring[0]
            self._dedup_set.discard(evicted)
        self._dedup_ring.append(key)
        self._dedup_set.add(key)
        return True
