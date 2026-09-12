# LLM communications

TJAI provides a shared session directory and durable peer mailbox over its
existing authenticated MCP endpoint. A local receiver delivers mail into the
selected live Claude Code or Codex session. See the
[plan](llm-communications-plan.md) and [adapter instructions](../scripts/llm_comms/README.md).

This initial implementation is opt-in. Existing interactive sessions and shell
launchers are unchanged. Deployment reservations and enforcement are a later
step; a message or acknowledgment is never deployment clearance by itself.

## Tools and lifecycle

| Tool | Use |
|------|-----|
| `list_sessions` | Discover fresh registrations by host or shared resource; use returned stable IDs. |
| `send_message` | Send to one recipient or one resource group. Supply a new UUID `message_id`, your registered `sender_id`, and short `content`. |
| `get_messages` | Read inbox or sent messages and delivery states. Default `pending_only` means not model-acknowledged. Reads do not acknowledge. |
| `acknowledge_message` | A model records consideration of a message. No reply turn is sent. |
| `register_session`, `heartbeat_session` | Receiver lifecycle; registration ID is stable for client, host and native session ID. |
| `wait_messages` | Receiver waits up to 25 seconds for new pending deliveries, without invoking a model. |
| `record_delivery` | Receiver reserves a dispatch and reports its observed transport result. |

Registrations expire from online discovery after 90 seconds without a heartbeat.
A direct message can be stored for an offline recipient. Resource sends snapshot
the fresh group members at send time, excluding the sender. Retrying the same
message UUID returns its existing receipt; changing its envelope is rejected.
Resource membership changes do not expand an existing send.

A reply sets `reply_to` to the received message UUID and automatically
acknowledges that message. Set `reply_requested=true` only for an actual question.
Avoid reciprocal acknowledgment messages. Models should use the common TJAI
tools when coordinating mixed clients, so a native Claude-only exchange does
not omit a Codex participant.

The bridge waits using PostgreSQL LISTEN/NOTIFY. The database stores rows before
its commit wakes receivers. A missed notification does not lose mail: the next
receive checks durable pending rows. All HTTP calls remain finite JSON
request/response calls; no SSE service was introduced.

## Delivery and recovery

States distinguish `pending`, `written_to_transport` (Claude socket write),
`accepted_by_client` (Codex API), `uncertain`, `failed`, and `acknowledged` (model).
Neither a socket write nor an API acceptance proves model receipt.

A local file lock prevents duplicate receivers for a session; an atomic database
claim also prevents concurrent dispatch of the same delivery. The claim records
`uncertain` before writing to a client. A crash in that interval remains visible
as uncertainty. The receiver does not blindly repeat a possibly accepted input.
Inspect these messages with `get_messages`; the model can recover their content
there and acknowledge them. Pending mail resumes automatically after reconnect.
An acknowledgment cannot be undone by a late adapter report.

Session identity is asserted by clients within the operator's existing MCP
account; it is not cryptographic proof of model identity. Native messages carry
the sender, recipient and message UUID and explicitly retain the operator's
permissions and task scope. Bridges neither approve actions nor grant privileges.

## Dialog and assessment

The mailbox retains immutable message content, sender snapshot, recipients,
send time, reply reference and mutable delivery receipts. A native dialog hook
observing the broker-backed envelope, or the recipient model acknowledging it,
creates one canonical dialog entry per message and recipient. Merely storing or
writing the message does not claim it appeared in the recipient's dialog.

These entries use `role=peer`, retain the actual sender and message UUID, and
contain the original message body. Hook delivery and later acknowledgment share
the same entry ID. Normal human text remains on the normal recording path.
Assessment formats peer provenance explicitly and counts peer messages separately
from user and assistant turns; peer-only sessions are not discarded as headless.

## Validation and rollout

`scripts/test_llm_comms.py` uses a temporary PostgreSQL schema and removes it
afterward. It checks concurrent send retries, fixed group membership, exclusive
dispatch, monotonic acknowledgment, dialog deduplication and attribution, and
notification wake/recovery. It creates no live mailbox or dialog records.

Apply migration `0026_llm_communications` through the normal TJAI deployment.
Each participating host runs only its own bridge with the existing
`TJAI_MCP_TOKEN`. Codex receivers additionally need `websockets`; install from
`scripts/llm_comms/requirements.txt` in the chosen Python environment.

Local native-client evidence is recorded in the adapter README. Full automatic
MCP reply, cross-machine latency and interactive approval routing must be
established before changing normal launchers. The first release exposes the
opt-in components without claiming that every existing session is reachable.
