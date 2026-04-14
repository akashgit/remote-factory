"""Remote Factory — domain-agnostic multi-agent software evolution loop."""

import sys

import structlog

structlog.configure(
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)
