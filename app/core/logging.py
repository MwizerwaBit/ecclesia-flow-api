"""Structured, scrubbed logging (DIF-11).

Every log line is one JSON object on stdout (the platform's log shipper
collects it). Before anything is written, values that must never reach logs
are redacted, wherever they appear in the message or its fields:

- bearer tokens, JWTs, refresh/reset/invite tokens (long URL-safe strings)
- anything under a sensitive key: password, token, secret, authorization,
  cookie, national_id, mfa/backup codes
- email addresses are reduced to their domain

Request bodies are never logged. Query strings are logged with their values
removed. Uvicorn's own access log is turned off in favour of
``RequestIdMiddleware``'s line, which carries the request id.
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime

_SENSITIVE_KEYS = re.compile(
    r"pass(word)?|token|secret|authori[sz]ation|cookie|national_id|mfa|backup_code|api_key|signature", re.I
)
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED_JWT]"),
    (re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])"), "[REDACTED_TOKEN]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"), r"***@\1"),
]
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def scrub(value):
    if isinstance(value, str):
        if _UUID.match(value):  # ids are fine and useful for correlation
            return value
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {k: "[REDACTED]" if _SENSITIVE_KEYS.search(str(k)) else scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    return value


_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(scrub(entry), default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
