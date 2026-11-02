"""Uniform JSON error envelope for every API failure.

Response shape
--------------
{
  "error": {
      "code": "VALIDATION_ERROR",
      "message": "Human readable message",
      "details": [ {...}, ... ]      # optional
  }
}
"""
from __future__ import annotations

import logging

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from db import DatabaseError

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, message: str, *, status: int = 400, code: str = "BAD_REQUEST",
                 details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details or []


class ValidationError(ApiError):
    def __init__(self, details, message="Request validation failed."):
        super().__init__(message, status=422, code="VALIDATION_ERROR", details=details)


class NotFoundError(ApiError):
    def __init__(self, message="Resource not found."):
        super().__init__(message, status=404, code="NOT_FOUND")


class ConflictError(ApiError):
    def __init__(self, message, details=None, code="CONFLICT"):
        super().__init__(message, status=409, code=code, details=details)


def error_response(status: int, code: str, message: str, details=None):
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return jsonify(body), status


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.errorhandler(DatabaseError)
    def _db_error(exc: DatabaseError):
        if exc.unavailable:
            log.error("Database unavailable: %s", exc)
            return error_response(
                503, "DATABASE_UNAVAILABLE",
                "The database is not reachable. Verify the MySQL server and .env credentials.")
        if exc.is_duplicate:
            return error_response(409, "DUPLICATE_RECORD", exc.message)
        if exc.is_referenced:
            return error_response(409, "RECORD_IN_USE", exc.message)
        if exc.is_constraint:
            return error_response(422, "CONSTRAINT_VIOLATION", exc.message)
        log.exception("Unexpected database error")
        return error_response(500, "DATABASE_ERROR", exc.message or "Database error.")

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        return error_response(exc.code or 500, exc.name.upper().replace(" ", "_"),
                              exc.description or "HTTP error")

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        log.exception("Unhandled server error")
        return error_response(500, "INTERNAL_ERROR",
                              "An unexpected server error occurred.")
