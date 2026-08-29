from pathlib import Path

from sipd import create_app
from sipd.db import init_db


def test_app_opens_production_format_database(tmp_path):
    path = tmp_path / "sip-d.db"
    init_db(path)
    assert create_app({"SIPD_DB": str(path)}).test_client().get("/healthz").get_json() == {"status": "ok"}


def test_python_systemd_unit_runs_gunicorn():
    unit = Path("deploy/sip-d-python.service").read_text()
    assert "gunicorn --bind ${SIPD_ADDR} sipd.wsgi:app" in unit
    assert "ReadWritePaths=/var/lib/sip-d" in unit
