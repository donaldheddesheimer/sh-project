"""Process logging: one JSON object per line, everywhere.

Cloud Run ships stdout/stderr to Cloud Logging. Plain text lands there with no severity, so ERROR and INFO look
alike; a JSON line with ``severity`` and ``message`` is parsed into a real log entry that can be filtered
(``severity>=WARNING``, ``jsonPayload.logger="app.learning.episode"``). The format is fixed in code, not chosen
from the environment.
"""

from __future__ import annotations

import json
import logging

# Cloud Logging severities; Python's CRITICAL is CRITICAL there too and the rest map by name.
_SEVERITY = {"WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL", "INFO": "INFO", "DEBUG": "DEBUG"}


class CloudJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": _SEVERITY.get(record.levelname, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            entry["message"] += "\n" + self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(CloudJsonFormatter())
    # force=True replaces uvicorn's pre-installed root handlers so every line goes through one formatter.
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Uvicorn installs its own named handlers before importing the app. Remove them so access
    # and server records propagate to the JSON root handler instead of bypassing it as text.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
