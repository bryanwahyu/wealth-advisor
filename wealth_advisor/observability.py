"""Structured logging.

Every record carries the run id and the active agent (via contextvars), so a
single run can be reconstructed from the JSONL file with `grep run_id`.
Console output stays human-readable; the file gets one JSON object per line.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")
agent_var: contextvars.ContextVar[str] = contextvars.ContextVar("agent", default="system")

LOGGER_NAME = "wealth_advisor"


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        record.agent = agent_var.get()
        if not hasattr(record, "event"):
            record.event = "log"
        if not hasattr(record, "data"):
            record.data = {}
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "run_id": record.run_id,
            "agent": record.agent,
            "event": record.event,
            "msg": record.getMessage(),
        }
        if record.data:
            payload["data"] = record.data
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        line = f"{ts} {record.levelname:<7} [{record.agent:<12}] {record.event:<22} {record.getMessage()}"
        if record.exc_info and record.levelno >= logging.ERROR:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(log_dir: Path | None, run_id: str, verbose: bool = False) -> Path | None:
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(ConsoleFormatter())
    console.addFilter(_ContextFilter())
    logger.addHandler(console)

    log_path = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"run-{run_id}.jsonl"
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JsonFormatter())
        fh.addFilter(_ContextFilter())
        logger.addHandler(fh)
    return log_path


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not any(isinstance(f, _ContextFilter) for f in logger.filters):
        logger.addFilter(_ContextFilter())
    return logger


def log_event(event: str, msg: str, level: int = logging.INFO, **data: Any) -> None:
    get_logger().log(level, msg, extra={"event": event, "data": data})


@contextmanager
def agent_scope(name: str) -> Iterator[None]:
    token = agent_var.set(name)
    try:
        yield
    finally:
        agent_var.reset(token)


@contextmanager
def timed(event: str, msg: str, **data: Any) -> Iterator[dict[str, Any]]:
    """Logs start/end of a block with its duration. Callers can add fields to
    the yielded dict and they end up on the completion record."""
    extra: dict[str, Any] = {}
    start = time.perf_counter()
    log_event(f"{event}.start", msg, level=logging.DEBUG, **data)
    try:
        yield extra
    except Exception:
        duration = round((time.perf_counter() - start) * 1000, 1)
        log_event(f"{event}.failed", msg, level=logging.WARNING, duration_ms=duration, **data, **extra)
        raise
    duration = round((time.perf_counter() - start) * 1000, 1)
    log_event(f"{event}.done", msg, duration_ms=duration, **data, **extra)
