from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Sonde de vivacité : ne dépend d'aucune ressource externe."""
    return HealthResponse(status="ok", version=__version__)
