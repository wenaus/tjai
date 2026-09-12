# LLM communications adapters

Local delivery components for the [communications plan](../../docs/llm-communications-plan.md).
The scripts are not installed into normal client startup yet.

## Codex

`codex_client.py` connects to an explicitly selected private app-server Unix
socket. It lists only loaded threads and refuses to resume a saved transcript as
a substitute for reaching a live session. Idle delivery uses `turn/start`;
active delivery uses `turn/steer` with the expected active turn ID. A stale turn
is rejected rather than silently becoming a new turn. The adapter obtains the
current turn with a bounded metadata query, without loading conversation history.

Use a Python environment with `websockets`; on ec2dev the existing TJAI runtime
provides it:

```bash
/var/www/tjai/.venv/bin/python scripts/llm_comms/codex_client.py \
  --socket /private/path/codex.sock inspect

/var/www/tjai/.venv/bin/python scripts/llm_comms/codex_client.py \
  --socket /private/path/codex.sock send \
  --thread SESSION_UUID --sender SENDER_SESSION_ID \
  --message-id MESSAGE_UUID --message-file /path/to/message.txt
```

Optionally pin a turn with `--expected-turn TURN_UUID`. Message bodies are read from
a file or stdin (`--message-file -`) rather than interpolated into shell code.
The response `accepted_by_client` is not model acknowledgment. Ambiguous network
failures are not automatically retried.

The normal embedded Codex CLI has no external app-server connection. An opt-in
session can use the same interactive TUI with an external owning runtime:

```bash
codex app-server --listen unix:///private/path/codex.sock
codex --remote unix:///private/path/codex.sock
```

Run these in separate terminals. The socket directory must be private. Preserve
the normal workspace and permission options when integrating this with the
shared launch function. Do not resume a session into this runtime while it is
still executing in another embedded CLI. No global daemon or launch-function
change was installed by the initial proof.

The RPC reader exposes selected lifecycle events, excluding token deltas. A
connection does not automatically subscribe to a thread merely by sending input.
The proof explicitly attached an observer to the already-loaded test thread to
receive completion events. Production lifecycle integration must establish its
subscription when the session is created. The adapter does not answer approval
requests; interactive permission routing needs verification before general
launcher rollout.

## Claude Code

`claude_client.py` writes a peer user frame to an explicitly selected inbox
socket, with the target session UUID to guard against a reused process/socket.
The socket is exported to session hooks as `CLAUDE_CODE_MESSAGING_SOCKET` and
listed in the session registry. It must be owned by the current user and private.

```bash
python3 scripts/llm_comms/claude_client.py \
  --socket /private/path/claude.sock --session SESSION_UUID \
  --sender SENDER_SESSION_ID --message-id MESSAGE_UUID \
  --message-file /path/to/message.txt
```

The `written_to_transport` result does not mean the receiving model read the
message. Claude's native inbound policy can hold or refuse it. The adapter does
not change that policy or assert a permission class. An own-session child bridge
may supply its exported messaging token to the Python function; tokens are not
CLI arguments or part of delivery reports.

The socket interface is documented by Anthropic. The exact newline-delimited
user-frame shape was checked against installed Claude Code 2.1.268 and exercised
against its live inbox. Keep this version-dependent boundary inside the adapter.
The native UI calls the sender another Claude session even when the source is
Codex; the message envelope preserves the actual sender and TJAI provenance.

## Initial delivery evidence — 2026-09-12

The bounded proof used two designated interactive sessions on ec2dev, outside
application working trees. `TJAI_ACTION_ID=tjai-comms-proof` excluded the test
turns from ordinary TJAI dialog recording. No other working session received
messages except the initiating Codex session's own queue probe.

