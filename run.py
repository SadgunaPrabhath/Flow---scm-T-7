"""Entry point for the Flask development server.

    python run.py                # uses FLASK_HOST / FLASK_PORT from .env
"""
from __future__ import annotations

import logging

from api.app import create_app
from config import config

app = create_app()

if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    print(f" * Supply Chain API on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    print(f" * environment: {config.APP_ENV} | database: {config.DB_NAME}"
          f"@{config.DB_HOST}:{config.DB_PORT}")
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
