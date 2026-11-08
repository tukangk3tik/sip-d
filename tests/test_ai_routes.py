import pytest
from datetime import datetime,timezone
from decimal import Decimal
from sipd.db import connect
from sipd.ai_provider import BatchResult
from sipd.providers import Quote


def test_admin_settings_and_secret_redaction(app,client,admin_session):
    from cryptography.fernet import Fernet
    app.config['SIPD_AI_ENCRYPTION_KEY']=Fernet.generate_key().decode()
    client.set_cookie('sipd_session',admin_session)
    assert client.post('/settings/ai',data={'csrf_token':'csrf','active_service':'openai','openai_model':'test','openai_key':'private-secret'}).status_code==303
    page=client.get('/settings').text
    assert 'private-secret' not in page and 'Terkonfigurasi' in page
    db=connect(app.config['SIPD_DB']);db.execute('UPDATE users SET is_admin=0');db.close()
    assert client.post('/settings/ai',data={'csrf_token':'csrf'}).status_code==403
    assert 'name="openai_key"' not in client.get('/settings').text


def test_refresh_batches_owned_ai_prices_and_is_idempotent(app,client,existing_session,monkeypatch):
    from sipd import routes
    client.set_cookie('sipd_session',existing_session)
    db=connect(app.config['SIPD_DB'])
    db.execute("INSERT INTO assets(user_id,investment_type_id,name,unit,quote_currency,pricing_mode,provider,provider_symbol) VALUES(1,1,'BBCA','share','IDR','automatic','ai_scrape','BBCA.JK'),(1,1,'BBRI','share','IDR','automatic','ai_scrape','BBRI.JK')")
    db.execute("UPDATE assets SET provider_symbol=name||'.JK'")
    calls=[]
    def batch(path,instruments,**kw):
        calls.append(instruments)
        return BatchResult(quotes={instruments[0]:Quote(Decimal('9000'),'IDR','AI',datetime.now(timezone.utc),'https://example.com','close')},errors={instruments[1]:'unavailable'},revision=0)
    monkeypatch.setattr(routes,'get_ai_quotes',batch)
    monkeypatch.setattr(routes,'usd_idr_quote',lambda: Quote(Decimal('16000'),'IDR','FX',datetime.now(timezone.utc)))
    for _ in range(2): assert client.post('/refresh',data={'csrf_token':'csrf','refresh_key':'one'}).status_code==303
    assert len(calls)==1 and len(calls[0])==2
    assert db.execute('SELECT price,source_url FROM asset_prices').fetchone()['price']=='9000'
    assert db.execute('SELECT COUNT(*) FROM portfolio_snapshots').fetchone()[0]==1
    assert db.execute('SELECT status FROM price_refreshes').fetchone()[0]=='partial'
    db.close()


@pytest.mark.parametrize('ok,message,status', [
    (True, 'Connection ready', 200),
    (False, 'Connection unavailable; check key, model and provider limits', 503),
])
def test_connection_result_returns_json_without_leaving_settings(app,client,admin_session,monkeypatch,ok,message,status):
    from sipd import routes
    client.set_cookie('sipd_session',admin_session)
    monkeypatch.setattr(routes,'test_ai_connection',lambda *args,**kwargs: (ok,message))
    response=client.post('/settings/ai/openai/test',data={'csrf_token':'csrf'},headers={'Accept':'application/json'})
    assert response.status_code==status
    assert response.is_json and response.get_json()['ok'] is ok
    assert response.get_json()['message']
    page=client.get('/settings').text
    assert '<dialog' in page and 'id="ai-test-dialog"' in page
    assert 'data-ai-test' in page


def test_connection_test_without_javascript_returns_to_settings(client,admin_session,monkeypatch):
    from sipd import routes
    client.set_cookie('sipd_session',admin_session)
    monkeypatch.setattr(routes,'test_ai_connection',lambda *args,**kwargs: (True,'Connection ready'))
    response=client.post('/settings/ai/openai/test',data={'csrf_token':'csrf'})
    assert response.status_code==303
    assert response.headers['Location'].startswith('/settings?ai_test=success')