| Check | Observed result |
|-------|-----------------|
| Existing embedded Codex queue | `codex queue` accepted the message, but it remained queued during the active turn. The exact test queue item was subsequently removed through the queue API. |
| Codex idle | `turn/start` produced `COMMS_CODEX_IDLE_01` in the model response and interactive TUI. |
| Codex active | `turn/steer` was accepted in approximately 3 ms; `COMMS_CODEX_STEER_01` appeared in the same turn as the initial response. |
| Codex stale turn | The completed turn ID was rejected with `no active turn to steer`. |
| Codex unloaded target | Adapter refused delivery without resuming the saved session. |
| Claude idle | Inbox delivery produced `COMMS_CLAUDE_IDLE_01`. |
| Claude active | A second message sent 250 ms after the first was included in the response as `COMMS_CLAUDE_ACTIVE_01`. |
| Return delivery | Claude's response was relayed by the test driver into Codex, which replied `COMMS_FROM_CLAUDE_RECEIVED_01`. This was a transport proof, not yet an autonomous MCP reply loop. |
| Codex offline queue | A message queued while the dedicated app-server was stopped survived restart and TUI reconnection; the model replied `COMMS_CODEX_OFFLINE_02`. This establishes native Codex queue recovery, not yet TJAI mailbox recovery. |

Codex test thread: `01a0953a-168f-7332-8dfb-49d616f8143d` (CLI 0.154.0).
Claude test session: `79715909-3af5-4c7f-9ac9-651da5e78035` (CLI 2.1.268).
Evidence came from live model output, TUI output and the designated transcripts.
Socket-write and API-acceptance timings are not model-response latency guarantees.

After deployment of migration 0026, the same designated pair received messages
through the public HTTPS TJAI MCP endpoint and the new receivers:

| Check | Observed result |
|-------|-----------------|
| TJAI → Claude | `COMMS_TJAI_HTTPS_CLAUDE_01`; stored-to-socket-report interval 119 ms. |
| TJAI → Codex | `COMMS_TJAI_HTTPS_CODEX_01`; stored-to-client-report interval 145 ms. |
| TJAI receiver recovery | Receiver stopped and its registration observed offline; message `d774f7af-5b13-402f-a973-22639f2968a5` stored pending; restarted receiver delivered it and the model returned `COMMS_TJAI_RECOVERY_01`. |
| Opt-in launcher | Started the TUI and private app-server, automatically registered its first loaded thread, and retained the runtime after TUI disconnect. |

These were driver-originated transport probes, not autonomous MCP replies. The
receipt states remained transport reports; no adapter asserted model acknowledgment.
The test receivers were stopped afterward. The isolated PostgreSQL checks also
passed concurrent idempotency, dispatch claims, dialog deduplication, assessment
attribution and notification wake (approximately 10 ms locally).

The directory/mailbox, receiver, acknowledgment and canonical peer dialog are
now implemented; see [service behavior](../../docs/llm-communications.md).
Remaining rollout checks include an autonomous MCP reply loop, cross-machine
proof, and general client lifecycle and permission-routing integration.

## Opt-in receiver

For an existing Claude session, use its exact `sessionId` and
`messagingSocketPath` from `~/.claude/sessions/`. For Codex, use a thread loaded
in the selected app-server. Run one receiver for that session:

```bash
python scripts/llm_comms/bridge.py --client claude \
  --native-id SESSION_UUID --socket /private/path/claude.sock \
  --host ec2dev --name my-session --resource swf-monitor
```

Use `--client codex` for Codex. The bridge prints its stable TJAI registration
ID, maintains heartbeat freshness and receives mail without model polling. It
uses the existing `TJAI_MCP_TOKEN` environment variable or `~/.env`; optional
`TJAI_MCP_URL` overrides the endpoint. `--once` receives one bounded batch.

For a new opt-in Codex TUI with automatic receiver startup:

```bash
/var/www/tjai/.venv/bin/python scripts/llm_comms/launch_codex.py \
  --name comms-codex --host ec2dev --cwd /home/admin/github \
  --resource swf-monitor -- --no-alt-screen
```

This starts a private runtime and a receiver that registers the TUI's first
loaded thread. Normal Codex config and extra TUI options apply; the wrapper
does not invoke a shell function. It prints a private runtime directory with
logs and process IDs. The runtime and receiver remain running when the TUI
disconnects; the printed reconnect command returns to that runtime. Stop the
printed processes only after their work has finished. Use one thread per opt-in
runtime, or select threads explicitly with `bridge.py`.
