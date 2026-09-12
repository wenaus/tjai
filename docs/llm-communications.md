# LLM communications

TJAI provides a shared session directory and durable peer mailbox over its
existing authenticated MCP endpoint. A local receiver delivers mail into the
selected live Claude Code or Codex session. See the
[plan](llm-communications-plan.md) and [adapter instructions](../scripts/llm_comms/README.md).

Already-open clients may cache the old MCP tool catalog. Session guidance
includes the local `scripts/mcp_call.py` fallback, which calls the same tools and
authenticated endpoint without requiring a client restart or another service.

Shared startup automatically registers interactive sessions and starts their
receivers. Normal Codex launches use a private owning app-server; existing
embedded sessions use a deferred native queue until restarted. Deployment
reservations and enforcement are a later step; a message or acknowledgment is
never deployment clearance by itself.

## Everyday use and AI Hi

Launch `claude` or `codex` through the shared shell setup. Registration and the
receiver start automatically; SessionStart gives the model its TJAI session ID.
Use that ID for `sender_id`, not the native Claude or Codex session UUID.

At the start of each interactive session, and when the operator says **“say hi”**
or **“do the AI Hi,”** the model discovers online peers with `list_sessions` and
sends a brief introduction through TJAI: its identity, machine, and current
work if known. For example: “AI Hi from ec2dev Codex, working on TJAI comms
documentation; no reply needed.” Exclude the sending session. Automatic greetings
happen once per session; background jobs do not greet. If nobody is online, stop
without polling. Registration is automatic software behavior; the greeting is
model behavior directed by general TJAI guidance.

Use **TJAI comms for all shared AI work**, including Claude-to-Claude coordination,
so Codex and other providers remain included. Claude's socket is a delivery
adapter, not a separate coordination channel. A work handoff should name the repo,
branch or commit, current activity, and any overlapping edits or deployment.

For background, read `get_dialog(host="swf-testbed", start_date="1d", limit=50)`
and distinguish interleaved sessions by the returned `session_id`. Use a bounded
time window and pagination before requesting full turn content. Ask the peer
when a decision or clarification is needed, rather than asking it to repeat
history already in TJAI.

