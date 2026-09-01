import json
import logging
import uuid
from datetime import UTC, datetime
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SERVICE_NAME = "voice-assistance-backend"
REQUEST_ID_HEADER = "X-Request-ID"


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for application and request logs."""

    _STANDARD_FIELDS = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "msg", "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "thread", "threadName", "message", "event", "service",
        "timestamp", "request_id", "method", "path", "status_code", "duration_ms"
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        for field_name in ("method", "path", "status_code", "duration_ms"):
            field_value = getattr(record, field_name, None)
            if field_value is not None:
                payload[field_name] = field_value
                
        # Include all custom extra fields
        for key, val in record.__dict__.items():
            if key not in self._STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = val
                
        if record.exc_info:
            payload["error"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Error",
                "message": str(record.exc_info[1]),
            }
        return json.dumps(payload, separators=(",", ":"), default=str)



def configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    json_handlers = [
        handler for handler in root_logger.handlers if isinstance(handler.formatter, JsonFormatter)
    ]
    if not json_handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root_logger.addHandler(handler)

    logging.getLogger("uvicorn.access").disabled = True


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        started = perf_counter()
        logger = logging.getLogger(SERVICE_NAME)
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "event": "request.failed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise

        duration_ms = round((perf_counter() - started) * 1000, 3)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request completed",
            extra={
                "event": "request.completed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
