"""Domain exceptions + the handlers that turn them into safe HTTP responses.

The rule this module enforces: nothing but these types (or FastAPI's own
``RequestValidationError``) is allowed to shape a response body. An
unexpected exception is logged with a correlation id and the client gets a
generic 500 — never a stack trace, a SQL fragment, or an internal file path.
"""
import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("ecclesia_flow")


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None):
        self.message = message
        if code:
            self.code = code
        super().__init__(message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class StepUpRequiredError(ForbiddenError):
    code = "step_up_required"


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


def _error_body(code: str, message: str, request_id: str) -> dict:
    return {"error": {"code": code, "message": message, "requestId": request_id}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        # Field-level detail is safe to return — it describes the client's
        # own payload shape, not server internals. jsonable_encoder is
        # required, not optional, here: a custom validator's `ctx` can hold
        # the raw ValueError it raised (e.g. the password-strength check),
        # which plain json.dumps cannot serialize — that turned every
        # rejected weak password into a 500 instead of the intended 422.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("validation_error", "The request payload is invalid.", request_id)
            | {"fields": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.error("db_error request_id=%s", request_id, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "Something went wrong on our end.", request_id),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.error("unhandled_error request_id=%s", request_id, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "Something went wrong on our end.", request_id),
        )
