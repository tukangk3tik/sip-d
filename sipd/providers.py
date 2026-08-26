import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

import yfinance as yf


@dataclass(frozen=True)
class Quote:
    price: Decimal
    currency: str
    source: str
    at: datetime


def _map_final_closes(frame, symbols):
    quotes, errors = {}, {}
    for symbol in symbols:
        try:
            close = frame[(symbol, "Close")] if len(symbols) > 1 else frame["Close"]
            close = close.dropna()
            price = Decimal(str(close.iloc[-1]))
            if price <= 0:
                raise ValueError("Yahoo Finance returned no valid quote")
            quotes[symbol] = Quote(price, "IDR", "Yahoo Finance (yfinance)", datetime.now(timezone.utc))
        except (KeyError, IndexError, ValueError) as error:
            errors[symbol] = str(error) or "Yahoo Finance returned no valid quote"
    return quotes, errors


@lru_cache(maxsize=16)
def _cached_yahoo_quotes(symbols: tuple[str, ...], time_bucket: int):
    try:
        frame = yf.download(list(symbols), period="5d", interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=False)
    except Exception as error:
        return {}, {symbol: str(error) for symbol in symbols}
    return _map_final_closes(frame, symbols)


def yahoo_quotes(symbols: tuple[str, ...]):
    return _cached_yahoo_quotes(tuple(sorted(symbols)), int(time.time() // 30))
