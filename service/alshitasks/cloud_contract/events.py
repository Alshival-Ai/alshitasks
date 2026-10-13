"""Bounded input decoding and normalization shared by JSON and OTLP transports."""

import json
import zlib
from datetime import UTC, datetime
from typing import Any

from google.protobuf.json_format import MessageToDict, Parse, ParseError
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import APIError

MAX_BODY = 4 * 1024 * 1024
MAX_EVENT = 64 * 1024


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime | None = None
    severity: str = "INFO"
    message: str = Field(max_length=MAX_EVENT)
    source: str = Field(default="", max_length=256)
    attributes: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = Field(default="", pattern=r"^(?:[0-9a-fA-F]{32})?$")
    span_id: str = Field(default="", pattern=r"^(?:[0-9a-fA-F]{16})?$")

    @field_validator("timestamp")
    @classmethod
    def aware(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Timestamp requires a time zone")
        return value

    @field_validator("severity")
    @classmethod
    def severity_value(cls, value):
        value = value.upper()
        if value not in ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"):
            raise ValueError("Unknown severity")
        return value


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[Event] = Field(min_length=1, max_length=1000)


async def read_body(request):
    encoding = request.headers.get("content-encoding", "identity").lower()
    if encoding not in ("gzip", "identity"):
        raise APIError(415, "unsupported_encoding", "Use gzip or an uncompressed request.")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    output = bytearray()
    compressed_size = 0
    try:
        async for chunk in request.stream():
            compressed_size += len(chunk)
            if compressed_size > MAX_BODY:
                raise APIError(413, "batch_too_large", "Batches are limited to 4 MiB.")
            decoded = decoder.decompress(chunk, MAX_BODY + 1 - len(output)) if decoder else chunk
            output.extend(decoded)
            if len(output) > MAX_BODY or (decoder and decoder.unconsumed_tail):
                raise APIError(413, "batch_too_large", "Decompressed batches are limited to 4 MiB.")
        if decoder and (not decoder.eof or decoder.unused_data):
            raise APIError(400, "invalid_gzip", "Invalid gzip stream.")
    except zlib.error:
        raise APIError(400, "invalid_gzip", "Invalid gzip stream.") from None
    return bytes(output)


def any_value(value):
    kind = value.WhichOneof("value")
    if kind == "array_value":
        return [any_value(v) for v in value.array_value.values]
    if kind == "kvlist_value":
        return {v.key: any_value(v.value) for v in value.kvlist_value.values}
    if kind == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, kind) if kind else None


def otlp_events(raw, content_type):
    request = ExportLogsServiceRequest()
    try:
        if content_type == "application/x-protobuf":
            request.ParseFromString(raw)
        elif content_type == "application/json":
            # OTLP uses hex for trace/span IDs; generic protobuf JSON expects base64.
            import base64

            data = json.loads(raw)
            for resource in data.get("resourceLogs", []):
                for scope in resource.get("scopeLogs", []):
                    for log in scope.get("logRecords", []):
                        for field, size in (("traceId", 16), ("spanId", 8)):
                            if log.get(field):
                                value = bytes.fromhex(log[field])
                                if len(value) != size:
                                    raise ValueError
                                log[field] = base64.b64encode(value).decode()
            Parse(json.dumps(data), request, ignore_unknown_fields=True, max_recursion_depth=30)
        else:
            raise APIError(415, "unsupported_type", "Use application/json or application/x-protobuf.")
    except (DecodeError, ParseError, ValueError, TypeError, AttributeError, RecursionError):
        raise APIError(400, "invalid_otlp", "Malformed OTLP logs request.") from None
    result = []
    for resource in request.resource_logs:
        resource_attrs = {v.key: any_value(v.value) for v in resource.resource.attributes}
        for scope in resource.scope_logs:
            for log in scope.log_records:
                if len(result) >= 1000:
                    raise APIError(413, "batch_too_large", "Batches are limited to 1000 events.")
                number = log.severity_number
                if not 0 <= number <= 24:
                    raise APIError(400, "invalid_severity", "Invalid OTLP severity number.")
                body = any_value(log.body)
                stamp = log.time_unix_nano or log.observed_time_unix_nano
                result.append(
                    Event(
                        timestamp=datetime.fromtimestamp(stamp / 1e9, UTC) if stamp else None,
                        severity=("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")[(number - 1) // 4]
                        if number
                        else "INFO",
                        message=body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
                        source=str(resource_attrs.get("service.name", scope.scope.name)),
                        attributes={
                            "resource": resource_attrs,
                            "scope": MessageToDict(scope.scope),
                            "log": {v.key: any_value(v.value) for v in log.attributes},
                            "severity_text": log.severity_text,
                        },
                        trace_id=log.trace_id.hex(),
                        span_id=log.span_id.hex(),
                    )
                )
    return result


def normalize(events, now):
    rows = []
    for event in events:
        data = event.model_dump(mode="json")
        data["timestamp"] = event.timestamp.astimezone(UTC).isoformat() if event.timestamp else now.isoformat()
        data["trace_id"], data["span_id"] = data["trace_id"].lower(), data["span_id"].lower()
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        size = len(encoded.encode())
        if size > MAX_EVENT:
            raise APIError(413, "event_too_large", "Normalized events are limited to 64 KiB.")
        rows.append((data, size))
    if sum(size for _, size in rows) > MAX_BODY:
        raise APIError(413, "batch_too_large", "Normalized batches are limited to 4 MiB.")
    return rows