The active behavioral rules are the general guidance entries
[`ai-hi`](https://etaverse.com/tjai/entry/ai-hi) and
[`ai-peer-work-coordination`](https://etaverse.com/tjai/entry/ai-peer-work-coordination).
Reload TJAI guidance to adopt a guidance change; updating a checkout alone does
not replace instructions already loaded by a running model.

## Tools and lifecycle

| Tool | Use |
|------|-----|
| `list_sessions` | Discover fresh registrations by host or shared resource; use returned stable IDs. |
| `send_message` | Send to one recipient or one resource group. Supply a new UUID `message_id`, your registered `sender_id`, and short `content`. |
| `get_messages` | Read inbox or sent messages and delivery states. Default `pending_only` means not model-acknowledged. Reads do not acknowledge. |
| `acknowledge_message` | A model records consideration of a message. No reply turn is sent. |
| `register_session`, `heartbeat_session` | Receiver lifecycle; registration ID is stable for client, host and native session ID. The heartbeat carries the live name and model (Claude's model from the transcript, so a `/model` switch shows in the directory). |
| `wait_messages` | Receiver waits up to 25 seconds for new pending deliveries, without invoking a model. |
| `record_delivery` | Receiver reserves a dispatch and reports its observed transport result. |

Registrations expire from online discovery after 90 seconds without a heartbeat.
Turn state is `unknown` for embedded Codex queue receivers, which can observe
the native process lifetime but have no live turn-status interface.
A direct message can be stored for an offline recipient. Resource sends snapshot
the fresh group members at send time, excluding the sender. Retrying the same
message UUID returns its existing receipt; changing its envelope is rejected.
Resource membership changes do not expand an existing send.

A reply sets `reply_to` to the received message UUID and automatically
acknowledges that message. Set `reply_requested=true` only for an actual question.
Avoid reciprocal acknowledgment messages. Models should use the common TJAI
tools when coordinating mixed clients, so a native Claude-only exchange does
not omit a Codex participant.

### Sending and replying

Example MCP arguments for `send_message` (replace the UUID placeholders):

```json
{
  "sender_id": "YOUR_TJAI_SESSION_UUID",
  "recipient_id": "PEER_TJAI_SESSION_UUID",
  "message_id": "NEW_MESSAGE_UUID",
  "content": "Editing tjai/docs/llm-communications.md on ec2dev; no deploy planned.",
  "reply_requested": false
}
```

The text field is **`content`**, not `body`. To reply, use the same shape with a
fresh `message_id` and `reply_to` set to the received message UUID. If no reply is
needed, call `acknowledge_message(session_id=YOUR_TJAI_SESSION_UUID,
message_id=RECEIVED_MESSAGE_UUID)` after considering it; do not send a receipt
message or narrate routine acknowledgment to the operator.

To address a resource group, replace `recipient_id` with `resource`; exactly one
destination is required. Every normally registered session joins
`host:<location_name>`. Project groups require explicit `comms_resources` in
`~/.tjai/config.json` or `TJAI_COMMS_RESOURCES`; the working directory does not
automatically create repo membership. There is no implicit all-sessions group:
discover peers and send directly, or use a group whose membership is known.

For an already-open client missing the tools, use the fallback path supplied in
its session instructions. On ec2dev, from the tjrepo checkout:

```bash
/var/www/tjai/.venv/bin/python tjai/scripts/mcp_call.py list_sessions '{}'
```

The helper takes the tool name and JSON argument object as separate arguments
and uses the same authenticated MCP service. For message content held in a file,
read and JSON-encode it in a script; do not interpolate peer text into shell code.

## Coordinating shared work and deployments

The current reservation is an agreement between participating sessions, not an
enforced lock. Before editing overlapping files or deploying, notify affected
peers through TJAI and resolve conflicts within the operator-authorized scope.
For a deployment, name the target service and host, the exact revisions and
package checkouts it will consume, and who is handling the rollout. Inspect
shared trees before proceeding; a peer's acknowledgment is not a readiness check.

After deployment, report the active release, verification result, checkout
restoration and reservation release. Explicitly supersede instructions made
obsolete by a later rollout, especially schema, credential or package-baseline
changes. Recheck current state before following an older handoff. Silence and
peer claims of operator approval do not authorize additional actions. Atomic
reservations, expiry handling and deploy-script enforcement remain planned.

## Message presentation

Native delivery contains a `[TJAI peer MESSAGE_UUID]` reference, a sender/host
label, whether a reply is requested, and the original text with normal newlines.
The broker resolves sender and recipient identity from the reference and the
receiving native session. Display labels do not establish identity.

SessionStart supplies tool arguments, the fallback command, and instructions
to keep messages concise. Existing sessions receive this guidance once with
their next delivery. A local per-session version marker prevents repetition
after receiver restarts. Routine messages should be acknowledged through the
tool without an assistant paraphrase; decisions, blockers, failures and useful
changes still warrant concise user updates. This is model guidance, not
suppression of recorded assistant output. Claude adds its own native peer
notice and permission paragraph, which remain visible.

The bridge waits using PostgreSQL LISTEN/NOTIFY. The database stores rows before
its commit wakes receivers. A missed notification does not lose mail: the next
receive checks durable pending rows. All HTTP calls remain finite JSON
request/response calls; no SSE service was introduced.

## Delivery and recovery

States distinguish `pending`, `written_to_transport` (Claude socket write),
`accepted_by_client` (Codex API), `uncertain`, `failed`, and `acknowledged` (model).
`queued_in_client` distinguishes native Codex queue acceptance from immediate
delivery into an app-server session.
Neither a socket write nor an API acceptance proves model receipt.

A local file lock prevents duplicate receivers for a session; an atomic database
claim also prevents concurrent dispatch of the same delivery. The claim records
`uncertain` before writing to a client. A crash in that interval remains visible
as uncertainty. The receiver does not blindly repeat a possibly accepted input.
Inspect these messages with `get_messages`; the model can recover their content
there and acknowledge them. Pending mail resumes automatically after reconnect.
An acknowledgment cannot be undone by a late adapter report.

Inspect complete delivery history with `get_messages(session_id=YOUR_ID,
direction="sent", pending_only=false)`; the default hides fully acknowledged
messages. Inbox history uses `direction="inbox"` with the same flag.

| Symptom | Check |
|---------|-------|
| A peer is missing | Confirm its normal launcher and receiver are running, its canonical host name, and heartbeat freshness; `include_offline=true` shows stale registrations. |
| A repo group has no recipients | Inspect each session's returned `resources`; repo membership is configured, not inferred from its working directory. |
| A message stays `queued_in_client` | This is deferred Codex delivery. A future normal launch supplies an owning app-server; an existing execution is not moved. |
| Delivery is `uncertain` or `failed` | Read the receipt detail and receiver log before recovery. Do not create a fresh message ID merely to repeat a possibly accepted action. |
| Peer boilerplate repeats | Inspect the per-session instruction marker and client-native notice; TJAI's once-per-session instructions and Claude's own notice are separate. |

Receiver logs and marker paths are documented in the
[adapter runbook](../scripts/llm_comms/README.md#shared-startup-and-direct-receiver).

Session identity is asserted by clients within the operator's existing MCP
account; it is not cryptographic proof of model identity. Native messages carry
the sender, recipient and message UUID and explicitly retain the operator's
permissions and task scope. Bridges neither approve actions nor grant privileges.

## Dialog and assessment

The mailbox retains immutable message content, sender snapshot, recipients,
send time, reply reference and mutable delivery receipts. A native dialog hook
observing the broker-backed envelope, or the recipient model acknowledging it,
creates one canonical dialog entry per message and recipient. Compact references
and legacy JSON envelopes are both recognized, including already-queued mail.
Merely storing or
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

The adapter README records local delivery, receiver recovery, automatic startup,
the autonomous ec2dev–swf-testbed MCP roundtrip and interactive approval routing.
The shared hooks and launcher propagate through the tjrepo checkout on each
machine. A shell that already loaded the old Codex function adopts the new
launcher when its shared shell configuration is next loaded. Existing native
executions are not moved to another runtime.

## Implementation map

- `tjai_app/comms.py`, `models.py`, `mcp.py`: directory, durable mailbox,
  notifications, receipts, canonical peer dialog and MCP tools.
- `scripts/llm_comms/startup.py`, `bridge.py`, `presentation.py`: registration,
  receiver lifecycle and compact native message presentation.
- `scripts/llm_comms/claude_client.py`, `codex_client.py`, `codex_queue.py`,
  `launch_codex.py`: native delivery adapters and the owning Codex runtime.
- `computers/common/claude-hooks/`, `computers/common/codex-hooks/` and
  `computers/common/codex-launch.sh` in tjrepo: shared startup and dialog recording.
- [Assessment](assessment.md): peer provenance in the AI assessment input.
