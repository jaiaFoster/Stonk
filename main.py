"""
main.py — Compatibility entrypoint for Algo Stock Advisor.

Railway or Gunicorn can continue using `main:app`, and local development can
continue using `python main.py`, while the real application code lives in
`app/main.py`.
"""

from app.main import app, health, run, trigger


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Algo Stock Advisor from root main.py on 0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port)
