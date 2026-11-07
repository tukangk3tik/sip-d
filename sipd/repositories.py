from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3

from sipd.providers import Quote
from sipd.db import connect


@dataclass(frozen=True)
class Asset:
    id: int
    type_id: int
    name: str
    symbol: str
    unit: str
    quote_currency: str
    pricing_mode: str
    provider: str
    provider_symbol: str


def get_asset(path: str, user_id: int, asset_id: int) -> Asset | None:
    db = connect(path)
    try:
        row = db.execute(
            """SELECT id,investment_type_id,name,coalesce(symbol,''),unit,quote_currency,
                      pricing_mode,coalesce(provider,''),coalesce(provider_symbol,'')
               FROM assets WHERE id=? AND user_id=?""",
            (asset_id, user_id),
        ).fetchone()
    finally:
        db.close()
    return Asset(row["id"], row["investment_type_id"], row["name"], row["symbol"], row["unit"], row["quote_currency"], row["pricing_mode"], row["provider"], row["provider_symbol"]) if row else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_db_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_cached_quote(db: sqlite3.Connection, provider: str, symbol: str):
    return db.execute("SELECT * FROM quote_cache WHERE provider=? AND symbol=?", (provider, symbol)).fetchone()


def cached_quote_to_quote(row) -> Quote | None:
    if not row or not row["price"] or not row["currency"] or not row["fetched_at"]:
        return None
    return Quote(Decimal(row["price"]), row["currency"], row["source"], parse_db_time(row["fetched_at"]) or utc_now())


def save_cached_quote(db: sqlite3.Connection, provider: str, symbol: str, quote: Quote) -> None:
    db.execute(
        """INSERT INTO quote_cache(provider,symbol,price,currency,source,fetched_at,error,error_at,failure_count,backoff_until,updated_at)
           VALUES(?,?,?,?,?,?, '', NULL, 0, NULL, CURRENT_TIMESTAMP)
           ON CONFLICT(provider,symbol) DO UPDATE SET
             price=excluded.price,
             currency=excluded.currency,
             source=excluded.source,
             fetched_at=excluded.fetched_at,
             error='',
             error_at=NULL,
             failure_count=0,
             backoff_until=NULL,
             updated_at=CURRENT_TIMESTAMP""",
        (provider, symbol, str(quote.price), quote.currency, quote.source, as_db_time(quote.at)),
    )


def record_quote_failure(db: sqlite3.Connection, provider: str, symbol: str, error: str, *, now: datetime, initial_seconds: int, max_seconds: int):
    current = get_cached_quote(db, provider, symbol)
    failure_count = int(current["failure_count"]) + 1 if current else 1
    delay = min(max_seconds, initial_seconds * (2 ** (failure_count - 1)))
    backoff_until = datetime.fromtimestamp(now.timestamp() + delay, timezone.utc)
    db.execute(
        """INSERT INTO quote_cache(provider,symbol,error,error_at,failure_count,backoff_until,updated_at)
           VALUES(?,?,?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(provider,symbol) DO UPDATE SET
             error=excluded.error,
             error_at=excluded.error_at,
             failure_count=excluded.failure_count,
             backoff_until=excluded.backoff_until,
             updated_at=CURRENT_TIMESTAMP""",
        (provider, symbol, error, as_db_time(now), failure_count, as_db_time(backoff_until)),
    )
    return get_cached_quote(db, provider, symbol)
