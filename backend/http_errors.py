"""Generic public HTTP errors that avoid reflecting request contents."""

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def sanitized_validation_error(request, exc: RequestValidationError) -> JSONResponse:
    """Return a generic validation message without input values or internals."""
    return JSONResponse(
        status_code=422,
        content={"detail": "Invalid request. Check supported fields and values."},
    )
