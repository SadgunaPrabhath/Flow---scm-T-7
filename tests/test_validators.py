"""Request-validation tests: valid, invalid, boundary and type-confusion input."""
from __future__ import annotations

from datetime import date

import pytest

from api.errors import ApiError, ValidationError
from api.validators import (as_bool, as_date, as_decimal, as_email, as_int, as_sku,
                            as_text, validate_demand, validate_po, validate_product,
                            validate_stock_txn, validate_supplier)


class TestScalars:
    def test_as_int_ok(self):
        assert as_int("42", "x") == 42
        assert as_int(0, "x", minimum=0) == 0

    @pytest.mark.parametrize("bad", ["abc", "", None, 1.5, True, [1]])
    def test_as_int_invalid(self, bad):
        with pytest.raises(ValidationError):
            as_int(bad, "x")

    def test_as_int_boundaries(self):
        assert as_int(1, "x", minimum=1, maximum=10) == 1
        assert as_int(10, "x", minimum=1, maximum=10) == 10
        with pytest.raises(ValidationError):
            as_int(11, "x", minimum=1, maximum=10)
        with pytest.raises(ValidationError):
            as_int(0, "x", minimum=1, maximum=10)

    def test_as_decimal(self):
        assert float(as_decimal("1249.50", "cost")) == 1249.5
        with pytest.raises(ValidationError):
            as_decimal("NaN", "cost")
        with pytest.raises(ValidationError):
            as_decimal(0, "cost", minimum=0.01)

    def test_as_date(self):
        assert as_date("2026-10-19", "d") == date(2026, 10, 19)
        for bad in ("19-10-2026", "2026-13-01", "yesterday", "2026-02-30"):
            with pytest.raises(ValidationError):
                as_date(bad, "d")

    def test_as_bool(self):
        assert as_bool("true", "b") is True
        assert as_bool("0", "b") is False
        with pytest.raises(ValidationError):
            as_bool("maybe", "b")

    def test_as_email(self):
        assert as_email("buyer@example.com", "e") == "buyer@example.com"
        for bad in ("buyer", "buyer@", "@example.com", "buyer@example"):
            with pytest.raises(ValidationError):
                as_email(bad, "e")

    def test_as_sku(self):
        assert as_sku("AUTO-BRK-0012") == "AUTO-BRK-0012"
        assert as_sku("auto-brk-0012") == "AUTO-BRK-0012"   # normalised to upper-case
        for bad in ("a", "sku with space", "!", "x" * 33):
            with pytest.raises(ValidationError):
                as_sku(bad, "sku")

    def test_as_text_length_limits(self):
        assert as_text("abc", "t", max_len=3) == "abc"
        with pytest.raises(ValidationError):
            as_text("abcd", "t", max_len=3)
        with pytest.raises(ValidationError):
            as_text("   ", "t")


class TestProductValidation:
    def valid(self, **overrides):
        payload = {"sku": "TEST-001", "product_name": "Test item", "category": "Testing",
                   "unit_cost": "99.99", "lead_time_days": 7, "supplier_id": 1}
        payload.update(overrides)
        return payload

    def test_valid_payload(self):
        out = validate_product(self.valid())
        assert out["sku"] == "TEST-001"
        assert out["is_active"] == 1
        assert out["reorder_point"] == 0

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError) as exc:
            validate_product({"sku": "TEST-001"})
        fields = {d["field"] for d in exc.value.details}
        assert {"product_name", "unit_cost", "lead_time_days", "supplier_id"} <= fields

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            validate_product(self.valid(hacker="x"))
        assert exc.value.details[0]["field"] == "hacker"

    def test_supplier_id_boundary(self):
        with pytest.raises(ValidationError):
            validate_product(self.valid(supplier_id=0))

    def test_lead_time_upper_boundary(self):
        validate_product(self.valid(lead_time_days=180))
        with pytest.raises(ValidationError):
            validate_product(self.valid(lead_time_days=181))

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            validate_product(self.valid(unit_cost="-1"))

    def test_partial_update_allows_subset(self):
        out = validate_product({"category": "Braking"}, partial=True)
        assert out == {"category": "Braking"}


class TestSupplierValidation:
    def test_valid(self):
        out = validate_supplier({"supplier_name": "ACME", "promised_lead_time_days": 21})
        assert out["country"] == "India"
        assert out["is_active"] == 1

    def test_missing_name(self):
        with pytest.raises(ValidationError):
            validate_supplier({"promised_lead_time_days": 21})

    def test_lead_time_out_of_range(self):
        with pytest.raises(ValidationError):
            validate_supplier({"supplier_name": "X", "promised_lead_time_days": 0})


class TestPurchaseOrderValidation:
    def valid(self, **overrides):
        payload = {"supplier_id": 1, "product_id": 1, "quantity": 100,
                   "order_date": "2026-10-01", "expected_date": "2026-10-11"}
        payload.update(overrides)
        return payload

    def test_valid(self):
        out = validate_po(self.valid())
        assert out["status"] == "OPEN"

    def test_expected_before_order_rejected(self):
        with pytest.raises(ValidationError) as exc:
            validate_po(self.valid(expected_date="2026-09-30"))
        assert exc.value.details[0]["field"] == "expected_date"

    def test_received_status_requires_date(self):
        with pytest.raises(ValidationError):
            validate_po(self.valid(status="RECEIVED"))

    def test_open_status_cannot_have_received_date(self):
        with pytest.raises(ValidationError):
            validate_po(self.valid(status="OPEN", received_date="2026-10-10"))

    def test_received_qty_cannot_exceed_quantity(self):
        with pytest.raises(ValidationError):
            validate_po(self.valid(status="RECEIVED", received_date="2026-10-11",
                                   received_qty=101))

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            validate_po(self.valid(status="SHIPPED"))

    def test_zero_quantity_rejected(self):
        with pytest.raises(ValidationError):
            validate_po(self.valid(quantity=0))


class TestStockTransactionValidation:
    def test_issue_must_be_negative(self):
        txn = validate_stock_txn({"product_id": 1, "transaction_date": "2026-10-19",
                                  "transaction_type": "ISSUE", "quantity": -5})
        assert txn["quantity"] == -5
        with pytest.raises(ValidationError):
            validate_stock_txn({"product_id": 1, "transaction_date": "2026-10-19",
                                "transaction_type": "ISSUE", "quantity": 5})

    def test_receipt_must_be_positive(self):
        with pytest.raises(ValidationError):
            validate_stock_txn({"product_id": 1, "transaction_date": "2026-10-19",
                                "transaction_type": "RECEIPT", "quantity": -5})

    def test_zero_quantity_rejected(self):
        with pytest.raises(ValidationError):
            validate_stock_txn({"product_id": 1, "transaction_date": "2026-10-19",
                                "transaction_type": "ADJUSTMENT", "quantity": 0})

    def test_invalid_type(self):
        with pytest.raises(ValidationError):
            validate_stock_txn({"product_id": 1, "transaction_date": "2026-10-19",
                                "transaction_type": "SALE", "quantity": -1})


class TestDemandValidation:
    def test_valid(self):
        out = validate_demand({"product_id": 1, "demand_date": "2026-10-19",
                               "quantity": 0})
        assert out["quantity"] == 0

    def test_negative_demand_rejected(self):
        with pytest.raises(ValidationError):
            validate_demand({"product_id": 1, "demand_date": "2026-10-19",
                             "quantity": -1})


def test_validation_error_carries_field_details():
    with pytest.raises(ApiError) as exc:
        validate_product({})
    assert exc.value.status == 422
    assert all("field" in d and "message" in d for d in exc.value.details)
