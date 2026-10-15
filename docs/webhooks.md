# Local assignment webhooks

Open the local workspace's settings (gear icon) → Webhooks. Add a receiving URL,
optional board/assignee filters and events. New subscriptions start disabled.
Copy the generated signing secret into your receiver, enable the subscription,
and use **Send test event**. A test has type `webhook.test`: acknowledge it without
starting work. Saving configuration changes cancels pending deliveries from the
previous configuration. Rotate secret shows a replacement once and requires
updating the receiver. Disable pauses new notifications and cancels queued ones;
a request already in flight may still arrive.

The destination must be loopback or a private network. HTTP and HTTPS are supported;
HTTPS certificates are verified normally. Public, link-local/metadata and mixed
private/public DNS destinations are rejected. The worker resolves and validates
all addresses on every attempt, pins the selected address for connection, and
never follows redirects. In Docker, localhost means the container: use the
receiver's private bridge address, not the host's loopback address.

## Event contract v1

`POST` with `Content-Type: application/json`:

```json
{
  "schema_version": 1,
  "id": "unique-event-uuid",
  "type": "task.assignment.changed",
  "occurred_at": "2026-10-15T20:00:00+00:00",
  "source": {"instance_id": "installation-uuid", "workspace_id": "local-workspace-uuid"},
  "task": {"id": "task-uuid", "board_id": "board-uuid-or-null", "revision": 4},
  "previous_assignee": null,
  "assignee": {"id": "profile:profile-uuid", "kind": "droid"}
}
```

Events are `task.assignment.changed` and `task.deleted`. Assigned task creation
counts as assignment; unchanged assignments, comment/status edits and restores do
not trigger work. Assignee filters match either old or new identity, including
unassignment. Delivery is for local tasks only; portal sync/import is excluded.
The test envelope contains the version, ID, type, occurrence time and source only.
Task content, credentials, commands and provider selections are not in the event.

Headers:

- `X-AlshiTasks-Event-Id`: matches the event ID.
- `X-AlshiTasks-Timestamp`: Unix seconds for this delivery attempt.
- `X-AlshiTasks-Signature`: `sha256=` followed by the lowercase hex
  HMAC-SHA256 of `timestamp + "." + exact_raw_request_body`, using the signing secret.

Verify the signature in constant time and reject timestamps more than five
minutes from your clock. Deduplicate durably by `(source.instance_id, id)`;
reject a reused ID with different contents. Acknowledge only after durable
acceptance; any 2xx means accepted, not completed. Keep requests short and run
work asynchronously. See `scripts/webhook-example.py` for a receiver without
any agent framework. Its intentionally minimal in-memory behavior is not a
production work queue.

## Delivery and cancellation

Task changes and deliveries commit together. The supervised worker performs
network I/O outside the database transaction with a 10-second socket timeout.
Delivery is at least once. Transient connection failures, HTTP 408/425/429 and
5xx retry at 5 seconds, doubling to one hour, for up to 24 hours. Other HTTP
failures require correction and manual retry. Manual retry keeps the event ID.
Configuration changes invalidate queued attempts. History is retained 30 days.

Before launching work, receivers can use their existing project-read credential
with `GET /v1/projects/items/{id}/assignment?workspace_id={workspace}`. It returns
instance/workspace/task/board IDs, current revision, `assignment_revision`,
assignee ID/active state, deletion state and task number. `assignment_revision`
changes on assignment and delete/restore, not on ordinary edits, allowing a
receiver to reject delayed A→B→A events without rejecting later comments.
This additive REST endpoint does not change the shared MCP tool contract.
Agents continue to read and update tasks using the existing MCP tools.

Reassignment/deletion is a notification, not an execution mechanism. The receiver
owns cancellation and must wait for the previous execution to stop before
starting another for that source task. Completed side effects are not rolled back.
AlshiTasks has no dependency on Factory, Codex, Claude Code, or a run dashboard.

## Storage and recovery

Only the local owner may manage subscriptions. Signing secrets use the existing
OS credential store or encrypted server vault; they never sync to the portal or
appear in delivery logs. Subscription settings and event IDs are installation
local. A workspace restore clears subscriptions and pending deliveries, creates
a fresh source identity, and requires reconfiguration. Existing files and task
history are preserved by the usual backup/restore process. Use a complete private
instance backup with its separate vault key for operator recovery.
