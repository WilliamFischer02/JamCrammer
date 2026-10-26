"""Structured logging: rotating JSONL + rotating human-readable log + console.

Also bridges the event bus into the log so every published event is persisted.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from jamcrammer.core.events import Event, EventBus

LOGGER_NAME = "jamcrammer"
JSONL_NAME = "jamcrammer.jsonl"
HUMAN_NAME = "jamcrammer.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
HUMAN_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_CONFIGURED_FLAG = "_jamcrammer_configured"


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "event", None)
        if isinstance(extra, dict):
            doc["event"] = extra
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False, default=str)


def setup_logging(log_dir: Path, console_level: int = logging.INFO) -> logging.Logger:
    """Configure the ``jamcrammer`` logger once. Safe to call repeatedly."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, _CONFIGURED_FLAG, False):
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    jsonl = logging.handlers.RotatingFileHandler(
        log_dir / JSONL_NAME, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    jsonl.setFormatter(JsonlFormatter())
    jsonl.setLevel(logging.DEBUG)

    human = logging.handlers.RotatingFileHandler(
        log_dir / HUMAN_NAME, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    human.setFormatter(logging.Formatter(HUMAN_FORMAT))
    human.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(HUMAN_FORMAT))
    console.setLevel(console_level)

    for handler in (jsonl, human, console):
        logger.addHandler(handler)
    setattr(logger, _CONFIGURED_FLAG, True)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def attach_bus(bus: EventBus, logger: logging.Logger | None = None) -> None:
    """Persist every event published on ``bus`` as a log record with an ``event`` extra."""
    log = logger or logging.getLogger(LOGGER_NAME)

    def on_event(event: Event) -> None:
        log.info(
            "%s [%s] %s %s",
            event.type,
            event.level,
            event.source,
            event.payload if event.payload else "",
            extra={
                "event": {
                    "type": event.type,
                    "source": event.source,
                    "level": str(event.level),
                    "payload": event.payload,
                    "screenshot_path": (
                        str(event.screenshot_path) if event.screenshot_path else None
                    ),
                    "ts": event.ts,
                }
            },
        )

    bus.subscribe("*", on_event)
