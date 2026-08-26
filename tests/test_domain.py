from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sipd.domain import LedgerEntry, calculate_position, convert


def test_weighted_average_sale_matches_current_ledger():
    at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    position = calculate_position([
        LedgerEntry(1, "buy", Decimal("2"), Decimal("100"), Decimal("1"), at),
        LedgerEntry(2, "buy", Decimal("2"), Decimal("200"), Decimal("1"), at + timedelta(seconds=1)),
        LedgerEntry(3, "sell", Decimal("1"), Decimal("180"), Decimal("1"), at + timedelta(seconds=2)),
    ])

    assert position.quantity == Decimal("3")
    assert position.average_cost == Decimal("150")
    assert position.cost_basis == Decimal("450")
    assert position.realized == Decimal("30")
    assert position.net_invested == Decimal("420")


def test_position_rejects_oversell():
    with pytest.raises(ValueError, match="quantity exceeds available holding"):
        calculate_position([
            LedgerEntry(1, "sell", Decimal("1"), Decimal("100"), Decimal("1"), datetime.now(timezone.utc)),
        ])


def test_convert_between_idr_and_usd():
    assert convert(Decimal("2"), Decimal("16000"), "USD", "IDR") == Decimal("32000")
    assert convert(Decimal("32000"), Decimal("16000"), "IDR", "USD") == Decimal("2")
