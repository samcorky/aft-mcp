# Access and auditing

How to limit what an agent can do through aft-mcp, and how to see what it did. There are three layers, and they work
together: what the AFT user may do in AFT, which tools aft-mcp exposes, and which tools the MCP client allows.

## A dedicated AFT user

Don't use your own AFT account. Create a separate user for the agent (for example `claude`), make it **API only**
in User Management (it can't sign in to the web app), and choose **Create API Token** on it. Put the token in `.env`
as `AFT_MCP_TOKEN`. Make the token read only if the agent only needs to look, and give it an expiry. On AFT versions
without API tokens, use the user's email and password instead.

- **Limit it to specific boards.** In AFT, give that user access only to the boards the agent should work on, with the
  lowest role that does the job: a read-only role for boards it should only look at, an edit role for boards where it
  should make changes. It then cannot see or touch anything else, whatever tools the MCP server exposes.
- **Why not rely on the server alone?** This server's allowlist (see
  [What is exposed](../README.md#what-is-exposed)) limits which *kinds* of action are possible. AFT's own permissions
  limit *which boards* they apply to. Use both, and set `AFT_MCP_BOARD_IDS` as a second check on the boards (see
  [Limiting the boards](#limiting-the-boards)).
- **Attribution.** Comments, edits and assignments made through the MCP are recorded against that user, so you can
  tell the agent's work from yours. It can also be assigned cards.
- **Revocation.** If the token leaks, revoke it in AFT (Manage API Tokens on the user) without changing anything
  else. If the agent misbehaves, disable that one user without affecting your own account.

The tools only work where the user has permission. A write on a board where it has read-only access is refused by AFT
with a 403. `get_auth_me` returns the user's effective `permissions`; `get_roles_my_roles` needs a role-management
permission, so it returns 403 for a normal user.

## Read-only mode

`AFT_MCP_READ_ONLY=true` leaves out every write tool, so only the safe reads are created and no tool can change
anything. Use it for a look-only connection, such as a second instance against a production AFT.

## Limiting the boards

`AFT_MCP_BOARD_IDS=12,15` guards against an AFT role granted too widely by mistake. When it is set, the server checks
every request a tool sends, before it reaches AFT:

- A request naming a board, column, card or checklist item (in the path, or as `board_id`, `column_id`, `card_id` or
  `card_ids` in the query or body) is sent only if that item is on a listed board. Otherwise the tool fails with a 403
  that names `AFT_MCP_BOARD_IDS`, and AFT never sees the request. Moving a card to a column on another board is refused
  too, and so is an id that is not a plain number.
- `get_boards` lists only the listed boards, and card search searches only them.
- A few tools that are not about a board keep working: the user's own profile and roles, the permission model, and
  the server's version, health, timezone and branding.
- Every other tool is refused, because the server can't tell which boards it would reach. That includes global boards
  (so promoting a card to one is refused too), schedules, notifications and public board links, and any endpoint AFT
  adds later.

To check an id, the server makes extra read requests as the same user: it reads each listed board's cards (once, and
again when it meets a column or checklist item it doesn't know) and reads a card before acting on it. Unset
`AFT_MCP_BOARD_IDS` to allow every board the AFT user can reach.

## Audit log

The server logs every tool call to stderr, allowed or refused, with [structlog](https://www.structlog.org/). Each HTTP
request to AFT gets an `aft_response` line with its method, path and status, and each call ends with a `tool_result`
line with the outcome (`ok` or `error`, plus the exception type) and its duration. Every line from one call carries the
same `call` id, the `tool` and the id arguments it was called with (`*_id`, `*_ids`). With `AFT_MCP_INSTANCE` set,
every line also carries `instance`.

```text
timestamp=2026-11-23T12:00:01.204Z level=info logger=aft_mcp.audit event=aft_response call=0c3e3ff3 tool=patch_cards_done method=PATCH path=/api/cards/42/done status=200 card_id=42
timestamp=2026-11-23T12:00:01.207Z level=info logger=aft_mcp.audit event=tool_result call=0c3e3ff3 tool=patch_cards_done outcome=ok duration_ms=81 card_id=42
timestamp=2026-11-23T12:00:04.511Z level=info logger=aft_mcp.audit event=tool_result call=9b1d77e2 tool=delete_cards_by_card_id outcome=error error=NotFoundError duration_ms=1 card_id=42
```

The lines are [logfmt](https://brandur.org/logfmt) by default; set `AFT_MCP_LOG_FORMAT=json` for one JSON object per
line, for log pipelines. Values are quoted and escaped, so a name sent by the client can't forge a line.

FastMCP's and httpx's own log lines go through the same handler, in the same format, so stderr has one format
throughout. A FastMCP error logged during a call carries that call's `call`, `tool` and id fields. httpx's per-request
INFO lines are dropped, since `aft_response` covers them without the query string.

Request and response bodies, query strings and error messages are never logged, so card text and credentials stay out
of it. MCP clients keep stderr apart from the protocol stream; Claude Code and Claude Desktop write it to their MCP
server logs.

### On a server: syslog

When aft-mcp runs on a server and is reached over SSH, its stderr travels back to the client, so the log ends up on
the workstation rather than the server. `AFT_MCP_LOG_SYSLOG=true` also sends every line to the local syslog
(`/dev/log`, ident `aft-mcp`), with `AFT_MCP_LOG_SYSLOG_FACILITY` (default `user`; `auth` or `authpriv` puts it next to
sshd's lines). `AFT_MCP_LOG_STDERR=false` then stops the client receiving the log at all. When `SSH_CONNECTION` is set,
every line carries `ssh_client` (the client's address and port). A missing syslog socket stops the server rather than
losing the log.

## Client permissions

See [Tool names and permissions](clients.md#tool-names-and-permissions) for allowing only the read tools in the client.
