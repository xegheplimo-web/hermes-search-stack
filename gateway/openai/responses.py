"""POST /v1/responses — deferred endpoint (501 not_implemented)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/v1/responses")
def create_response() -> JSONResponse:
    """The Responses API is not implemented yet; return the OpenAI 501 shape."""
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "message": "The /v1/responses endpoint is not implemented yet.",
                "type": "not_implemented",
                "code": "not_implemented",
            }
        },
    )
