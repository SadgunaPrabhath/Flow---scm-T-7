"""Flask application factory.

Run with:
    python run.py                 # development server on FLASK_HOST:FLASK_PORT
    flask --app api.app:create_app run

Layers
------
config.py            settings + credentials (environment variables)
db.py                pooled MySQL access, parameterised SQL, error mapping
api/validators.py    request validation
api/errors.py        uniform JSON error envelope
api/routes_*.py      blueprints (CRUD + analytics)
analytics/           pandas / numpy / statsmodels logic
viz/                 matplotlib charts
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify
from flask.json.provider import DefaultJSONProvider

from api.errors import register_error_handlers
from api.routes_analytics import analytics_bp
from api.routes_crud import crud_bp
from config import Config, config
from db import db as default_db


class ApiJSONProvider(DefaultJSONProvider):
    """JSON rules for this API: Decimals as numbers, dates as ISO strings."""

    @staticmethod
    def default(o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


def create_app(cfg: Config | None = None) -> Flask:
    app = Flask(__name__,
                static_folder=None,
                template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
    app.json = ApiJSONProvider(app)
    cfg = cfg or config
    default_db.configure(cfg)
    app.config.update({
        "SECRET_KEY": cfg.SECRET_KEY,
        "JSON_SORT_KEYS": False,
        "MAX_CONTENT_LENGTH": 8 * 1024 * 1024,       # 8 MB upload guard
        "APP_CFG": cfg,
    })
    app.json.sort_keys = False

    _configure_logging(app)
    register_error_handlers(app)

    app.register_blueprint(crud_bp)
    app.register_blueprint(analytics_bp)

    @app.get("/")
    def explorer():
        """Tiny single-file API explorer (inline CSS/JS, no external assets)."""
        from flask import render_template
        return render_template("explorer.html")

    @app.get("/api")
    def index():
        return jsonify({
            "service": "Supply Chain Optimization & Inventory Forecasting API",
            "version": "1.0.0",
            "environment": cfg.APP_ENV,
            "docs": "docs/06_api_reference.md",
            "endpoints": {
                "health": "/api/health",
                "config": "/api/config",
                "products": "/api/products",
                "suppliers": "/api/suppliers",
                "purchase_orders": "/api/purchase-orders",
                "stock_transactions": "/api/stock-transactions",
                "demand_records": "/api/demand-records",
                "inventory": "/api/inventory",
                "reorder_recommendations": "/api/analytics/reorder-recommendations",
                "supplier_summary": "/api/analytics/suppliers",
                "demand_summary": "/api/analytics/demand",
                "risk_flags": "/api/analytics/risks",
                "forecast": "/api/analytics/forecast",
                "run_analysis": "/api/analytics/run",
                "charts": "/api/charts",
            },
            "notice": "Decision support only - the API never places purchase orders.",
        })

    return app


def _configure_logging(app: Flask) -> None:
    log_dir = Path(config.REPORT_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "api.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    if not app.logger.handlers or app.logger.level > logging.INFO:
        logging.basicConfig(level=logging.INFO)
