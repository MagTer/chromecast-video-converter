import logging

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..dependencies import JOB_HISTORY_STORE

LOGGER = logging.getLogger("orchestrator.history")
router = APIRouter()


@router.get("/api/history")
async def list_job_history(limit: int = 100) -> JSONResponse:
    """Retrieve recent job history."""
    history = JOB_HISTORY_STORE.list_recent(limit=limit)
    return JSONResponse(
        jsonable_encoder(
            [
                {
                    "id": entry.job_id,
                    "job_id": entry.job_id,  # duplicate for compatibility if needed
                    "path": entry.path,
                    "library": entry.library,
                    "profile": entry.profile,
                    "status": entry.status,
                    "message": entry.message,
                    # map started_at to created_at for frontend sort
                    "created_at": entry.started_at,
                    "updated_at": entry.completed_at,
                    "elapsed_seconds": (
                        (entry.completed_at - entry.started_at).total_seconds()
                        if entry.completed_at and entry.started_at
                        else 0
                    ),
                }
                for entry in history
            ]
        )
    )
