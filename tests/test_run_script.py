from pathlib import Path


def test_local_launcher_uses_venv_and_gunicorn():
    script = Path("run.sh").read_text()
    assert "python3 -m venv .venv" in script
    assert ".venv/bin/pip install -r requirements.txt" in script
    assert "find sipd tests -type d -name __pycache__ -prune -exec rm -rf {} +" in script
    assert "SIPD_DB=${SIPD_DB:-data/sip-d.db}" in script
    assert ".venv/bin/gunicorn --workers 2 --timeout 180 --reload --bind \"${SIPD_ADDR:-127.0.0.1:8090}\" sipd.wsgi:app" in script
