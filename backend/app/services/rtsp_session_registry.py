from app.services.rtsp_session import RtspCaptureSession

#: In-memory registry of live capture sessions, keyed by processing_run
#: id. Session objects hold live threads/sockets, so they cannot be
#: DB-persisted the way everything else in this app is - this registry
#: is inherently process-local, consistent with the local-first,
#: single-user design (see docs/HANDOFF.md Phase 9 known issues for
#: what that trades away: sessions don't survive a backend restart).
_sessions: dict[str, RtspCaptureSession] = {}


def register_session(run_id: str, session: RtspCaptureSession) -> None:
    _sessions[run_id] = session


def get_session(run_id: str) -> RtspCaptureSession | None:
    return _sessions.get(run_id)


def remove_session(run_id: str) -> None:
    _sessions.pop(run_id, None)
