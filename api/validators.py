"""Request validation helpers.

Every route validates its inputs here and raises ValidationError with a
list of field-level problems, so the client gets 422 + machine-readable
details instead of a 500.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import request

from api.errors import ApiError, ValidationError

DATE_FMT = "%Y-%m-%d"
SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/]{1,31}$")

STOCK_TXN_TYPES = ("RECEIPT", "ISSUE", "ADJUSTMENT", "RETURN")
PO_STATUSES = ("OPEN", "PARTIAL", "RECEIVED", "CANCELLED")
POSITIVE_TXN_TYPES = ("RECEIPT", "RETURN")


# ---------------------------------------------------------------- scalars
def _fail(field: str, message: str, value=None):
    detail = {"field": field, "message": message}
    if value is not None:
        detail["value"] = str(value)
    raise ValidationError([detail])


def as_int(value, field, *, minimum=None, maximum=None, required=True):
    if value is None or value == "":
        if required:
            _fail(field, "This field is required.")
        return None
    if isinstance(value, bool):
        _fail(field, "Must be a whole number.", value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        _fail(field, "Must be a whole number.", value)
    if isinstance(value, float) and not float(value).is_integer():
        _fail(field, "Must be a whole number.", value)
    if minimum is not None and number < minimum:
        _fail(field, f"Must be >= {minimum}.", value)
    if maximum is not None and number > maximum:
        _fail(field, f"Must be <= {maximum}.", value)
    return number


def as_decimal(value, field, *, minimum=None, maximum=None, required=True):
    if value is None or value == "":
        if required:
            _fail(field, "This field is required.")
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        _fail(field, "Must be a decimal number.", value)
    if not number.is_finite():
        _fail(field, "Must be a finite decimal number.", value)
    if minimum is not None and number < Decimal(str(minimum)):
        _fail(field, f"Must be >= {minimum}.", value)
    if maximum is not None and number > Decimal(str(maximum)):
        _fail(field, f"Must be <= {maximum}.", value)
    return number


def as_text(value, field, *, min_len=1, max_len=255, required=True, allow_none=False):
    if value is None:
        if allow_none:
            return None
        if required:
            _fail(field, "This field is required.")
        return None
    if not isinstance(value, str):
        _fail(field, "Must be a string.", value)
    text = value.strip()
    if not text:
        if allow_none:
            return None
        _fail(field, "Must not be blank.", value)
    if len(text) < min_len:
        _fail(field, f"Must be at least {min_len} character(s).", value)
    if len(text) > max_len:
        _fail(field, f"Must be at most {max_len} characters.", value)
    return text


def as_enum(value, field, allowed, *, required=True):
    if value is None or value == "":
        if required:
            _fail(field, "This field is required.")
        return None
    text = str(value).strip().upper()
    if text not in allowed:
        _fail(field, f"Must be one of: {', '.join(allowed)}.", value)
    return text


def as_date(value, field, *, required=True):
    if value is None or value == "":
        if required:
            _fail(field, "This field is required (YYYY-MM-DD).")
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value).strip(), DATE_FMT).date()
    except ValueError:
        _fail(field, "Must be a valid date in YYYY-MM-DD format.", value)


def as_bool(value, field, *, required=False, default=None):
    if value is None:
        if required:
            _fail(field, "This field is required.")
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y"):
        return True
    if text in ("0", "false", "no", "n"):
        return False
    _fail(field, "Must be a boolean (true/false).", value)


def as_email(value, field, *, required=False):
    text = as_text(value, field, required=required, allow_none=not required, max_len=160)
    if text is None:
        return None
    if not re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", text):
        _fail(field, "Must be a valid e-mail address.", value)
    return text


def as_sku(value, field="sku"):
    """SKU codes are stored upper-case so 'auto-brk-1' and 'AUTO-BRK-1' collide
    on the unique index instead of creating two products."""
    text = as_text(value, field, max_len=32).upper()
    if not SKU_RE.match(text):
        _fail(field, "SKU must be 2-32 chars: A-Z, 0-9, '-', '_' or '/'.", value)
    return text


# ---------------------------------------------------------------- bodies
def json_body() -> dict:
    if not request.data:
        raise ApiError("Request body is required (application/json).",
                       status=400, code="EMPTY_BODY")
    if not request.is_json:
        raise ApiError("Content-Type must be application/json.",
                       status=415, code="UNSUPPORTED_MEDIA_TYPE")
    payload = request.get_json(silent=True)
    if payload is None:
        raise ApiError("Malformed JSON body.", status=400, code="INVALID_JSON")
    if not isinstance(payload, dict):
        raise ApiError("JSON body must be an object.", status=400, code="INVALID_JSON")
    return payload


def require_fields(payload: dict, fields, *, partial=False) -> None:
    if partial:
        return
    missing = [f for f in fields if f not in payload or payload[f] in (None, "")]
    if missing:
        raise ValidationError([{"field": f, "message": "This field is required."} for f in missing])


def reject_unknown_fields(payload: dict, allowed) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ValidationError([{"field": f, "message": "Unknown field."} for f in unknown])


def pagination() -> tuple[int, int]:
    args = request.args
    limit = as_int(args.get("limit", 50), "limit", minimum=1, maximum=500)
    offset = as_int(args.get("offset", 0), "offset", minimum=0, maximum=1_000_000)
    return limit, offset


# ------------------------------------------------------------- resources
PRODUCT_FIELDS = ("sku", "product_name", "category", "unit_cost", "lead_time_days",
                  "reorder_point", "supplier_id", "is_active")


def validate_product(payload: dict, *, partial=False) -> dict:
    reject_unknown_fields(payload, PRODUCT_FIELDS)
    require_fields(payload, ("sku", "product_name", "category", "unit_cost",
                             "lead_time_days", "supplier_id"), partial=partial)
    out: dict = {}
    if "sku" in payload or not partial:
        out["sku"] = as_sku(payload.get("sku"))
    if "product_name" in payload or not partial:
        out["product_name"] = as_text(payload.get("product_name"), "product_name", max_len=150)
    if "category" in payload or not partial:
        out["category"] = as_text(payload.get("category"), "category", max_len=60)
    if "unit_cost" in payload or not partial:
        out["unit_cost"] = as_decimal(payload.get("unit_cost"), "unit_cost",
                                      minimum=0.01, maximum=10_000_000)
    if "lead_time_days" in payload or not partial:
        out["lead_time_days"] = as_int(payload.get("lead_time_days"), "lead_time_days",
                                       minimum=1, maximum=180)
    if "supplier_id" in payload or not partial:
        out["supplier_id"] = as_int(payload.get("supplier_id"), "supplier_id", minimum=1)
    if "reorder_point" in payload or not partial:
        out["reorder_point"] = as_int(payload.get("reorder_point", 0), "reorder_point",
                                      minimum=0, maximum=1_000_000, required=False) or 0
    if "is_active" in payload or not partial:
        out["is_active"] = 1 if as_bool(payload.get("is_active", True), "is_active") else 0
    return out


SUPPLIER_FIELDS = ("supplier_name", "contact_email", "country",
                   "promised_lead_time_days", "is_active")


def validate_supplier(payload: dict, *, partial=False) -> dict:
    reject_unknown_fields(payload, SUPPLIER_FIELDS)
    require_fields(payload, ("supplier_name", "promised_lead_time_days"), partial=partial)
    out: dict = {}
    if "supplier_name" in payload or not partial:
        out["supplier_name"] = as_text(payload.get("supplier_name"), "supplier_name", max_len=120)
    if "contact_email" in payload or not partial:
        out["contact_email"] = as_email(payload.get("contact_email"), "contact_email")
    if "country" in payload or not partial:
        out["country"] = as_text(payload.get("country", "India"), "country", max_len=60)
    if "promised_lead_time_days" in payload or not partial:
        out["promised_lead_time_days"] = as_int(
            payload.get("promised_lead_time_days"), "promised_lead_time_days",
            minimum=1, maximum=180)
    if "is_active" in payload or not partial:
        out["is_active"] = 1 if as_bool(payload.get("is_active", True), "is_active") else 0
    return out


PO_FIELDS = ("po_number", "supplier_id", "product_id", "quantity", "order_date",
             "expected_date", "status", "received_date", "received_qty", "notes")


def validate_po(payload: dict, *, partial=False) -> dict:
    reject_unknown_fields(payload, PO_FIELDS)
    require_fields(payload, ("supplier_id", "product_id", "quantity",
                             "order_date", "expected_date"), partial=partial)
    out: dict = {}
    if "po_number" in payload:
        out["po_number"] = as_text(payload.get("po_number"), "po_number", max_len=24)
    if "supplier_id" in payload or not partial:
        out["supplier_id"] = as_int(payload.get("supplier_id"), "supplier_id", minimum=1)
    if "product_id" in payload or not partial:
        out["product_id"] = as_int(payload.get("product_id"), "product_id", minimum=1)
    if "quantity" in payload or not partial:
        out["quantity"] = as_int(payload.get("quantity"), "quantity", minimum=1,
                                 maximum=10_000_000)
    if "order_date" in payload or not partial:
        out["order_date"] = as_date(payload.get("order_date"), "order_date")
    if "expected_date" in payload or not partial:
        out["expected_date"] = as_date(payload.get("expected_date"), "expected_date")
    if "received_date" in payload or not partial:
        out["received_date"] = as_date(payload.get("received_date"), "received_date",
                                       required=False)
    if "received_qty" in payload or not partial:
        out["received_qty"] = as_int(payload.get("received_qty"), "received_qty",
                                     minimum=0, required=False)
    if "status" in payload or not partial:
        out["status"] = as_enum(payload.get("status", "OPEN"), "status", PO_STATUSES)
    if "notes" in payload or not partial:
        out["notes"] = as_text(payload.get("notes"), "notes", max_len=255,
                               required=False, allow_none=True)

    # cross-field business rules
    details = []
    if out.get("order_date") and out.get("expected_date") \
            and out["expected_date"] < out["order_date"]:
        details.append({"field": "expected_date",
                        "message": "expected_date cannot be before order_date."})
    if out.get("order_date") and out.get("received_date") \
            and out["received_date"] < out["order_date"]:
        details.append({"field": "received_date",
                        "message": "received_date cannot be before order_date."})
    if out.get("received_qty") is not None and out.get("quantity") is not None \
            and out["received_qty"] > out["quantity"]:
        details.append({"field": "received_qty",
                        "message": "received_qty cannot exceed quantity."})
    status = out.get("status")
    if status == "RECEIVED" and not out.get("received_date"):
        details.append({"field": "received_date",
                        "message": "A RECEIVED purchase order needs received_date."})
    if status in ("OPEN", "CANCELLED") and out.get("received_date"):
        details.append({"field": "received_date",
                        "message": f"A {status} purchase order cannot have received_date."})
    if details:
        raise ValidationError(details)
    return out


STOCK_FIELDS = ("product_id", "transaction_date", "transaction_type", "quantity",
                "reference", "notes")


def validate_stock_txn(payload: dict) -> dict:
    reject_unknown_fields(payload, STOCK_FIELDS)
    require_fields(payload, ("product_id", "transaction_date", "transaction_type", "quantity"))
    txn = {
        "product_id": as_int(payload.get("product_id"), "product_id", minimum=1),
        "transaction_date": as_date(payload.get("transaction_date"), "transaction_date"),
        "transaction_type": as_enum(payload.get("transaction_type"),
                                    "transaction_type", STOCK_TXN_TYPES),
        "quantity": as_int(payload.get("quantity"), "quantity", minimum=-10_000_000,
                           maximum=10_000_000),
        "reference": as_text(payload.get("reference"), "reference", max_len=64,
                             required=False, allow_none=True),
        "notes": as_text(payload.get("notes"), "notes", max_len=255,
                         required=False, allow_none=True),
    }
    details = []
    if txn["quantity"] == 0:
        details.append({"field": "quantity", "message": "quantity must not be zero."})
    elif txn["transaction_type"] in POSITIVE_TXN_TYPES and txn["quantity"] < 0:
        details.append({"field": "quantity",
                        "message": f"{txn['transaction_type']} must be a positive quantity."})
    elif txn["transaction_type"] == "ISSUE" and txn["quantity"] > 0:
        details.append({"field": "quantity",
                        "message": "ISSUE must be a negative quantity (stock out)."})
    if details:
        raise ValidationError(details)
    return txn


DEMAND_FIELDS = ("product_id", "demand_date", "quantity")


def validate_demand(payload: dict) -> dict:
    reject_unknown_fields(payload, DEMAND_FIELDS)
    require_fields(payload, ("product_id", "demand_date", "quantity"))
    return {
        "product_id": as_int(payload.get("product_id"), "product_id", minimum=1),
        "demand_date": as_date(payload.get("demand_date"), "demand_date"),
        "quantity": as_int(payload.get("quantity"), "quantity", minimum=0,
                           maximum=1_000_000),
    }


# -------------------------------------------------------------- analytics
class AnalysisParams:
    """Validated, documented analysis parameters (see docs/04)."""

    def __init__(self, window_days=None, service_level=None, safety_stock_days=None,
                 safety_stock_policy=None, review_period_days=None,
                 excess_cover_days=None, forecast_horizon_days=None, product_id=None):
        self.window_days = window_days
        self.service_level = service_level
        self.safety_stock_days = safety_stock_days
        self.safety_stock_policy = safety_stock_policy
        self.review_period_days = review_period_days
        self.excess_cover_days = excess_cover_days
        self.forecast_horizon_days = forecast_horizon_days
        self.product_id = product_id

    def as_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "service_level": self.service_level,
            "safety_stock_days": self.safety_stock_days,
            "safety_stock_policy": self.safety_stock_policy,
            "review_period_days": self.review_period_days,
            "excess_cover_days": self.excess_cover_days,
            "forecast_horizon_days": self.forecast_horizon_days,
        }


def analysis_params(defaults) -> AnalysisParams:
    a = request.args
    policy = (a.get("safety_stock_policy") or defaults.SAFETY_STOCK_POLICY).strip().lower()
    if policy not in ("zscore", "days", "max"):
        _fail("safety_stock_policy", "Must be one of: zscore, days, max.", policy)
    return AnalysisParams(
        window_days=as_int(a.get("window_days", defaults.WINDOW_DAYS), "window_days",
                           minimum=14, maximum=730),
        service_level=float(as_decimal(a.get("service_level", defaults.SERVICE_LEVEL),
                                       "service_level", minimum=0.5, maximum=0.999)),
        safety_stock_days=float(as_decimal(a.get("safety_stock_days",
                                                 defaults.SAFETY_STOCK_DAYS),
                                           "safety_stock_days", minimum=0, maximum=90)),
        safety_stock_policy=policy,
        review_period_days=float(as_decimal(a.get("review_period_days",
                                                   defaults.REVIEW_PERIOD_DAYS),
                                             "review_period_days", minimum=0, maximum=90)),
        excess_cover_days=float(as_decimal(a.get("excess_cover_days",
                                                 defaults.EXCESS_COVER_DAYS),
                                           "excess_cover_days", minimum=7, maximum=730)),
        forecast_horizon_days=as_int(a.get("horizon", defaults.FORECAST_HORIZON_DAYS),
                                     "horizon", minimum=1, maximum=180),
        product_id=as_int(a.get("product_id"), "product_id", minimum=1, required=False),
    )


def clean_number(value, digits=3):
    """Convert Decimal / numpy scalars to JSON-friendly floats."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), digits)
