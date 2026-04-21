"""Tests for stale-watchdog gating and Alpaca market-clock integration."""

import asyncio
import json
import time
import urllib.error
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from src.clients.alpaca import ALPACA_API_URLS, AlpacaClient
from src.clients.base import BasePriceClient
from src.schemas import AlpacaConfig


def _clock_response(is_open: bool, transition_iso: str) -> MagicMock:
    body = {"is_open": is_open, "next_open": transition_iso, "next_close": transition_iso}
    mock = MagicMock()
    mock.read.return_value = json.dumps(body).encode()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestAlpacaFetchClock:
    @pytest.fixture
    def client(self) -> AlpacaClient:
        return AlpacaClient(AlpacaConfig(), api_key="k", api_secret="s")

    def test_first_call_hits_live_and_caches_base_url(self, client: AlpacaClient) -> None:
        response = _clock_response(True, "2099-01-01T00:00:00+00:00")
        with patch("urllib.request.urlopen", return_value=response) as mock_urlopen:
            is_open, _ = client._fetch_clock()

        assert is_open is True
        assert client._api_base_url == "https://api.alpaca.markets"
        assert mock_urlopen.call_count == 1
        assert "paper" not in mock_urlopen.call_args[0][0].full_url

    def test_falls_back_to_paper_on_401(self, client: AlpacaClient) -> None:
        response = _clock_response(False, "2099-01-01T00:00:00+00:00")

        def _side_effect(req: Any, timeout: int = 10) -> Any:
            if "paper-api" in req.full_url:
                return response
            raise urllib.error.HTTPError(
                url="", code=401, msg="Unauthorized", hdrs={}, fp=None  # type: ignore[arg-type]
            )

        with patch("urllib.request.urlopen", side_effect=_side_effect) as mock_urlopen:
            is_open, _ = client._fetch_clock()

        assert is_open is False
        assert client._api_base_url == "https://paper-api.alpaca.markets"
        assert mock_urlopen.call_count == 2

    def test_raises_when_both_endpoints_return_401(self, client: AlpacaClient) -> None:
        err = urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized", hdrs={}, fp=None  # type: ignore[arg-type]
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="authentication failed"):
                client._fetch_clock()

    def test_cached_base_url_skips_other_endpoint(self, client: AlpacaClient) -> None:
        client._api_base_url = "https://paper-api.alpaca.markets"
        response = _clock_response(True, "2099-01-01T00:00:00+00:00")

        with patch("urllib.request.urlopen", return_value=response) as mock_urlopen:
            client._fetch_clock()

        assert mock_urlopen.call_count == 1
        assert "paper-api" in mock_urlopen.call_args[0][0].full_url

    def test_api_urls_contain_both_endpoints(self) -> None:
        # Sanity: if we ever reorder these, the fallback assumptions change.
        assert ALPACA_API_URLS[0] == "https://api.alpaca.markets"
        assert ALPACA_API_URLS[1] == "https://paper-api.alpaca.markets"


class TestAlpacaClockCache:
    @pytest.fixture
    def client(self) -> AlpacaClient:
        return AlpacaClient(AlpacaConfig(), api_key="k", api_secret="s")

    async def test_cache_hit_skips_fetch(self, client: AlpacaClient) -> None:
        client._clock_cache = (True, time.time() + 3600)

        with patch.object(client, "_fetch_clock") as mock_fetch:
            assert await client._is_market_open() is True

        mock_fetch.assert_not_called()

    async def test_cache_miss_triggers_fetch_and_stores_result(self, client: AlpacaClient) -> None:
        expiry = time.time() + 3600

        with patch.object(client, "_fetch_clock", return_value=(True, expiry)) as mock_fetch:
            assert await client._is_market_open() is True

        mock_fetch.assert_called_once()
        assert client._clock_cache == (True, expiry)

    async def test_expired_cache_refreshes(self, client: AlpacaClient) -> None:
        client._clock_cache = (False, time.time() - 100)  # expired, stale value
        fresh_expiry = time.time() + 3600

        with patch.object(client, "_fetch_clock", return_value=(True, fresh_expiry)) as mock_fetch:
            assert await client._is_market_open() is True

        mock_fetch.assert_called_once()
        assert client._clock_cache == (True, fresh_expiry)

    async def test_fetch_failure_falls_back_to_stale_cache(self, client: AlpacaClient) -> None:
        client._clock_cache = (True, time.time() - 100)  # expired but present

        with patch.object(client, "_fetch_clock", side_effect=RuntimeError("boom")):
            assert await client._is_market_open() is True

    async def test_fetch_failure_without_cache_returns_false(self, client: AlpacaClient) -> None:
        with patch.object(client, "_fetch_clock", side_effect=RuntimeError("boom")):
            assert await client._is_market_open() is False


class _StubConfig:
    max_reconnect_attempts = 0
    reconnect_delay = 0

    def __init__(self, stale_timeout_seconds: int) -> None:
        self.stale_timeout_seconds = stale_timeout_seconds


class _StubClient(BasePriceClient):
    def __init__(self, stale_timeout: int, staleness_hook: Callable[[], bool]) -> None:
        self._config = _StubConfig(stale_timeout)  # type: ignore[assignment]
        self._price_queue = asyncio.Queue()
        self._subscribed_tickers = []
        self._connected = True
        self._staleness_hook = staleness_hook

    @property
    def name(self) -> str:
        return "Stub"

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def subscribe(self, tickers: list[str]) -> None:
        pass

    def validate_tickers(self, tickers: list[str]) -> None:
        pass

    async def should_check_staleness(self) -> bool:
        return self._staleness_hook()


async def _fake_wait_for(coro: Any, *args: Any, **kwargs: Any) -> Any:
    # Tiny real sleep so time.monotonic advances and idle > 0; then pretend
    # the queue.get timed out.
    coro.close()
    await asyncio.sleep(0.01)
    raise asyncio.TimeoutError


class TestStaleWatchdogGating:
    async def test_disarmed_hook_prevents_stale_raise(self) -> None:
        client = _StubClient(stale_timeout=0, staleness_hook=lambda: False)

        async def consume() -> None:
            async for _ in client.price_updates():
                pass

        with patch("src.clients.base.asyncio.wait_for", side_effect=_fake_wait_for):
            task = asyncio.create_task(consume())
            await asyncio.sleep(0.1)  # allow several loop iterations
            if task.done():
                pytest.fail(f"price_updates raised unexpectedly: {task.exception()!r}")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async def test_armed_hook_raises_when_idle_exceeds_timeout(self) -> None:
        client = _StubClient(stale_timeout=0, staleness_hook=lambda: True)

        async def consume() -> None:
            async for _ in client.price_updates():
                pass

        with patch("src.clients.base.asyncio.wait_for", side_effect=_fake_wait_for):
            with pytest.raises(RuntimeError, match="stale"):
                await consume()

    async def test_disconnected_raises_even_when_hook_disarmed(self) -> None:
        # Regression guard: the disconnected check must fire before the hook
        # is consulted, so a dropped WS is never masked by "market closed".
        client = _StubClient(stale_timeout=999, staleness_hook=lambda: False)
        client._connected = False

        async def consume() -> None:
            async for _ in client.price_updates():
                pass

        with patch("src.clients.base.asyncio.wait_for", side_effect=_fake_wait_for):
            with pytest.raises(RuntimeError, match="disconnected"):
                await consume()
