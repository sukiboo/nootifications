"""Tests for PriceStateManager persistence and concurrent writes."""

import asyncio
import json
from pathlib import Path

import pytest

from src.utils import PriceStateManager


@pytest.mark.asyncio
async def test_set_price_persists_and_roundtrips(tmp_path: Path) -> None:
    """Prices written by set_price should load back via a fresh manager."""
    state_file = tmp_path / "prices.json"

    writer = PriceStateManager(state_file)
    await writer.set_price("BTC/USD", 100_000.5)
    await writer.set_price("ETH/USD", 3_250.75)

    with open(state_file, encoding="utf-8") as f:
        assert json.load(f) == {"prices": {"BTC/USD": 100_000.5, "ETH/USD": 3_250.75}}

    reader = PriceStateManager(state_file)
    assert reader.get_price("BTC/USD") == 100_000.5
    assert reader.get_price("ETH/USD") == 3_250.75


@pytest.mark.asyncio
async def test_concurrent_set_price_keeps_file_valid(tmp_path: Path) -> None:
    """Concurrent writes must not leave a corrupt/partial JSON file."""
    state_file = tmp_path / "prices.json"
    manager = PriceStateManager(state_file)

    tickers = [f"T{i}" for i in range(20)]
    await asyncio.gather(*(manager.set_price(t, float(i)) for i, t in enumerate(tickers)))

    # File parses cleanly and contains every writer's final value.
    with open(state_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["prices"] == {t: float(i) for i, t in enumerate(tickers)}
