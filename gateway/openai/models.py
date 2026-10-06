"""GET /v1/models — list the single gateway model id."""

from __future__ import annotations

from fastapi import APIRouter

from gateway.openai.streaming import MODEL_ID, MODEL_OWNER

router = APIRouter()

# Fixed creation timestamp for the model listing (seconds since epoch).
MODEL_CREATED = 1759276800  # 2025-10-01T00:00:00Z


@router.get("/v1/models")
def list_models() -> dict:
    """Return the OpenAI model list with the single ``hermes-search`` model."""
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "owned_by": MODEL_OWNER,
                "created": MODEL_CREATED,
            }
        ],
    }
