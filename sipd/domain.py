from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    kind: str
    quantity: Decimal
    price: Decimal
    fx_to_idr: Decimal
    at: datetime


@dataclass
class Position:
    quantity: Decimal = Decimal()
    average_cost: Decimal = Decimal()
    cost_basis: Decimal = Decimal()
    realized: Decimal = Decimal()
    net_invested: Decimal = Decimal()


def calculate_position(entries: list[LedgerEntry]) -> Position:
    position = Position()
    for entry in sorted(entries, key=lambda value: (value.at, value.id)):
        if not all(value > 0 for value in (entry.quantity, entry.price, entry.fx_to_idr)):
            raise ValueError("quantity, price, and exchange rate must be positive")
        value = entry.quantity * entry.price * entry.fx_to_idr
        if entry.kind in {"buy", "deposit"}:
            position.cost_basis += value
            position.quantity += entry.quantity
            position.average_cost = position.cost_basis / position.quantity
            position.net_invested += value
        elif entry.kind in {"sell", "withdrawal"}:
            if entry.quantity > position.quantity:
                raise ValueError("quantity exceeds available holding")
            cost = position.average_cost * entry.quantity
            position.realized += value - cost
            position.cost_basis -= cost
            position.quantity -= entry.quantity
            position.net_invested -= value
            if not position.quantity:
                position.average_cost = position.cost_basis = Decimal()
        else:
            raise ValueError("invalid transaction type")
    return position


def convert(amount: Decimal, usd_to_idr: Decimal, source: str, target: str) -> Decimal:
    if source == target:
        return amount
    if usd_to_idr <= 0:
        raise ValueError("USD/IDR rate unavailable")
    if source == "USD" and target == "IDR":
        return amount * usd_to_idr
    if source == "IDR" and target == "USD":
        return amount / usd_to_idr
    raise ValueError("unsupported currency")
