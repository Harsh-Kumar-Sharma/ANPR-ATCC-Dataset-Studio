from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.active_learning import router as active_learning_router
from app.api.datasets import datasets_router, project_datasets_router
from app.api.evaluation import router as evaluation_router
from app.api.health import router as health_router
from app.api.processing_profiles import router as processing_profiles_router
from app.api.projects import router as projects_router
from app.api.rtsp import router as rtsp_router
from app.api.rtsp import run_router as rtsp_run_router
from app.api.sources import router as sources_router
from app.api.tracks import project_tracks_router, tracks_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(title=get_settings().app_name)

# Local-first desktop app only ever talks to this backend on
# localhost - permissive CORS is fine here (not a multi-tenant
# service exposed to the internet).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(health_router)
app.include_router(projects_router)
app.include_router(sources_router)
app.include_router(processing_profiles_router)
app.include_router(project_tracks_router)
app.include_router(tracks_router)
app.include_router(project_datasets_router)
app.include_router(datasets_router)
app.include_router(evaluation_router)
app.include_router(active_learning_router)
app.include_router(rtsp_router)
app.include_router(rtsp_run_router)
