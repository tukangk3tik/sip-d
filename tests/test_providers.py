from decimal import Decimal
from datetime import datetime, timezone

import pandas as pd

from sipd import providers


def test_yahoo_quotes_batches_final_close(monkeypatch):
    providers._cached_yahoo_quotes.cache_clear()

    def fake_download(*args, **kwargs):
        return pd.DataFrame({
            ("BBRI.JK", "Close"): [4100, 4200],
            ("BMRI.JK", "Close"): [5000, 5100],
        })

    monkeypatch.setattr(providers.yf, "download", fake_download)
    quotes, errors = providers.yahoo_quotes(("BBRI.JK", "BMRI.JK"))

    assert quotes["BBRI.JK"].price == Decimal("4200")
    assert quotes["BMRI.JK"].price == Decimal("5100")
    assert errors == {}


def test_yahoo_quotes_reports_empty_close_without_pandas_error(monkeypatch):
    providers._cached_yahoo_quotes.cache_clear()

    def fake_download(*args, **kwargs):
        return pd.DataFrame({
            ("BBRI.JK", "Close"): [],
            ("BMRI.JK", "Close"): [],
        })

    monkeypatch.setattr(providers.yf, "download", fake_download)
    quotes, errors = providers.yahoo_quotes(("BBRI.JK", "BMRI.JK"))

    assert quotes == {}
    assert errors == {
        "BBRI.JK": "Yahoo Finance returned no quote data",
        "BMRI.JK": "Yahoo Finance returned no quote data",
    }


def test_yahoo_chart_quote_uses_latest_valid_close(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "chart": {
                    "result": [{
                        "meta": {"currency": "IDR"},
                        "timestamp": [1788224400, 1788310800],
                        "indicators": {"quote": [{"close": [4100, 4200]}]},
                    }]
                }
            }

    monkeypatch.setattr(providers.requests, "get", lambda url, timeout: Response())
    quote = providers.yahoo_chart_quote("BBRI.JK")

    assert quote.price == Decimal("4200")
    assert quote.currency == "IDR"
    assert quote.source == "Yahoo Finance (chart)"


def test_yahoo_chart_quote_reports_empty_data(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {
                "chart": {
                    "result": [{
                        "meta": {"currency": "IDR"},
                        "timestamp": [1788224400],
                        "indicators": {"quote": [{"close": [None]}]},
                    }]
                }
            }

    monkeypatch.setattr(providers.requests, "get", lambda url, timeout: Response())

    try:
        providers.yahoo_chart_quote("BBRI.JK")
    except ValueError as error:
        assert str(error) == "Yahoo Finance returned no quote data"
    else:
        raise AssertionError("expected ValueError")


def test_kraken_quote_normalizes_btc_alias(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"error": [], "result": {"XXBTZUSD": {"c": ["68000.50"]}}}

    monkeypatch.setattr(providers.requests, "get", lambda url, timeout: Response())
    quote = providers.kraken_quote("BTC")
    assert quote.price == Decimal("68000.50")
    assert quote.currency == "USD"


def test_frankfurter_rate_validates_response(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return [{"date": "2026-08-26", "base": "USD", "quote": "IDR", "rate": "16500.25"}]

    monkeypatch.setattr(providers.requests, "get", lambda url, timeout: Response())
    quote = providers.usd_idr_quote()
    assert quote.price == Decimal("16500.25")
    assert quote.at == datetime(2026, 8, 26, tzinfo=timezone.utc)


def test_quote_for_asset_uses_configured_metal_key(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"status": "success", "currency": "IDR", "unit": "g", "metals": {"gold": "1800000"}}

    monkeypatch.setattr(providers.requests, "get", lambda url, timeout: Response())
    quote = providers.quote_for_asset({"pricing_mode": "automatic", "provider": "metalsdev", "provider_symbol": "gold", "quote_currency": "IDR", "unit": "gram"}, metals_key="key")
    assert quote.price == Decimal("1800000")
    assert quote.source == "Metals.dev"
