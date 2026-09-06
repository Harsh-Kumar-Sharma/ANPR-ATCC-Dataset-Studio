from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.processing_profiles import router as processing_profiles_router
from app.api.projects import router as projects_router
from app.api.sources import router as sources_router
from app.api.tracks import project_tracks_router, tracks_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(title=get_settings().app_name)

register_exception_handlers(app)
app.include_router(health_router)
app.include_router(projects_router)
app.include_router(sources_router)
app.include_router(processing_profiles_router)
app.include_router(project_tracks_router)
app.include_router(tracks_router)
