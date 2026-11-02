"""Central configuration.

Nothing secret is hard-coded here: database credentials and every
analysis parameter are read from environment variables (see .env.example).
`config.py` is imported by the Flask app, by the CLI scripts and by the
test-suite, so a single change of environment changes the whole system.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


def _get(key: str, default: str) -> str:
    value = os.getenv(key)
    return default if value is None or value == "" else value


def _get_int(key: str, default: int) -> int:
    return int(_get(key, str(default)))


def _get_float(key: str, default: float) -> float:
    return float(_get(key, str(default)))


@dataclass(frozen=True)
class Config:
    # ---- database -----------------------------------------------------
    DB_HOST: str = field(default_factory=lambda: _get("DB_HOST", "127.0.0.1"))
    DB_PORT: int = field(default_factory=lambda: _get_int("DB_PORT", 3306))
    DB_USER: str = field(default_factory=lambda: _get("DB_USER", "scm_app"))
    DB_PASSWORD: str = field(default_factory=lambda: _get("DB_PASSWORD", ""))
    DB_NAME: str = field(default_factory=lambda: _get("DB_NAME", "supplychain"))
    DB_POOL_SIZE: int = field(default_factory=lambda: _get_int("DB_POOL_SIZE", 5))
    DB_CONNECT_TIMEOUT: int = field(default_factory=lambda: _get_int("DB_CONNECT_TIMEOUT", 8))

    # ---- flask --------------------------------------------------------
    FLASK_HOST: str = field(default_factory=lambda: _get("FLASK_HOST", "0.0.0.0"))
    FLASK_PORT: int = field(default_factory=lambda: _get_int("FLASK_PORT", 5000))
    FLASK_DEBUG: bool = field(default_factory=lambda: _get("FLASK_DEBUG", "0") == "1")
    SECRET_KEY: str = field(default_factory=lambda: _get("SECRET_KEY", "change-me-in-env"))
    APP_ENV: str = field(default_factory=lambda: _get("APP_ENV", "development"))

    # ---- analysis parameters (documented assumptions) -----------------
    # Rolling demand window used for the average / standard deviation.
    WINDOW_DAYS: int = field(default_factory=lambda: _get_int("WINDOW_DAYS", 90))
    # Target cycle service level -> z factor via NormalDist.inv_cdf.
    SERVICE_LEVEL: float = field(default_factory=lambda: _get_float("SERVICE_LEVEL", 0.95))
    # Alternative safety stock policy (days of average demand).
    SAFETY_STOCK_DAYS: float = field(default_factory=lambda: _get_float("SAFETY_STOCK_DAYS", 7.0))
    # Policy used for the recommendation: 'zscore' | 'days' | 'max'
    SAFETY_STOCK_POLICY: str = field(default_factory=lambda: _get("SAFETY_STOCK_POLICY", "max"))
    REVIEW_PERIOD_DAYS: float = field(default_factory=lambda: _get_float("REVIEW_PERIOD_DAYS", 7.0))
    # Excess-stock test: days of cover above this -> EXCESS
    EXCESS_COVER_DAYS: float = field(default_factory=lambda: _get_float("EXCESS_COVER_DAYS", 90.0))
    # Stockout test: on-hand below this share of ROP -> STOCKOUT (else AT_RISK)
    STOCKOUT_POSITION_RATIO: float = field(
        default_factory=lambda: _get_float("STOCKOUT_POSITION_RATIO", 1.0))
    FORECAST_HORIZON_DAYS: int = field(default_factory=lambda: _get_int("FORECAST_HORIZON_DAYS", 28))
    FORECAST_HOLDOUT_DAYS: int = field(default_factory=lambda: _get_int("FORECAST_HOLDOUT_DAYS", 28))
    FORECAST_BASELINE_WINDOW: int = field(
        default_factory=lambda: _get_int("FORECAST_BASELINE_WINDOW", 28))
    # Supplier KPIs use a trailing 365-day PO window (more deliveries to judge).
    SUPPLIER_WINDOW_DAYS: int = field(
        default_factory=lambda: _get_int("SUPPLIER_WINDOW_DAYS", 365))
    # Supplier "good" targets used for grading, not for data manipulation.
    TARGET_ON_TIME_RATE: float = field(default_factory=lambda: _get_float("TARGET_ON_TIME_RATE", 0.90))
    TARGET_LEAD_TIME_VARIANCE: float = field(
        default_factory=lambda: _get_float("TARGET_LEAD_TIME_VARIANCE", 3.0))

    # ---- output paths -------------------------------------------------
    # always absolute, so charts/reports land in the same place no matter
    # which directory the app is started from
    REPORT_DIR: str = field(
        default_factory=lambda: str(
            (BASE_DIR / _get("REPORT_DIR", "reports")).resolve()))
    TIMEZONE: str = field(default_factory=lambda: _get("TIMEZONE", "Asia/Kolkata"))

    # ------------------------------------------------------------------
    def db_kwargs(self) -> dict:
        """Keyword arguments for mysql.connector.connect()."""
        return {
            "host": self.DB_HOST,
            "port": self.DB_PORT,
            "user": self.DB_USER,
            "password": self.DB_PASSWORD,
            "database": self.DB_NAME,
            "connection_timeout": self.DB_CONNECT_TIMEOUT,
            "autocommit": False,
            "charset": "utf8mb4",
        }

    def public(self) -> dict:
        """Safe subset exposed through GET /api/config (no credentials)."""
        data = asdict(self)
        for key in ("DB_PASSWORD", "SECRET_KEY", "DB_USER"):
            data.pop(key, None)
        return data


config = Config()
