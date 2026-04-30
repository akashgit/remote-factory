"""Remote Factory — domain-agnostic multi-agent software evolution loop."""

import logging as _logging
import os
import sys

import structlog

_log_level: int = getattr(
    _logging, os.environ.get("FACTORY_LOG_LEVEL", "INFO"), _logging.INFO
)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_log_level),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    cache_logger_on_first_use=True,
)
