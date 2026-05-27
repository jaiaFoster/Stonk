# Railway Port Hotfix v1

## Problem

The previous `railway.toml` used:

```toml
[deploy]
startCommand = "gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 300"
```

On Railway, `$PORT` was passed through to Gunicorn literally instead of being shell-expanded. Gunicorn then failed with:

```text
Error: '$PORT' is not a valid port number.
```

## Fix

`railway.toml` now starts a tiny shell script:

```toml
[deploy]
startCommand = "sh start.sh"
```

`start.sh` expands `${PORT:-8080}` inside the shell, then execs Gunicorn:

```sh
exec gunicorn main:app --bind "0.0.0.0:${PORT_TO_BIND}" --workers "${WEB_CONCURRENCY_TO_USE}" --threads "${GUNICORN_THREADS_TO_USE}" --timeout "${GUNICORN_TIMEOUT_TO_USE}"
```

## Expected deploy log

```text
Starting Algo Stock Advisor with Gunicorn on 0.0.0.0:<railway port>
```

The Flask development-server warning should disappear once this start command is being used.
