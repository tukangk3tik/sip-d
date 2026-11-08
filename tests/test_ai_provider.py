import json
from datetime import datetime, timezone
from decimal import Decimal
import pytest


def test_parse_sourced_quote_and_reject_invalid_sibling():
    from sipd.ai_provider import Instrument, ProviderReply, build_prompt, parse_reply
    now = datetime(2026,11,8,tzinfo=timezone.utc)
    a,b = Instrument('BBCA.JK','IDR','share'), Instrument('BBRI.JK','IDR','share')
    prompt, refs = build_prompt((a,b), now=now)
    rows = [dict(ref=r,symbol=i.symbol,currency=i.currency,unit=i.unit,status='success',price='9250' if i==a else 'NaN',quoted_at='2026-11-06T09:00:00Z',price_kind='close',source_url='https://example.com/quote') for r,i in refs.items()]
    result = parse_reply(ProviderReply(json.dumps({'quotes':rows}),frozenset({'https://example.com/quote'}),True),refs,now=now,source='AI')
    assert result.quotes[a].price == Decimal('9250')
    assert b in result.errors
    with pytest.raises(ValueError):
        parse_reply(ProviderReply('{}',frozenset(),False),refs,now=now,source='AI')


def _slow_worker(pipe,*args):
    import time
    time.sleep(10)


def test_hard_deadline_terminates_worker(monkeypatch):
    import multiprocessing,time
    from sipd import ai_provider as ai
    from sipd.ai_settings import AIConfig
    monkeypatch.setattr(ai,'_worker',_slow_worker)
    before={p.pid for p in multiprocessing.active_children()}
    start=time.monotonic()
    with pytest.raises(ai.AIProviderError,match='timeout'):
        ai.fetch_ai_batch(AIConfig('openai','test','secret'),(ai.Instrument('BBCA.JK','IDR','share'),),now=datetime.now(timezone.utc),timeout_seconds=.2)
    assert time.monotonic()-start<3
    assert {p.pid for p in multiprocessing.active_children()}==before


def test_malformed_sibling_does_not_discard_valid_quote():
    from sipd.ai_provider import Instrument,ProviderReply,build_prompt,parse_reply
    now=datetime(2026,11,8,tzinfo=timezone.utc)
    a,b=Instrument('A','IDR','share'),Instrument('B','IDR','share')
    _,refs=build_prompt((a,b),now=now)
    valid=dict(ref='q0',symbol='A',currency='IDR',unit='share',status='success',price='100',quoted_at='2026-11-07T00:00:00Z',price_kind='close',source_url='https://example.com')
    rows=[valid,dict(valid,ref='q1',symbol='B',quoted_at=None),{'ref':[]}]
    result=parse_reply(ProviderReply(json.dumps({'quotes':rows}),frozenset({'https://example.com'}),True),refs,now=now,source='AI')
    assert result.quotes[a].price==Decimal('100') and b in result.errors


@pytest.mark.parametrize('service',['openai','claude'])
def test_official_http_adapter_extracts_search_evidence(service,monkeypatch):
    from sipd.ai_provider import _request
    from sipd.ai_settings import AIConfig
    import requests
    payloads=[]
    data=({'status':'completed','output':[{'type':'web_search_call','status':'completed','action':{'sources':[{'url':'https://example.com'}]}},{'type':'message','content':[{'type':'output_text','text':'{"quotes":[]}'}]}]} if service=='openai' else {'stop_reason':'end_turn','content':[{'type':'web_search_tool_result','content':[{'type':'web_search_result','url':'https://example.com'}]},{'type':'text','text':'{"quotes":[]}'}]})
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,size):yield json.dumps(data).encode()
    def post(url,**kwargs):
        payloads.append((url,kwargs));return Response()
    monkeypatch.setattr(requests,'post',post)
    reply=_request(AIConfig(service,'test','secret'),'prompt')
    assert reply.searched and reply.source_urls==frozenset({'https://example.com'})
    assert len(payloads)==1 and payloads[0][1]['allow_redirects'] is False
    assert payloads[0][1]['json']['tools'][0]['type'].startswith('web_search')


@pytest.mark.parametrize('override',[
    {'price':'0'},{'price':'-1'},{'price':'Infinity'},{'price':123},
    {'currency':'USD'},{'symbol':'OTHER'},{'unit':'lot'},
    {'quoted_at':'2000-01-01T00:00:00Z'},{'quoted_at':'2099-01-01T00:00:00Z'},
    {'quoted_at':'2026-11-07T00:00:00'},{'source_url':'javascript:alert(1)'},
    {'source_url':'https://unretrieved.example'},{'status':'unavailable'},
])
def test_invalid_quotes_fail_closed(override):
    from sipd.ai_provider import Instrument,ProviderReply,build_prompt,parse_reply
    now=datetime(2026,11,8,tzinfo=timezone.utc);i=Instrument('A','IDR','share')
    _,refs=build_prompt((i,),now=now)
    row=dict(ref='q0',symbol='A',currency='IDR',unit='share',status='success',price='100',quoted_at='2026-11-07T00:00:00Z',price_kind='close',source_url='https://example.com')
    row.update(override)
    result=parse_reply(ProviderReply(json.dumps({'quotes':[row]}),frozenset({'https://example.com'}),True),refs,now=now,source='AI')
    assert not result.quotes and i in result.errors
