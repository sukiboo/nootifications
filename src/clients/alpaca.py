import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from src.clients.base import BasePriceClient
from src.schemas import AlpacaConfig, PriceUpdate

logger = logging.getLogger(__name__)

ALPACA_WS_URL = "wss://stream.data.alpaca.markets/v2/{feed}"


class AlpacaClient(BasePriceClient):
    """
    Alpaca WebSocket client for real-time US stock trades.

    Requires ALPACA_API_KEY and ALPACA_API_SECRET in env.
    Feed: 'iex' (free) or 'sip' (subscription).
    """

    def __init__(
        self,
        config: AlpacaConfig | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self._config = config or AlpacaConfig()
        self._api_key = api_key or ""
        self._api_secret = api_secret or ""
        self._ws: ClientConnection | None = None
        self._connected = False
        self._subscribed_tickers: list[str] = []
        self._price_queue: asyncio.Queue[PriceUpdate] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None

    @property
    def name(self) -> str:
        return "Alpaca"

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        """Establish WebSocket connection to Alpaca and authenticate."""
        if self._connected:
            return
        if not self._api_key or not self._api_secret:
            raise RuntimeError(
                "Alpaca API key and secret required. "
                "Set ALPACA_API_KEY and ALPACA_API_SECRET in `.env`"
            )

        feed = self._config.feed.strip().lower()
        if feed not in ("iex", "sip"):
            feed = "iex"
        url = ALPACA_WS_URL.format(feed=feed)
        logger.info("Connecting to Alpaca WebSocket (feed=%s)...", feed)
        self._ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)

        # Authenticate within 10 seconds
        await self._ws.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": self._api_key,
                    "secret": self._api_secret,
                }
            )
        )
        auth_response = await self._ws.recv()
        auth_raw = json.loads(auth_response)
        auth_data = auth_raw[0] if isinstance(auth_raw, list) and auth_raw else auth_raw
        if not isinstance(auth_data, dict):
            await self._ws.close()
            self._ws = None
            raise RuntimeError(f"Alpaca auth failed: unexpected response {auth_data}")
        if auth_data.get("T") == "error":
            await self._ws.close()
            self._ws = None
            raise RuntimeError(f"Alpaca auth failed: {auth_data.get('msg', auth_data)}")
        if auth_data.get("T") != "success":
            await self._ws.close()
            self._ws = None
            raise RuntimeError(f"Alpaca auth failed: unexpected response {auth_data}")
        # Accept both "authenticated" and "connected" as valid success messages
        msg = auth_data.get("msg", "")
        if msg not in ("authenticated", "connected"):
            await self._ws.close()
            self._ws = None
            raise RuntimeError(
                f"Alpaca auth failed: unexpected message '{msg}' in response {auth_data}"
            )

        self._connected = True
        self._recv_task = asyncio.create_task(self._receive_loop())
        logger.info("Connected to Alpaca WebSocket")

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning("Error closing Alpaca connection: %s", e)
            finally:
                self._ws = None
                self._connected = False
                logger.info("Disconnected from Alpaca WebSocket")

    async def subscribe(self, tickers: list[str]) -> None:
        """
        Subscribe to trade updates for the given symbols.

        Args:
            tickers: US stock symbols (e.g. ["AAPL", "MSFT", "TSLA"])
        """
        if not self._ws:
            raise RuntimeError("Client not connected. Call connect() first.")

        logger.info("Subscribing to tickers: %s", tickers)
        await self._ws.send(json.dumps({"action": "subscribe", "trades": tickers}))
        self._subscribed_tickers = tickers
        logger.info("Subscribed to %d tickers", len(tickers))

    async def _receive_loop(self) -> None:
        """Process incoming WebSocket messages and queue price updates."""
        last_update: dict[str, float] = {}
        assert self._ws is not None

        try:
            async for raw in self._ws:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.warning("Invalid JSON from Alpaca: %s", e)
                    continue

                # Alpaca can return a single message (dict) or an array of messages (list)
                messages = parsed if isinstance(parsed, list) else [parsed]

                for msg in messages:
                    if not isinstance(msg, dict):
                        continue

                    # T: message type. "t" = trade, "success"/"error" = control
                    msg_type = msg.get("T")
                    if msg_type in ("success", "error", "subscription"):
                        continue
                    if msg_type != "t":
                        continue

                    # Alpaca trade: S=symbol, p=price
                    symbol = msg.get("S")
                    price_raw = msg.get("p")
                    if symbol is None or price_raw is None:
                        continue
                    try:
                        price = float(price_raw)
                    except (TypeError, ValueError):
                        continue

                    now = time.monotonic()
                    if now - last_update.get(symbol, 0) < self._config.throttle_seconds:
                        continue
                    last_update[symbol] = now
                    update = PriceUpdate(ticker=symbol, price=price, source="alpaca")
                    await self._price_queue.put(update)
                    logger.debug("Price update: %s = %.2f", symbol, price)

        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Alpaca WebSocket closed: %s", e)
            self._connected = False
        except Exception as e:
            logger.exception("Alpaca receive loop error: %s", e)
            self._connected = False

    async def price_updates(self) -> AsyncIterator[PriceUpdate]:
        """
        Async iterator yielding price updates.

        Handles reconnection on connection loss.
        """
        reconnect_attempts = 0

        while True:
            try:
                try:
                    update = await asyncio.wait_for(self._price_queue.get(), timeout=30.0)
                    reconnect_attempts = 0
                    yield update
                except asyncio.TimeoutError:
                    if not self._connected:
                        raise RuntimeError("Alpaca WebSocket disconnected")
                    continue

            except asyncio.CancelledError:
                logger.info("Alpaca price update stream cancelled")
                raise

            except Exception as e:
                logger.error("Error in Alpaca price update stream: %s", e)
                self._connected = False

                if reconnect_attempts >= self._config.max_reconnect_attempts:
                    logger.error("Max reconnection attempts reached, giving up")
                    raise

                reconnect_attempts += 1
                logger.info(
                    "Attempting reconnection %d/%d in %ds...",
                    reconnect_attempts,
                    self._config.max_reconnect_attempts,
                    self._config.reconnect_delay,
                )
                await asyncio.sleep(self._config.reconnect_delay)

                try:
                    await self.disconnect()
                    await self.connect()
                    if self._subscribed_tickers:
                        await self.subscribe(self._subscribed_tickers)
                except Exception as reconnect_error:
                    logger.error("Reconnection failed: %s", reconnect_error)
