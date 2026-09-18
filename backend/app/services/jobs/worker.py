"""The worker process: ``python -m app.services.jobs.worker <job_id>``.

Started detached by ``runner.launch_worker_process``, so it has no
console and no parent to report to. Everything it wants to say goes to
the job row, the progress file, or its log file.

It takes only a job id on the command line. Everything else it needs is
in the row, which is what makes a job restartable and inspectable rather
than a closure that died with the process that made it.
"""

import logging
import sys
import traceback

from app.core.logging import configure_logging
from app.db.models.job import Job
from app.db.session import SessionLocal
from app.services.jobs import runner
from app.services.jobs.handlers import HANDLERS
from app.services.jobs.progress import write_progress

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_INVOCATION = 2


def run_job(job_id: str) -> int:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            logger.error("No such job: %s", job_id)
            return EXIT_BAD_INVOCATION

        if job.is_terminal:
            # Most likely a cancel that landed between launch and start.
            logger.info("Job %s is already %s - nothing to do", job_id, job.status)
            return EXIT_OK

        handler = HANDLERS.get(job.type)
        if handler is None:
            runner.mark_failed(db, job_id, f"No handler for job type {job.type!r}")
            return EXIT_BAD_INVOCATION

        params = dict(job.params_json or {})
        progress_file = runner.progress_path(job_id)

        def report(fraction: float, message: str | None = None) -> None:
            write_progress(progress_file, fraction=fraction, message=message)

        runner.mark_running(db, job_id)
        report(0.0, "Starting")

        try:
            result = handler(db, params, report)
        except BaseException as exc:
            # Includes KeyboardInterrupt, which is how a cancel arrives on
            # Windows - recording it as failed here is harmless, because
            # cancellation overwrites the status from the app side.
            logger.exception("Job %s failed", job_id)
            runner.mark_failed(db, job_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            return EXIT_FAILED

        report(1.0, "Done")
        runner.mark_succeeded(db, job_id, result=result)
        return EXIT_OK


def main(argv: list[str]) -> int:
    configure_logging()
    if len(argv) != 2:
        print(f"usage: python -m {__spec__.name} <job_id>", file=sys.stderr)
        return EXIT_BAD_INVOCATION
    return run_job(argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
