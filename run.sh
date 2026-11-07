#!/usr/bin/env sh
set -eu

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -r requirements.txt
find sipd tests -type d -name __pycache__ -prune -exec rm -rf {} +
SIPD_DB=${SIPD_DB:-data/sip-d.db}
export SIPD_DB
exec .venv/bin/gunicorn --reload --bind "${SIPD_ADDR:-127.0.0.1:8090}" sipd.wsgi:app
