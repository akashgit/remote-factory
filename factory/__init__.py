"""Remote Factory — domain-agnostic multi-agent software evolution loop."""

import logging
import os
import sys

import structlog

# Without a filtering logger every `log.debug` renders exactly like `log.info`, so the distinction
# the code makes is invisible and a routine command buries its own output in internal event names.
# INFO by default; `FACTORY_LOG_LEVEL=debug` opts back in to the detail.
_LEVELS = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}
_level = _LEVELS.get(os.environ.get("FACTORY_LOG_LEVEL", "").strip().lower(), logging.INFO)

structlog.configure(
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    wrapper_class=structlog.make_filtering_bound_logger(_level),
)
