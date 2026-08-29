import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from urllib.parse import urlencode

import yfinance as yf
import requests


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


def _json(url):
    response = requests.get(url, timeout=6)
    if response.status_code != 200:
        raise ValueError(f"provider HTTP {response.status_code}")
    return response.json()


def kraken_quote(symbol: str):
    if symbol.strip().upper() not in {"BTC", "XBT", "BTCUSD", "XBTUSD", "XXBTZUSD"}:
        raise ValueError("unsupported Kraken asset pair")
    data = _json("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")
    if data.get("error"):
        raise ValueError(",".join(data["error"]))
    try:
        price = Decimal(str(next(iter(data["result"].values()))["c"][0]))
    except (KeyError, IndexError, StopIteration, TypeError, ValueError) as error:
        raise ValueError("ticker missing") from error
    if price <= 0:
        raise ValueError("ticker missing")
    return Quote(price, "USD", "Kraken", datetime.now(timezone.utc))


def usd_idr_quote():
    data = _json("https://api.frankfurter.dev/v2/rates?base=USD&quotes=IDR")
    if not isinstance(data, list) or len(data) != 1 or data[0].get("base") != "USD" or data[0].get("quote") != "IDR":
        raise ValueError("rate missing")
    try:
        price = Decimal(str(data[0]["rate"]))
        at = datetime.fromisoformat(data[0]["date"]).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("rate missing") from error
    if price <= 0:
        raise ValueError("rate missing")
    return Quote(price, "IDR", "Frankfurter", at)


def metals_quote(symbol: str, currency: str, unit: str, api_key: str):
    if not api_key:
        raise ValueError("Metals.dev API key not configured")
    unit = "g" if unit.lower() == "gram" else unit.lower()
    data = _json("https://api.metals.dev/v1/latest?" + urlencode({"api_key": api_key, "currency": currency, "unit": unit, "metal": symbol.lower()}))
    if data.get("status") != "success" or data.get("currency") != currency or data.get("unit") != unit:
        raise ValueError("Metals.dev returned mismatched currency or unit")
    try:
        price = Decimal(str(data["metals"][symbol.lower()]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Metals.dev returned no valid metal price") from error
    if price <= 0:
        raise ValueError("Metals.dev returned no valid metal price")
    return Quote(price, currency, "Metals.dev", datetime.now(timezone.utc))


def finnhub_quote(symbol: str, currency: str, api_key: str):
    if not api_key:
        raise ValueError("Finnhub API key not configured")
    data = _json("https://finnhub.io/api/v1/quote?" + urlencode({"symbol": symbol, "token": api_key}))
    try:
        price, timestamp = Decimal(str(data["c"])), int(data["t"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Finnhub returned no valid quote") from error
    if data.get("error") or price <= 0 or timestamp <= 0:
        raise ValueError(data.get("error") or "Finnhub returned no valid quote")
    return Quote(price, currency, "Finnhub", datetime.fromtimestamp(timestamp, timezone.utc))


def quote_for_asset(asset, *, metals_key="", finnhub_key=""):
    if asset["pricing_mode"] == "fixed":
        return Quote(Decimal("1"), asset["quote_currency"], "Fixed", datetime.now(timezone.utc))
    if asset["provider"] == "kraken":
        return kraken_quote(asset["provider_symbol"])
    if asset["provider"] == "metalsdev":
        return metals_quote(asset["provider_symbol"], asset["quote_currency"], asset["unit"], metals_key)
    if asset["provider"] == "finnhub":
        return finnhub_quote(asset["provider_symbol"], asset["quote_currency"], finnhub_key)
    if asset["provider"] == "yahoo":
        quotes, errors = yahoo_quotes((asset["provider_symbol"],))
        if asset["provider_symbol"] in quotes:
            return quotes[asset["provider_symbol"]]
        raise ValueError(errors.get(asset["provider_symbol"], "Yahoo Finance returned no valid quote"))
    raise ValueError("automatic provider unavailable")
