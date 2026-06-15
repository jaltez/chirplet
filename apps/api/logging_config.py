import logging
import sys

from apps.api.request_context import RequestIdFilter

FORMAT = "%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s"


def setup_logging(level: str) -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logging.getLogger("chirplet")
