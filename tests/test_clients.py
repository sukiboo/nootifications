"""Tests for price client ticker validation."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.clients.alpaca import AlpacaClient
from src.clients.kraken import KrakenClient
from src.schemas import AlpacaConfig, KrakenConfig


class TestKrakenTickerValidation:
    """Tests for Kraken ticker validation."""

    @pytest.fixture
    def mock_asset_pairs(self) -> dict:
        """Sample Kraken asset pairs response (REST API format with v1 wsname)."""
        return {
            "XXBTZUSD": {
                "altname": "XBTUSD",
                "wsname": "XBT/USD",  # v1 format, v2 uses BTC/USD
                "base": "XXBT",
                "quote": "ZUSD",
            },
            "XETHZUSD": {
                "altname": "ETHUSD",
                "wsname": "ETH/USD",
                "base": "XETH",
                "quote": "ZUSD",
            },
            "DOTUSD": {
                "altname": "DOTUSD",
                "wsname": "DOT/USD",
                "base": "DOT",
                "quote": "ZUSD",
            },
        }

    def test_valid_tickers_pass(self, mock_asset_pairs: dict) -> None:
        """WS v2 format tickers (BTC/USD) should pass."""
        client = KrakenClient(KrakenConfig())

        with patch("src.clients.kraken.Market") as mock_market_cls:
            mock_market = MagicMock()
            mock_market.get_asset_pairs.return_value = mock_asset_pairs
            mock_market_cls.return_value = mock_market

            # BTC/USD is the v2 format, should work
            client.validate_tickers(["BTC/USD", "ETH/USD"])

    def test_deprecated_xbt_format_raises(self, mock_asset_pairs: dict) -> None:
        """Deprecated XBT format should raise with helpful message."""
        client = KrakenClient(KrakenConfig())

        with pytest.raises(ValueError, match="Deprecated ticker format.*XBT.*Use 'BTC'"):
            client.validate_tickers(["XBT/USD"])

    def test_invalid_ticker_raises(self, mock_asset_pairs: dict) -> None:
        """Invalid ticker should raise ValueError."""
        client = KrakenClient(KrakenConfig())

        with patch("src.clients.kraken.Market") as mock_market_cls:
            mock_market = MagicMock()
            mock_market.get_asset_pairs.return_value = mock_asset_pairs
            mock_market_cls.return_value = mock_market

            with pytest.raises(ValueError, match="Invalid Kraken ticker"):
                client.validate_tickers(["BTCUSD"])  # Wrong format, missing slash

    def test_mixed_valid_invalid_raises(self, mock_asset_pairs: dict) -> None:
        """Mix of valid and invalid tickers should raise for invalid ones."""
        client = KrakenClient(KrakenConfig())

        with patch("src.clients.kraken.Market") as mock_market_cls:
            mock_market = MagicMock()
            mock_market.get_asset_pairs.return_value = mock_asset_pairs
            mock_market_cls.return_value = mock_market

            with pytest.raises(ValueError, match="FAKE/USD"):
                client.validate_tickers(["BTC/USD", "FAKE/USD"])

    def test_empty_tickers_passes(self) -> None:
        """Empty ticker list should not raise or call API."""
        client = KrakenClient(KrakenConfig())

        with patch("src.clients.kraken.Market") as mock_market_cls:
            client.validate_tickers([])
            mock_market_cls.assert_not_called()


class TestAlpacaTickerValidation:
    """Tests for Alpaca ticker validation."""

    @pytest.fixture
    def alpaca_client(self) -> AlpacaClient:
        """Alpaca client with test credentials."""
        return AlpacaClient(
            AlpacaConfig(),
            api_key="test-key",
            api_secret="test-secret",
        )

    def test_valid_ticker_passes(self, alpaca_client: AlpacaClient) -> None:
        """Valid tradable ticker should not raise."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"symbol": "AAPL", "tradable": True}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            # Should not raise
            alpaca_client.validate_tickers(["AAPL"])

    def test_invalid_ticker_raises(self, alpaca_client: AlpacaClient) -> None:
        """Non-existent ticker should raise ValueError."""
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="", code=404, msg="Not Found", hdrs={}, fp=None  # type: ignore[arg-type]
            ),
        ):
            with pytest.raises(ValueError, match="Invalid Alpaca ticker"):
                alpaca_client.validate_tickers(["NOTREAL"])

    def test_non_tradable_ticker_raises(self, alpaca_client: AlpacaClient) -> None:
        """Ticker that exists but is not tradable should raise."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"symbol": "DELISTED", "tradable": False}
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            with pytest.raises(ValueError, match="not tradable"):
                alpaca_client.validate_tickers(["DELISTED"])

    def test_missing_credentials_raises(self) -> None:
        """Validation without API credentials should raise."""
        client = AlpacaClient(AlpacaConfig())  # No credentials

        with pytest.raises(RuntimeError, match="API credentials required"):
            client.validate_tickers(["AAPL"])

    def test_empty_tickers_passes(self, alpaca_client: AlpacaClient) -> None:
        """Empty ticker list should not raise or call API."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            alpaca_client.validate_tickers([])
            mock_urlopen.assert_not_called()
