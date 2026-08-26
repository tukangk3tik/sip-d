from decimal import Decimal

import pandas as pd

from sipd import providers


def test_yahoo_quotes_batches_final_close(monkeypatch):
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
