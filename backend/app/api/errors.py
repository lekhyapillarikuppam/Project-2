"""API errors in the SPEC §9 shape: {"error": {"code": ..., "message": ...}}, with the §9 status."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.pipeline.errors import ERROR_MESSAGES

HTTP_STATUS: dict[str, int] = {
    "UNSUPPORTED_TYPE": 415,
    "LEGACY_DOC": 415,
    "FILE_TOO_LARGE": 413,
    "TOO_MANY_PAGES": 422,
    "CORRUPT_FILE": 422,
    "ENCRYPTED_FILE": 422,
    "SCANNED_PDF": 422,
    "MISSING_FILE": 400,
    "SERVER_BUSY": 503,
    "JOB_NOT_FOUND": 404,
    "CHAPTER_NOT_FOUND": 404,
    "NOT_FOUND": 404,
    "JOB_EXPIRED": 410,
    "CHAPTER_NOT_READY": 409,
    "BAD_REQUEST": 400,
}

# Codes the API needs that are not user-facing pipeline errors in SPEC §12.
EXTRA_MESSAGES: dict[str, str] = {
    "MISSING_FILE": "No file was uploaded. Choose a PDF or DOCX file.",
    "CHAPTER_NOT_FOUND": "This chapter doesn't exist.",
    "NOT_FOUND": "Not found.",
    "BAD_REQUEST": "The request was not valid.",
}
FULL_BOOK_NOT_READY_MESSAGE = "The full book is available once every chapter has finished."


class ApiError(Exception):
    def __init__(self, code: str, message: str | None = None, status: int | None = None) -> None:
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code) or EXTRA_MESSAGES.get(code, code)
        self.status = status or HTTP_STATUS.get(code, 400)
        super().__init__(f"{self.code}: {self.message}")


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "BAD_REQUEST"
        message = EXTRA_MESSAGES[code] if exc.status_code == 404 else str(exc.detail)
        return error_response(code, message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response("BAD_REQUEST", EXTRA_MESSAGES["BAD_REQUEST"], 400)
