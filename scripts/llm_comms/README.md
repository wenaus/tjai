# LLM communications adapters

Local delivery components for the [communications plan](../../docs/llm-communications-plan.md).
The shared SessionStart hooks and normal Codex launcher start these components
automatically. Direct commands below are for diagnostics and designated tests.

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

An embedded Codex CLI has no external app-server connection. The normal shared
launcher now uses the same interactive TUI with an external owning runtime:

```bash
codex app-server --listen unix:///private/path/codex.sock
codex --remote unix:///private/path/codex.sock
```

Run these in separate terminals. The socket directory must be private. Preserve
the normal workspace and permission options when integrating this with the
shared launch function. Do not resume a session into this runtime while it is
still executing in another embedded CLI. Each normal launch owns a private
runtime; no shared system daemon is required.

The receiver uses bounded metadata reads and does not depend on subscribing to
token streams. The supervisor registers user sessions, excluding internal
housekeeping models, and handles later `/new` and `/resume` threads. On TUI exit
or launcher loss, any active turn can finish before the runtime closes. The
adapter never answers approval requests; a live test verified that an approval
requested during externally initiated input reached the TUI and that declining
it canceled the command.

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
implemented; see [service behavior](../../docs/llm-communications.md).

### Automatic cross-machine proof

On 2026-09-12, the shared Codex launcher on ec2dev registered its session without
a manual receiver command. A designated Claude session on swf-testbed registered
through the SessionStart integration. Codex invoked TJAI `send_message`, Claude
replied through `send_message(reply_to=...)`, and Codex invoked
`acknowledge_message`. The test driver did not relay the response.

- Original message: `841f9ac0-b028-4eae-9022-861286b10c96`.
- Claude reply: `7c3e9b2a-4d1f-4e8a-9b6c-2f5d8a1e3c47`, body `COMMS_CROSSHOST_REPLY_01`.
- Both deliveries reached `acknowledged`; each had exactly one `role=peer`
  dialog entry, on its receiving host, with the reply linked to the original.
- Native clients: Codex 0.154.0 on ec2dev; Claude Code 2.1.269 on swf-testbed.

The first automatic-discovery check exposed an internal Codex title-generation
thread in the directory. The supervisor now selects `threadSource=user`, and
the delivery adapter rejects `threadSource=system`. Native names/models are
refreshed by heartbeat without changing resource membership.

## Shared startup and direct receiver

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

Normal use is simply `codex` or `claude`. The shared Codex shell function calls
`computers/common/codex-launch.sh`, retaining its existing status line, sandbox,
approval, search and working-directory arguments. Administrative commands and
noninteractive execution pass through to native Codex. The wrapper reuses a
Python environment with `websockets`, or prepares `~/.tjai/comms-venv` once.

For a direct launcher check outside the shared shell function:

```bash
/var/www/tjai/.venv/bin/python scripts/llm_comms/launch_codex.py \
  -C /home/admin/github --sandbox danger-full-access --ask-for-approval on-request --search
```

SessionStart supplies the model its TJAI session ID and common messaging
instructions. Receivers use the canonical `location_name` and optional
`comms_resources` list in `~/.tjai/config.json`; `TJAI_COMMS_RESOURCES` can add
comma-separated resources. Every session joins `host:<location_name>`.

Existing embedded Codex sessions enroll through the recording hook on their
next user prompt, using `codex_queue` delivery. This queues input for the next
native input boundary and cannot steer their current turn. Restart through the
normal launcher for immediate delivery. Scheduled action/research workers with
`TJAI_ACTION_ID` are excluded; designated transport tests can set
`TJAI_COMMS_TEST=1` explicitly.

Logs live under `~/.tjai/comms/<session-id>.log`; the private runtime has a
`runtime.json`, `runtime.log` and `supervisor.log`. Set `TJAI_COMMS_DEBUG=1` to
print its path at launch. No receiver grants approvals or changes client policy.
