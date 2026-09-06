from fastapi import APIRouter

from app.core.processing_profiles import DEFAULT_PROCESSING_PROFILES

router = APIRouter(prefix="/processing-profiles", tags=["processing-profiles"])


@router.get("")
def list_processing_profiles() -> dict[str, dict]:
    return DEFAULT_PROCESSING_PROFILES
