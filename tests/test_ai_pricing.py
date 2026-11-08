from datetime import datetime,timezone
from decimal import Decimal
from cryptography.fernet import Fernet
from sipd.ai_settings import save_ai_settings
from sipd.ai_provider import Instrument,BatchResult
from sipd.providers import Quote
from sipd.db import connect
from sipd import ai_pricing


def test_shared_cache_and_revision_change(app,monkeypatch):
    path=app.config['SIPD_DB']; key=Fernet.generate_key().decode(); db=connect(path)
    save_ai_settings(db,encryption_key=key,active_service='openai',models={'openai':'test'},replacements={'openai':'secret'},removals=set())
    i=Instrument('BBCA.JK','IDR','share'); calls=[]
    def fetch(config,instruments,**kw):
        calls.append(instruments)
        return BatchResult(quotes={i:Quote(Decimal('9000'),'IDR','AI',datetime.now(timezone.utc),'https://example.com','close')})
    monkeypatch.setattr(ai_pricing,'fetch_ai_batch',fetch)
    first=ai_pricing.get_ai_quotes(path,(i,i),encryption_key=key)
    second=ai_pricing.get_ai_quotes(path,(i,),encryption_key=key)
    assert first.quotes[i].price==Decimal('9000') and i in second.cached
    assert calls==[(i,)]
    def changed(config,instruments,**kw):
        save_ai_settings(db,encryption_key=key,active_service='openai',models={'openai':'new'},replacements={},removals=set())
        return fetch(config,instruments,**kw)
    db.execute('DELETE FROM quote_cache')
    monkeypatch.setattr(ai_pricing,'fetch_ai_batch',changed)
    assert not ai_pricing.get_ai_quotes(path,(i,),encryption_key=key).quotes
    db.close()


def test_lease_blocks_paid_fetch_and_keeps_cache_only_get_safe(app,monkeypatch):
    path=app.config['SIPD_DB']; key=Fernet.generate_key().decode(); db=connect(path)
    save_ai_settings(db,encryption_key=key,active_service='openai',models={'openai':'test'},replacements={'openai':'secret'},removals=set())
    db.execute("INSERT INTO ai_runtime(service,lease_token,lease_until) VALUES('openai','other','2099-01-01T00:00:00Z')")
    monkeypatch.setattr(ai_pricing,'fetch_ai_batch',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('paid call')))
    i=Instrument('BBCA.JK','IDR','share')
    assert i in ai_pricing.get_ai_quotes(path,(i,),encryption_key=key).errors
    db.close()


def test_invalid_instrument_never_penalizes_shared_account(app,monkeypatch):
    path=app.config['SIPD_DB'];key=Fernet.generate_key().decode();db=connect(path)
    save_ai_settings(db,encryption_key=key,active_service='openai',models={'openai':'test'},replacements={'openai':'secret'},removals=set())
    monkeypatch.setattr(ai_pricing,'fetch_ai_batch',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('paid call')))
    i=Instrument('A'*65,'IDR','share')
    assert i in ai_pricing.get_ai_quotes(path,(i,),encryption_key=key).errors
    assert db.execute('SELECT COUNT(*) FROM ai_runtime').fetchone()[0]==0
    db.close()
