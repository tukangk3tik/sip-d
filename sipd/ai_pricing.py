import json
import secrets
from datetime import datetime, timezone, timedelta
from sipd.db import connect, transaction
from sipd.ai_settings import load_ai_config, read_ai_settings
from sipd.ai_provider import Instrument, BatchResult, AIProviderError, fetch_ai_batch, build_prompt
from sipd.providers import Quote
from sipd.repositories import as_db_time, parse_db_time
from decimal import Decimal


def get_ai_quotes(path,instruments,*,encryption_key,ttl_seconds=900,allow_fetch=True,service=None,force=False):
    instruments=tuple(dict.fromkeys(instruments)); result=BatchResult()
    if not instruments: return result
    db=connect(path); token=None; config=None
    now=datetime.now(timezone.utc)
    try:
        public=read_ai_settings(db); result.revision=public['revision']
        selected=service or public['active_service']
        namespace=f'ai_scrape:{selected}:{result.revision}'
        pending=[]
        for i in instruments:
            try:
                build_prompt((i,),now=now)
            except ValueError:
                result.errors[i]='Invalid AI instrument'
                continue
            key=json.dumps([i.symbol,i.currency,i.unit])
            row=db.execute('SELECT * FROM quote_cache WHERE provider=? AND symbol=?',(namespace,key)).fetchone()
            if not force and row and row['price'] and row['quoted_at']:
                at=parse_db_time(row['quoted_at']); fetched=parse_db_time(row['fetched_at'])
                if at and fetched and 0<=(now-at).total_seconds()<=604800 and 0<=(now-fetched).total_seconds()<=ttl_seconds:
                    result.quotes[i]=Quote(Decimal(row['price']),i.currency,row['source'],at,row['source_url'],row['price_kind']); result.cached.add(i); continue
            if row and row['backoff_until'] and parse_db_time(row['backoff_until'])>now:
                result.errors[i]='AI quote temporarily unavailable'; continue
            pending.append(i)
        if not pending: return result
        result.errors.update({i:'AI pricing unavailable; using last known price' for i in pending})
        if not allow_fetch: return result
        try:
            build_prompt(tuple(pending),now=now)
        except ValueError:
            return result
        try: config=load_ai_config(db,encryption_key=encryption_key,service=selected)
        except ValueError: return result
        with transaction(db):
            db.execute('INSERT OR IGNORE INTO ai_runtime(service) VALUES(?)',(config.service,))
            state=db.execute('SELECT * FROM ai_runtime WHERE service=?',(config.service,)).fetchone()
            if (state['lease_until'] and parse_db_time(state['lease_until'])>now) or (state['backoff_until'] and parse_db_time(state['backoff_until'])>now): return result
            token=secrets.token_urlsafe(24)
            db.execute('UPDATE ai_runtime SET lease_token=?,lease_until=? WHERE service=?',(token,as_db_time(now+timedelta(seconds=75)),config.service))
        if not force:
            # Another request may have populated the cache before this lease was claimed.
            recent=get_ai_quotes(path,tuple(pending),encryption_key=encryption_key,ttl_seconds=ttl_seconds,allow_fetch=False,service=selected)
            for i,q in recent.quotes.items():
                result.quotes[i]=q
                result.cached.add(i)
                result.errors.pop(i,None)
            pending=[i for i in pending if i not in recent.quotes]
            if not pending:
                return result
        try:
            fetched=fetch_ai_batch(config,tuple(pending),now=now)
        except (ValueError, OSError) as error:
            with transaction(db):
                state=db.execute('SELECT * FROM ai_runtime WHERE service=? AND lease_token=?',(config.service,token)).fetchone()
                if state:
                    delay=min(1800,60*2**min(state['failure_count'],5)); until=datetime.now(timezone.utc)+timedelta(seconds=delay)
                    if isinstance(error,AIProviderError) and error.retry_at and error.retry_at>until: until=error.retry_at
                    db.execute('UPDATE ai_runtime SET failure_count=failure_count+1,backoff_until=? WHERE service=? AND lease_token=?',(as_db_time(until),config.service,token))
            return result
        with transaction(db):
            state=db.execute('SELECT lease_token FROM ai_runtime WHERE service=?',(config.service,)).fetchone()
            if read_ai_settings(db)['revision']!=config.revision or state['lease_token']!=token: return result
            fetched_at=datetime.now(timezone.utc)
            for i in pending:
                key=json.dumps([i.symbol,i.currency,i.unit]); q=fetched.quotes.get(i)
                if q:
                    db.execute('''INSERT INTO quote_cache(provider,symbol,price,currency,source,fetched_at,quoted_at,source_url,price_kind)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,symbol) DO UPDATE SET price=excluded.price,currency=excluded.currency,source=excluded.source,fetched_at=excluded.fetched_at,quoted_at=excluded.quoted_at,source_url=excluded.source_url,price_kind=excluded.price_kind,backoff_until=NULL''',(namespace,key,str(q.price),q.currency,q.source,as_db_time(fetched_at),as_db_time(q.at),q.source_url,q.price_kind))
                    result.quotes[i]=q; result.errors.pop(i,None)
                else:
                    db.execute('''INSERT INTO quote_cache(provider,symbol,backoff_until) VALUES(?,?,?) ON CONFLICT(provider,symbol) DO UPDATE SET backoff_until=excluded.backoff_until''',(namespace,key,as_db_time(fetched_at+timedelta(seconds=900))))
            db.execute('UPDATE ai_runtime SET failure_count=0,backoff_until=NULL WHERE service=? AND lease_token=?',(config.service,token))
        return result
    finally:
        if token and config: db.execute('UPDATE ai_runtime SET lease_token=NULL,lease_until=NULL WHERE service=? AND lease_token=?',(config.service,token))
        db.close()


def test_ai_connection(path,*,encryption_key,service):
    # Explicit administrator action; use an uncached instrument by evicting no data.
    result=get_ai_quotes(path,(Instrument('BBCA.JK','IDR','share'),),encryption_key=encryption_key,service=service,force=True)
    return bool(result.quotes), 'Connection ready' if result.quotes else 'Connection unavailable; check key, model and provider limits'
