import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for actionable, user-facing application errors.

    Every AppError carries a machine-readable ``code`` and a
    human-actionable ``message`` so API consumers never have to guess
    what went wrong or what to do next.
    """

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("app_error path=%s code=%s message=%s", request.url.path, exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(OperationalError)
    async def handle_database_error(request: Request, exc: OperationalError) -> JSONResponse:
        # A full disk is the one database failure a user can act on, and
        # "An unexpected error occurred" hid it behind every panel at once.
        if "disk is full" in str(exc.orig if exc.orig is not None else exc).lower():
            logger.error("disk_full path=%s", request.url.path)
            return JSONResponse(
                status_code=507,
                content={
                    "code": "disk_full",
                    "message": "The server's disk is full, so nothing can be saved. Free some space "
                    "(Dataset > Storage, or old Docker images on the server), then try again.",
                },
            )
        logger.exception("unhandled_error path=%s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"code": "internal_error", "message": "An unexpected error occurred."},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error path=%s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"code": "internal_error", "message": "An unexpected error occurred."},
        )
