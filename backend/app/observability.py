import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_context = ContextVar("request_id", default=None)
_PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)\S+")


def redact_log_value(value: str) -> str:
    value = _BEARER_PATTERN.sub(r"\1[REDACTED]", value)
    return _PHONE_PATTERN.sub("[REDACTED_PHONE]", value)


class JsonLogFormatter(logging.Formatter):
    SAFE_FIELDS = (
        "event",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "dispatch_job_id",
        "call_attempt_id",
        "provider",
        "outcome",
        "count",
        "queue_depth",
        "role",
        "stage",
        "status",
        "reason",
        "control_event",
        "audio_format",
        "audio_bytes",
        "audio_frames",
        "audio_duration_ms",
        "text_length",
        "connection_code",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_log_value(record.getMessage()),
            "request_id": (
                getattr(record, "request_id", None)
                or request_id_context.get()
            ),
        }
        for field in self.SAFE_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = redact_log_value(
                self.formatException(record.exc_info)
            )
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging():
    root_logger = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root_logger.handlers[:] = [handler]
    root_logger.setLevel(logging.INFO)
