import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.active_learning import router as active_learning_router
from app.api.classes import router as classes_router
from app.api.datasets import datasets_router, project_datasets_router
from app.api.evaluation import router as evaluation_router
from app.api.frames import frames_router, project_frames_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.playback import router as playback_router
from app.api.processing_profiles import router as processing_profiles_router
from app.api.projects import router as projects_router
from app.api.rtsp import router as rtsp_router
from app.api.rtsp import run_router as rtsp_run_router
from app.api.sources import router as sources_router
from app.api.sources import runs_router as processing_runs_router
from app.api.tracks import project_tracks_router, tracks_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.jobs.runner import reconcile_jobs

configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Reattach to background jobs on startup.

    Workers are detached, so a job that was running when the app closed
    is usually still running, and is left alone - that is the point of
    detaching them. This only cleans up jobs whose process is genuinely
    gone, which would otherwise claim to be in progress forever and
    block their source against any future run.
    """
    try:
        with SessionLocal() as db:
            reconciled = reconcile_jobs(db)
        if reconciled:
            logger.warning("Marked %d job(s) failed whose processes are no longer running", len(reconciled))
    except Exception:
        # A failure here must not stop the app opening - the user needs
        # it far more than they need tidy job rows.
        logger.exception("Could not reconcile jobs on startup")

    yield


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)

# Local-first desktop app only ever talks to this backend on
# localhost - permissive CORS is fine here (not a multi-tenant
# service exposed to the internet).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    # The renderer runs on a different origin (Vite dev server / file://),
    # and browsers hide non-standard response headers cross-origin unless
    # they are listed here - the live preview needs this one to skip
    # frames it has already shown.
    expose_headers=["X-Frame-Sequence"],
)

register_exception_handlers(app)


app.include_router(health_router)
app.include_router(projects_router)
app.include_router(jobs_router)
app.include_router(classes_router)
app.include_router(project_frames_router)
app.include_router(frames_router)
app.include_router(sources_router)
app.include_router(processing_runs_router)
app.include_router(playback_router)
app.include_router(processing_profiles_router)
app.include_router(project_tracks_router)
app.include_router(tracks_router)
app.include_router(project_datasets_router)
app.include_router(datasets_router)
app.include_router(evaluation_router)
app.include_router(active_learning_router)
app.include_router(rtsp_router)
app.include_router(rtsp_run_router)
