import asyncio
import datetime
import json
import logging
import time
import urllib.error
import urllib.request

import websockets
from websockets.asyncio.client import ClientConnection

from src.clients.base import BasePriceClient
from src.schemas import AlpacaConfig, PriceUpdate

logger = logging.getLogger(__name__)

ALPACA_WS_URL = "wss://stream.data.alpaca.markets/v2/{feed}"
ALPACA_API_URLS = (
    "https://api.alpaca.markets",
    "https://paper-api.alpaca.markets",
)


class AlpacaClient(BasePriceClient[AlpacaConfig]):
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
        # The key/secret is scoped to exactly one of live/paper; remember
        # whichever endpoint actually authenticates so we can reuse it.
        self._api_base_url: str | None = None
        # Cached (is_open, unix_expiry_ts) from /v2/clock. Valid until the
        # next scheduled market-state transition.
        self._clock_cache: tuple[bool, float] | None = None

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

    def validate_tickers(self, tickers: list[str]) -> None:
        """Validate tickers against Alpaca's available assets."""
        if not tickers:
            return
        if not self._api_key or not self._api_secret:
            raise RuntimeError("Alpaca API credentials required for ticker validation")

        invalid = []
        for ticker in tickers:
            validated = False
            last_error = None

            for base_url in self._auth_base_urls():
                url = f"{base_url}/v2/assets/{ticker}"
                req = urllib.request.Request(
                    url,
                    headers={
                        "APCA-API-KEY-ID": self._api_key,
                        "APCA-API-SECRET-KEY": self._api_secret,
                    },
                )
                try:
                    with urllib.request.urlopen(
                        req, timeout=10
                    ) as resp:  # nosec B310 - URL is hardcoded https
                        data = json.loads(resp.read().decode())
                        if not data.get("tradable", False):
                            invalid.append(f"{ticker} (not tradable)")
                        self._api_base_url = base_url
                        validated = True
                        break
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        self._api_base_url = base_url
                        invalid.append(ticker)
                        validated = True
                        break
                    elif e.code == 401:
                        # Wrong endpoint for this key type, try next
                        last_error = e
                        continue
                    else:
                        raise ValueError(f"Alpaca API error for {ticker}: {e.code} {e.reason}")

            if not validated and last_error:
                raise ValueError(f"Alpaca API authentication failed. Check your API keys in .env")

        if invalid:
            raise ValueError(f"Invalid Alpaca ticker(s): {invalid}")

    def _auth_base_urls(self) -> tuple[str, ...]:
        if self._api_base_url:
            return (self._api_base_url,)
        return ALPACA_API_URLS

    async def should_check_staleness(self) -> bool:
        return await self._is_market_open()

    async def _is_market_open(self) -> bool:
        now = time.time()
        if self._clock_cache is not None and now < self._clock_cache[1]:
            return self._clock_cache[0]
        try:
            is_open, next_ts = await asyncio.to_thread(self._fetch_clock)
        except Exception as e:
            logger.warning("Failed to fetch Alpaca clock: %s", e)
            if self._clock_cache is not None:
                return self._clock_cache[0]
            # Unknown state: default to 'closed' so the watchdog stays
            # disarmed rather than falsely killing a healthy connection.
            return False
        self._clock_cache = (is_open, next_ts)
        logger.info(
            "Alpaca market %s; next transition at %s",
            "open" if is_open else "closed",
            datetime.datetime.fromtimestamp(next_ts, tz=datetime.timezone.utc).isoformat(),
        )
        return is_open

    # Returns (is_open, unix_ts_of_next_transition) from Alpaca's /v2/clock.
    def _fetch_clock(self) -> tuple[bool, float]:
        if not self._api_key or not self._api_secret:
            raise RuntimeError("Alpaca API credentials required for clock query")

        last_error: Exception | None = None
        for base_url in self._auth_base_urls():
            url = f"{base_url}/v2/clock"
            req = urllib.request.Request(
                url,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                },
            )
            try:
                with urllib.request.urlopen(
                    req, timeout=10
                ) as resp:  # nosec B310 - URL is hardcoded https
                    data = json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    last_error = e
                    continue
                raise
            self._api_base_url = base_url
            is_open = bool(data.get("is_open"))
            transition = data.get("next_close") if is_open else data.get("next_open")
            if transition:
                next_ts = datetime.datetime.fromisoformat(transition).timestamp()
            else:
                # Shouldn't happen in practice; fall back to a short refresh.
                next_ts = time.time() + 300.0
            return is_open, next_ts
        raise RuntimeError(f"Alpaca clock API authentication failed: {last_error}")

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
        except Exception as e:
            logger.exception("Alpaca receive loop error: %s", e)
        finally:
            # Covers the silent-close path where the iterator exits without raising.
            self._connected = False
