from dataclasses import dataclass, field
from cryptography.fernet import Fernet, InvalidToken
from sipd.db import transaction

SERVICES = {'openai', 'claude'}

@dataclass(frozen=True)
class AIConfig:
    service: str
    model: str
    api_key: str = field(repr=False)
    revision: int = 0


def cipher(key):
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise ValueError('AI encryption key is not configured correctly') from None


def read_ai_settings(db):
    row = db.execute('SELECT * FROM ai_settings WHERE id=1').fetchone()
    return dict(active_service=row['active_service'], revision=row['revision'],
                **{f'{s}_model': row[f'{s}_model'] for s in SERVICES},
                **{f'{s}_configured': bool(row[f'{s}_key']) for s in SERVICES})


def save_ai_settings(db, *, encryption_key, active_service, models, replacements, removals):
    crypto = cipher(encryption_key)
    if active_service not in SERVICES | {None} or (set(models) | set(replacements) | removals) - SERVICES:
        raise ValueError('Invalid AI service')
    if any(replacements.get(s) for s in removals):
        raise ValueError('Cannot replace and remove the same key')
    with transaction(db):
        old = dict(db.execute('SELECT * FROM ai_settings WHERE id=1').fetchone())
        new = old.copy()
        for s in SERVICES:
            model = models.get(s, old[f'{s}_model']).strip()
            key = replacements.get(s, '').strip()
            if len(model) > 128 or any(ord(c) < 32 for c in model) or len(key) > 4096 or any(c.isspace() for c in key):
                raise ValueError('Invalid AI model or key')
            new[f'{s}_model'] = model
            if s in removals:
                new[f'{s}_key'] = ''
            elif key:
                # Preserve the revision for an unchanged plaintext credential.
                try:
                    unchanged = crypto.decrypt(old[f'{s}_key'].encode()).decode() == key
                except (InvalidToken, ValueError):
                    unchanged = False
                if not unchanged:
                    new[f'{s}_key'] = crypto.encrypt(key.encode()).decode()
        if active_service in removals:
            active_service = None
        if active_service and (not new[f'{active_service}_key'] or not new[f'{active_service}_model']):
            raise ValueError('AI service requires an API key and model')
        new['active_service'] = active_service
        fields = ['active_service','openai_model','claude_model','openai_key','claude_key']
        if any(old[f] != new[f] for f in fields):
            db.execute('UPDATE ai_settings SET active_service=?,openai_model=?,claude_model=?,openai_key=?,claude_key=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE id=1', tuple(new[f] for f in fields))


def load_ai_config(db, *, encryption_key, service=None):
    row = db.execute('SELECT * FROM ai_settings WHERE id=1').fetchone()
    service = service or row['active_service']
    if service not in SERVICES or not row[f'{service}_key'] or not row[f'{service}_model']:
        raise ValueError('AI pricing is not configured')
    try:
        key = cipher(encryption_key).decrypt(row[f'{service}_key'].encode()).decode()
    except (InvalidToken, ValueError):
        raise ValueError('Cannot read AI credentials; check encryption configuration') from None
    return AIConfig(service, row[f'{service}_model'], key, row['revision'])
