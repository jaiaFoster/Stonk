"""
main.py — Compatibility entrypoint for Algo Stock Advisor.

Railway or Gunicorn can continue using `main:app`, and local development can
continue using `python main.py`, while the real application code now lives in
`app/main.py`.
"""

import os

from app.main import app, health, run, trigger


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
    )
