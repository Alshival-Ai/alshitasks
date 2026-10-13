"""Bounded HTTP framing for cloud-compatible log exporters."""

import json
import re
import socket
import sqlite3
import struct
import uuid
from urllib.parse import urlsplit, parse_qs
from . import cloud_logs
from .cloud_contract.errors import APIError
from .store import Problem


def read_exact(handler, size):
    data = handler.rfile.read(size)
    if len(data) != size:
        raise APIError(400, "invalid_body", "Incomplete request body.")
    return data


def read_raw(handler):
    transfer = handler.headers.get_all("Transfer-Encoding", [])
    lengths = handler.headers.get_all("Content-Length", [])
    if len(lengths) > 1 or len(transfer) > 1 or (lengths and transfer):
        raise APIError(400, "invalid_body", "Ambiguous request framing.")
    maximum = cloud_logs.MAX_BODY
    if transfer:
        if transfer[0].lower() != "chunked":
            raise APIError(400, "invalid_body", "Unsupported request framing.")
        output = bytearray()
        frames = 0
        while True:
            frames += 1
            if frames > 65536:
                raise APIError(413, "batch_too_large", "Too many body chunks.")
            line = handler.rfile.readline(1025)
            if len(line) > 1024 or not line.endswith(b"\r\n"):
                raise APIError(400, "invalid_body", "Invalid chunk.")
            try:
                value = line.split(b";", 1)[0].removesuffix(b"\r\n")
                if not re.fullmatch(b"[0-9a-fA-F]+", value):
                    raise ValueError()
                size = int(value, 16)
            except ValueError:
                raise APIError(400, "invalid_body", "Invalid chunk length.")
            if size < 0 or size + len(output) > maximum:
                raise APIError(413, "batch_too_large", "Batches are limited to 4 MiB.")
            if not size:
                # Bounded trailers are ignored; they cannot override framing/auth headers.
                count = 0
                while True:
                    trailer = handler.rfile.readline(8193)
                    count += len(trailer)
                    if count > 8192 or not trailer.endswith(b"\r\n"):
                        raise APIError(400, "invalid_body", "Invalid trailers.")
                    if trailer == b"\r\n":
                        return bytes(output)
            output.extend(read_exact(handler, size))
            if read_exact(handler, 2) != b"\r\n":
                raise APIError(400, "invalid_body", "Invalid chunk terminator.")
    try:
        size = int(lengths[0]) if lengths else 0
    except ValueError:
        raise APIError(400, "invalid_body", "Invalid Content-Length.")
    if size < 0:
        raise APIError(400, "invalid_body", "Invalid Content-Length.")
    if size > maximum:
        raise APIError(413, "batch_too_large", "Batches are limited to 4 MiB.")
    return read_exact(handler, size)


def handle(handler):
    from .server import local_owner

    api = handler.server.api
    request_id = str(uuid.uuid4())
    status = 200
    content_type = "application/json"
    headers = {}
    handler.connection.settimeout(10)
    try:
        owner = local_owner(handler)
        if handler.headers.get("Origin") or handler.headers.get("Sec-Fetch-Site") in (
            "cross-site",
            "same-site",
        ):
            raise Problem("Browser HTTP access is not supported; use the packaged desktop", 403)
        match = cloud_logs.ROUTE.fullmatch(urlsplit(handler.path).path)
        token = handler.headers.get("Authorization", "").removeprefix("Bearer ")
        with api.store.tx():
            cloud_logs.access(api, match, handler.command, owner, token)
        raw = read_raw(handler)
        with api.store.tx():
            resource = cloud_logs.access(api, match, handler.command, owner, token)
            if handler.command == "GET":
                value = cloud_logs.query(
                    api,
                    match[1],
                    resource,
                    {k: v[-1] for k, v in parse_qs(urlsplit(handler.path).query).items()},
                )
            else:
                content = handler.headers.get("Content-Type", "").split(";")[0]
                otlp = match[3] != "logs"
                value = cloud_logs.ingest(
                    api,
                    resource,
                    raw,
                    content,
                    handler.headers.get("Content-Encoding", "identity"),
                    otlp,
                    request_id,
                )
                if otlp:
                    content_type = content
                    value = b"" if content == "application/x-protobuf" else b"{}"
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
    except Exception as error:
        handler.close_connection = True
        if isinstance(error, APIError):
            e = error
        elif isinstance(error, Problem):
            e = APIError(
                error.status,
                {401: "invalid_credentials", 403: "forbidden", 404: "not_found"}.get(
                    error.status, "invalid_request"
                ),
                str(error),
            )
        elif isinstance(error, (TimeoutError, socket.timeout)):
            e = APIError(408, "request_timeout", "Request body timed out.")
        elif isinstance(error, sqlite3.Error):
            e = APIError(503, "logs_unavailable", "Log storage unavailable; retry with backoff.", 5)
        else:
            e = APIError(500, "internal_error", "Log operation failed; retry with backoff.", 5)
        status = e.status
        content_type = "application/json"
        if e.retry_after:
            headers["Retry-After"] = str(e.retry_after)
        data = json.dumps(
            {"error": {"code": e.code, "message": e.message}, "request_id": request_id}
        ).encode()
    handler.send_response(status)
    for k, v in {
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
        "X-Request-ID": request_id,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        **headers,
    }.items():
        handler.send_header(k, v)
    if handler.close_connection:
        handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(data)
