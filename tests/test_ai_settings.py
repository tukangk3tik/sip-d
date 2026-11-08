import json

import pytest
from cryptography.fernet import Fernet

from sipd.db import connect


@pytest.fixture
def settings_db(app):
    db = connect(app.config["SIPD_DB"])
    yield db
    db.close()


@pytest.fixture
def encryption_key():
    return Fernet.generate_key().decode()


def save(db, key, **overrides):
    from sipd.ai_settings import save_ai_settings
    values = dict(encryption_key=key, active_service="openai", models={"openai": "test-model"},
                  replacements={"openai": "test-api-secret"}, removals=set())
    values.update(overrides)
    save_ai_settings(db, **values)


def test_shared_keys_roundtrip_encrypted(settings_db, encryption_key):
    from sipd.ai_settings import load_ai_config, read_ai_settings
    save(settings_db, encryption_key)
    raw = settings_db.execute("SELECT * FROM ai_settings").fetchone()
    assert "test-api-secret" not in raw["openai_key"]
    config = load_ai_config(settings_db, encryption_key=encryption_key)
    assert config.api_key == "test-api-secret"
    assert config.service == "openai" and config.model == "test-model" and config.revision == 1
    assert "test-api-secret" not in repr(config)
    public = read_ai_settings(settings_db)
    assert public["openai_configured"] is True
    assert "test-api-secret" not in json.dumps(public)
    assert raw["openai_key"] not in json.dumps(public)


def test_blank_keeps_key_and_explicit_remove_deletes(settings_db, encryption_key):
    from sipd.ai_settings import load_ai_config, read_ai_settings
    save(settings_db, encryption_key)
    save(settings_db, encryption_key, replacements={"openai": ""})
    assert load_ai_config(settings_db, encryption_key=encryption_key).api_key == "test-api-secret"
    assert read_ai_settings(settings_db)["revision"] == 1
    save(settings_db, encryption_key, replacements={}, removals={"openai"})
    assert read_ai_settings(settings_db)["active_service"] is None
    assert not read_ai_settings(settings_db)["openai_configured"]
    with pytest.raises(ValueError):
        load_ai_config(settings_db, encryption_key=encryption_key)


@pytest.mark.parametrize("bad_key", ["", "invalid", None])
def test_invalid_crypto_configuration_preserves_settings(settings_db, encryption_key, bad_key):
    save(settings_db, encryption_key)
    before = tuple(settings_db.execute("SELECT * FROM ai_settings").fetchone())
    with pytest.raises(ValueError):
        save(settings_db, bad_key)
    assert tuple(settings_db.execute("SELECT * FROM ai_settings").fetchone()) == before


def test_tampered_ciphertext_and_wrong_key_fail_closed(settings_db, encryption_key):
    from sipd.ai_settings import load_ai_config
    save(settings_db, encryption_key)
    with pytest.raises(ValueError, match="credentials"):
        load_ai_config(settings_db, encryption_key=Fernet.generate_key().decode())
    settings_db.execute("UPDATE ai_settings SET openai_key='broken'")
    with pytest.raises(ValueError, match="credentials"):
        load_ai_config(settings_db, encryption_key=encryption_key)


@pytest.mark.parametrize("overrides", [
    {"active_service": "unknown"}, {"models": {"openai": ""}},
    {"replacements": {}}, {"removals": {"unknown"}},
    {"replacements": {"openai": "key\nsecret"}}, {"models": {"openai": "x"*129}},
    {"removals": {"openai"}}, {"models": {"unknown": "model"}},
])
def test_active_service_requires_key_and_model(settings_db, encryption_key, overrides):
    with pytest.raises(ValueError):
        save(settings_db, encryption_key, **overrides)
    assert settings_db.execute("SELECT revision FROM ai_settings").fetchone()[0] == 0
