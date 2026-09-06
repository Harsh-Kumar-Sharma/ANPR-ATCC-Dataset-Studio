import logging
import sys

from app.core.config import get_settings


def configure_logging() -> None:
    """Configure root logging once at process startup.

    Formats include a request/context-friendly structure. Callers must
    never log credentials or secrets - only IDs and context.
    """
    settings = get_settings()
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
