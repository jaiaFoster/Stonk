#!/bin/sh
set -eu

# Railway provides PORT at runtime. Do the expansion inside this shell script
# instead of inside railway.toml so Gunicorn never receives the literal string "$PORT".
PORT_TO_BIND="${PORT:-8080}"
WEB_CONCURRENCY_TO_USE="${WEB_CONCURRENCY:-1}"
GUNICORN_THREADS_TO_USE="${GUNICORN_THREADS:-2}"
GUNICORN_TIMEOUT_TO_USE="${GUNICORN_TIMEOUT:-300}"

echo "Starting Algo Stock Advisor with Gunicorn on 0.0.0.0:${PORT_TO_BIND}"

exec gunicorn main:app \
  --bind "0.0.0.0:${PORT_TO_BIND}" \
  --workers "${WEB_CONCURRENCY_TO_USE}" \
  --threads "${GUNICORN_THREADS_TO_USE}" \
  --timeout "${GUNICORN_TIMEOUT_TO_USE}"
