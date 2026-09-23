"""Erreurs applicatives au format Problem Details (RFC 9457).

Chaque erreur porte un ``code`` stable, traduit par le frontend (i18n)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    title = "Requête invalide"

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        if code:
            self.code = code
        self.extra = extra or {}


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    title = "Authentification requise"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    title = "Accès refusé"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    title = "Ressource introuvable"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    title = "Conflit"


class BusinessRuleError(AppError):
    status_code = 422
    code = "business_rule_violation"
    title = "Règle métier non respectée"


def _problem(
    status: int, code: str, title: str, detail: str, extra: dict[str, Any] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    if extra:
        body.update(extra)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _problem(exc.status_code, exc.code, exc.title, exc.detail, exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return _problem(
            422, "validation_error", "Données invalides", "Données invalides", {"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(exc.status_code, "http_error", str(exc.detail), str(exc.detail))
