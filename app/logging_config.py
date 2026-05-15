from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


class JSONFormatter(logging.Formatter):
    """Выводит каждую запись лога в виде однострочного JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # correlation_id берётся из ContextVar (устанавливается middleware),
        # либо из extra, если передан явно.
        cid: str | None = getattr(record, "correlation_id", None) or correlation_id_var.get()
        if cid is not None:
            entry["correlation_id"] = cid

        # Любые дополнительные поля из extra (например, user_id, endpoint и пр.)
        _skip = {
            "message", "asctime", "levelname", "name", "msg", "args",
            "created", "filename", "funcName", "levelno", "lineno",
            "module", "msecs", "pathname", "process", "processName",
            "relativeCreated", "stack_info", "thread", "threadName",
            "exc_info", "exc_text", "correlation_id",
        }
        for key, value in record.__dict__.items():
            if key not in _skip:
                entry[key] = value

        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Заглушаем шумные логгеры SQLAlchemy и uvicorn.access
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
