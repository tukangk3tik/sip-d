from dataclasses import dataclass

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
