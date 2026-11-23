<div align="center">

# aft-mcp

**An MCP server that gives AI agents safe, scoped access to your AFT boards.**

[![CI][ci-badge]][ci-link]
[![Python 3.14+][python-badge]][python-link]
[![License: MIT][license-badge]][license-link]
[![Ruff][ruff-badge]][ruff-link]
[![mypy: strict][mypy-badge]][mypy-link]
[![Conventional Commits][cc-badge]][cc-link]
[![Renovate][renovate-badge]][renovate-link]

</div>

---

An [MCP](https://modelcontextprotocol.io) server for the API of [AFT](https://github.com/sjefferson99/aft) (Aim,
Focus, Track), a self-hosted application for tasks. It fetches AFT's Swagger 2.0 spec, converts it to
OpenAPI 3, and exposes selected endpoints as MCP tools using [FastMCP](https://gofastmcp.com).

## What is exposed

- **Reads:** every `GET` endpoint, except any `GET` that changes state (`SIDE_EFFECT_GET_PATTERNS` in `src/aft_mcp/server.py`).
- **Writes:** only reversible changes to things *inside* a board (`ALLOWED_WRITES` in `src/aft_mcp/server.py`): edit or move a
  card, set done status and assignees, archive/unarchive cards, add comments and checklist items, update checklist
  items, add cards and columns, and rename or reorder columns.
- **Read-only mode:** with `AFT_MCP_READ_ONLY=true` no writes are exposed at all, only the reads above. Use it for a
  look-only connection, such as a second instance against a production AFT.
- **Board allowlist:** with `AFT_MCP_BOARD_IDS=12,15` the tools only reach those boards. See
  [Limiting the boards](#limiting-the-boards).
- **Never exposed:** `DELETE` routes, creating boards, and board-level changes (rename, owner,
  public link, import).

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)

## Setup

With uvx (see [Connecting to Claude and agents](#connecting-to-claude-and-agents)), there is nothing to install: make
a directory for the settings and put a `.env` in it, starting from [`.env.example`](.env.example). uv fetches Python
3.14 itself if it needs to.

From a checkout:

```bash
uv sync
cp .env.example .env   # then edit .env
```

## Configuration

All settings come from the process environment, then from a `.env` file (a variable already set in the environment
wins). The file is `.env` in the working directory; it is optional when the client passes every setting itself. Settings
are loaded once at startup into a typed settings class (`aft_mcp.settings`, using
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)). There are no defaults in the code,
so a missing or invalid setting stops the server, and the error lists every bad setting at once. The password is never
shown in reprs, logs or errors.

[docs/settings.md](docs/settings.md) lists every variable, with its type, default and allowed values. The essentials:

| Variable            | Required | Description                                     |
|---------------------|----------|-------------------------------------------------|
| `AFT_MCP_BASE_URL`  | yes      | Base URL the generated tools call               |
| `AFT_MCP_TIMEOUT`   | yes      | Request timeout in seconds                      |
| `AFT_MCP_USERNAME`  | yes      | AFT account email, sent as HTTP basic auth      |
| `AFT_MCP_PASSWORD`  | yes      | AFT account password                            |
| `AFT_MCP_READ_ONLY` | no       | `true` to expose only the safe reads, no writes |
| `AFT_MCP_BOARD_IDS` | no       | Comma-separated board ids the tools may reach   |

The tools are built from AFT's own spec, fetched from `api/apispec.json` under `AFT_MCP_BASE_URL`;
`AFT_MCP_SPEC_URL` or `AFT_MCP_SPEC_FILE` point somewhere else.

`.env` is git-ignored. Never commit real credentials.

### Recommended: a dedicated AFT user

Don't use your own AFT account. Create a separate user for the agent (for example `claude`) and put its email and
password in `.env`.

- **Limit it to specific boards.** In AFT, give that user access only to the boards the agent should work on, with the
  lowest role that does the job: a read-only role for boards it should only look at, an edit role for boards where it
  should make changes. It then cannot see or touch anything else, whatever tools the MCP server exposes.
- **Why not rely on the server alone?** This server's allowlist (see [What is exposed](#what-is-exposed)) limits which
  *kinds* of action are possible. AFT's own permissions limit *which boards* they apply to. Use both, and set
  `AFT_MCP_BOARD_IDS` as a second check on the boards (see below).
- **Attribution.** Comments, edits and assignments made through the MCP are recorded against that user, so you can
  tell the agent's work from yours. It can also be assigned cards.
- **Revocation.** If the credentials leak or the agent misbehaves, disable that one user without affecting your own
  account.

The tools only work where the user has permission. A write on a board where it has read-only access is refused by AFT
with a 403. `get_roles_my_roles` and `get_permissions_mapping` show what the user can currently do.

### Limiting the boards

`AFT_MCP_BOARD_IDS=12,15` guards against an AFT role granted too widely by mistake. When it is set, the server checks every
request a tool sends, before it reaches AFT:

- A request naming a board, column, card or checklist item (in the path, or as `board_id`, `column_id`, `card_id` or
  `card_ids` in the query or body) is sent only if that item is on a listed board. Otherwise the tool fails with a 403
  that names `AFT_MCP_BOARD_IDS`, and AFT never sees the request. Moving a card to a column on another board is refused
  too, and so is an id that is not a plain number.
- `get_boards` lists only the listed boards, and card search searches only them.
- A few tools that are not about a board keep working: the user's own profile and roles, the permission model, and
  the server's version, health, timezone and branding.
- Every other tool is refused, because the server can't tell which boards it would reach. That includes global boards,
  schedules, notifications and public board links, and any endpoint AFT adds later.

To check an id, the server makes extra read requests as the same user: it reads each listed board's cards (once, and
again when it meets a column or checklist item it doesn't know) and reads a card before acting on it. Unset
`AFT_MCP_BOARD_IDS` to allow every board the AFT user can reach.

## Running

```bash
uv run aft-mcp              # stdio transport; python -m aft_mcp does the same
```

### Audit log

The server logs every tool call to stderr, allowed or refused, with [structlog](https://www.structlog.org/). Each HTTP
request to AFT gets an `aft_response` line with its method, path and status, and each call ends with a `tool_result`
line with the outcome (`ok` or `error`, plus the exception type) and its duration. Every line from one call carries the
same `call` id, the `tool` and the id arguments it was called with (`*_id`, `*_ids`).

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
of it. MCP clients keep stderr
apart from the protocol stream; Claude Code and Claude Desktop write it to their MCP server logs.

## Connecting to Claude and agents

The server speaks MCP over stdio, so any MCP client launches it as a subprocess. There are two ways to give the client
a command to run.

### With uvx (no checkout)

[`uvx`](https://docs.astral.sh/uv/guides/tools/) (part of uv) installs aft-mcp into a cached, isolated environment and
runs it, so there is nothing to clone or keep in sync:

```bash
uvx --directory /path/to/aft-config --from git+https://github.com/samcorky/aft-mcp@<ref> aft-mcp
```

- **`--directory` is where the settings live.** uvx changes to that directory first, so the server reads the `.env`
  there. Make it a directory of its own holding only that `.env` (start from [`.env.example`](.env.example)), readable
  only by you. Always pass it: without it the server reads `.env` from whatever directory the client starts in.
- **Pin `<ref>`** to a release tag (such as `v2026.11.0`) or a commit, never a branch. uvx installs the pinned version
  once and reuses it, so the code only changes when you change the ref, after reading what changed. `@main` would
  give a different server whenever `main` moves.
- **Upgrading** is changing the ref in the client config and restarting the client. `uvx --refresh ...` rebuilds the
  cached environment if it ever needs it.
- **Without a `.env`**, leave out `--directory` and pass every setting as an environment variable in the client
  config instead. That puts the password in the client config, so prefer the `.env`.

Once aft-mcp is published to PyPI ([#21](https://github.com/samcorky/aft-mcp/issues/21)), `--from git+...` becomes
`uvx --directory /path/to/aft-config aft-mcp@2026.11.0`.

### From a checkout

For development, or to run code you have reviewed locally, point the client at your clone. `uv run --directory`
starts the server in the checkout, so it reads the `.env` there:

```bash
uv run --directory /path/to/aft-mcp aft-mcp
```

### Claude Code

```bash
claude mcp add aft -- uvx --directory /path/to/aft-config --from git+https://github.com/samcorky/aft-mcp@<ref> aft-mcp
```

Add `--scope user` to make it available in every project. Check it with `claude mcp list` or `/mcp` inside a session.
From a checkout, use `claude mcp add aft -- uv run --directory /path/to/aft-mcp aft-mcp`.

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings, then Developer, then Edit Config) and restart the app:

```json
{
  "mcpServers": {
    "aft": {
      "command": "uvx",
      "args": [
        "--directory", "/path/to/aft-config",
        "--from", "git+https://github.com/samcorky/aft-mcp@<ref>",
        "aft-mcp"
      ]
    }
  }
}
```

Claude Desktop may not see the `PATH` of your shell; if it can't find `uvx`, give its full path as `command` (`which
uvx`, or `where uvx` on Windows).

### Claude Agent SDK

```python
from claude_agent_sdk import ClaudeAgentOptions, query

options = ClaudeAgentOptions(
    mcp_servers={
        "aft": {
            "command": "uvx",
            "args": [
                "--directory",
                "/path/to/aft-config",
                "--from",
                "git+https://github.com/samcorky/aft-mcp@<ref>",
                "aft-mcp",
            ],
        }
    },
    allowed_tools=["mcp__aft__*"],  # or list specific tools
)

async for message in query(prompt="What is on my AFT board?", options=options):
    print(message)
```

### Other MCP clients

Point the client at the same command over stdio: the `uvx` one above, or `uv run --directory /path/to/aft-mcp aft-mcp`
from a checkout.

### Tool names and permissions

Clients prefix tool names with the server name, so `get_boards` appears as `mcp__aft__get_boards`. Use that form in
permission rules. To keep an agent read-only, allow only the `get_*` tools:

```json
{ "permissions": { "allow": ["mcp__aft__get_*"] } }
```

The write tools are `patch_*`, `post_*` and `put_*`. To enforce this in the server rather than the client, set
`AFT_MCP_READ_ONLY=true`: the write tools are then never created.

## How it works

The package lives in `src/aft_mcp/`, and the `aft-mcp` command runs `aft_mcp.server:main`.

- `settings.py` loads and validates the configuration.
- `server.py` fetches the spec, builds an authenticated HTTP client, and creates the FastMCP server.
- `swagger_convert.py` is a small Swagger 2.0 to OpenAPI 3 converter. It handles body and form parameters, response
  schemas, `definitions`, and basic auth. Specs that are already OpenAPI 3 pass through unchanged.

## Notes

- Credentials are held by the server, so every tool call acts as that one user. This is meant for local, single-user
  use over stdio.
- Only the `GET` conversion path has been exercised against a live API.

## Development

```bash
uv sync
uv run poe init    # install the git hooks
uv run poe check   # format, lint, types and tests, as CI runs them
```

The tests need no AFT instance and never read `.env`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow,
code style, and the rules for changing which endpoints are exposed.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md),
and report security issues privately as described in [SECURITY.md](SECURITY.md). For help, see
[SUPPORT.md](SUPPORT.md).

## Licence

[MIT](LICENSE)

[ci-badge]: https://github.com/samcorky/aft-mcp/actions/workflows/ci.yml/badge.svg?branch=main
[ci-link]: https://github.com/samcorky/aft-mcp/actions/workflows/ci.yml
[python-badge]: https://img.shields.io/badge/python-3.14%2B-3776ab?logo=python&logoColor=white
[python-link]: pyproject.toml
[license-badge]: https://img.shields.io/github/license/samcorky/aft-mcp
[license-link]: LICENSE
[ruff-badge]: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json
[ruff-link]: https://github.com/astral-sh/ruff
[mypy-badge]: https://img.shields.io/badge/mypy-strict-2a6db2
[mypy-link]: https://mypy-lang.org/
[cc-badge]: https://img.shields.io/badge/Conventional%20Commits-1.0.0-fe5196?logo=conventionalcommits&logoColor=white
[cc-link]: https://www.conventionalcommits.org/
[renovate-badge]: https://img.shields.io/badge/renovate-enabled-1a1f6c?logo=renovate&logoColor=white
[renovate-link]: https://github.com/renovatebot/renovate
