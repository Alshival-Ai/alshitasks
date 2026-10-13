# Cloud-compatible local logs

Open a resource's **Logs → Send logs here** section. Enable the localhost listener
(default port 8765), create a sender token, and copy the JSON or OTLP example.
The listener binds to loopback; tokens are resource-scoped and can be revoked.
Create a separate read token for queries. Desktop owner IPC needs no bearer token.

Applications use the same routes and payloads with either base URL:

- Local: `http://127.0.0.1:8765`
- Cloud: `https://api.alshival.ai`
- JSON ingestion and queries: `/v1/workspaces/{workspace_id}/resources/{resource_id}/logs`
- OTLP ingestion: `/v1/workspaces/{workspace_id}/resources/{resource_id}/otlp/v1/logs`

Synced resources expose the portal's canonical workspace/resource UUIDs. Offline
resources use local UUIDs; they must exist at the destination before switching.
Change the base URL and credential together. Local and cloud credentials are
separate, and cursors cannot be transferred between destinations.

JSON accepts `{"events":[{"severity":"INFO","message":"Worker started","source":"worker"}]}`.
Optional fields are timestamp (timezone-aware), attributes, trace_id (32 hex), and
span_id (16 hex). Severity is TRACE, DEBUG, INFO, WARN, ERROR or FATAL. Do not send
an event ID. Batches contain 1–1000 events, at most 4 MiB compressed/decompressed,
with at most 64 KiB per normalized event. Gzip and chunked HTTP are supported.
OTLP supports HTTP JSON and protobuf; this listener does not implement gRPC.

GET supports start, end, severity, q, trace_id, limit (1–200) and cursor. Responses
contain items, next_cursor and retention. Local logs retain the device's existing
retention/quota policy; cloud plan quotas and rate limits remain server-managed.
Retry network errors and 429/5xx with backoff and Retry-After. Retries may duplicate
events; delivery is at least once. Existing `/v1/logs` clients retain their legacy
sender-ID deduplication and response format.

## Optional cloud forwarding

The resource Logs panel can forward newly received local events to a cloud JSON
logs URL. The cloud token is stored in the operating-system keyring. Forwarding
is disabled by default and independent of project synchronization. The local
copy is stored before forwarding. The durable queue is bounded at 16 MiB; when
full, additional cloud copies are skipped and the panel reports that condition.
4xx credential/configuration failures pause a target until Retry; transient
failures back off. Disable stops delivery and retains queued copies locally.
The status panel refreshes when setup is refreshed or an action completes.

## Migration and recovery

The log schema migration adds cloud metadata while preserving legacy events and
IDs. A populated seven-column database gets a mode-0600 sibling recovery copy,
`logs.pre-cloud-v1.sqlite`, before migration. The backup contains historical logs
and is outside active retention cleanup; retain or remove it deliberately.
Older desktop versions cannot write the expanded schema. Prefer a corrective
release for rollback. Never restore the recovery copy over newer log writes
without explicitly accepting that loss. Other user/project data is unaffected.

## Contract maintenance

`log-contract-provenance.json` pins the cloud codec revision and byte hashes.
`log-contract-test.py` runs the same ingestion corpus against that cloud API and
the real local HTTP handler. Unit tests cover migration, auth, canonical IDs,
OTLP, framing, limits, cursor snapshots, concurrent writes and storage failure.
The packaged Electron log test verifies fields, filters and token setup.
Private codec dependencies are bundled for CPython 3.12, 3.13 and 3.14; no runtime
package downloads are required. Update the pins and conformance corpus together.
