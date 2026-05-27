# Railway Hotfix

Replace/add these files:

```text
railway.toml
start.sh
docs/railway_port_hotfix_v1.md
```

This fixes Railway deploy failures caused by `$PORT` being passed literally to Gunicorn. The app now starts through `sh start.sh`, which expands Railway's runtime `PORT` value safely before launching Gunicorn.
