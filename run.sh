#!/usr/bin/env sh
set -eu

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -r requirements.txt
SIPD_DB=${SIPD_DB:-data/sip-d.db}
export SIPD_DB
exec .venv/bin/gunicorn --bind "${SIPD_ADDR:-127.0.0.1:8090}" sipd.wsgi:app
