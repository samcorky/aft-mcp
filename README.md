# aft-mcp

An [MCP](https://modelcontextprotocol.io) server for the API of [AFT](https://github.com/sjefferson99/aft) (Aim,
Focus, Track), a self-hosted application for tasks. It fetches AFT's Swagger 2.0 spec, converts it to
OpenAPI 3, and exposes selected endpoints as MCP tools using [FastMCP](https://gofastmcp.com).

## What is exposed

- **Reads:** every `GET` endpoint, except any `GET` that changes state (`SIDE_EFFECT_GET_PATTERNS` in `server.py`).
- **Writes:** only reversible changes to things *inside* a board (`ALLOWED_WRITES` in `server.py`): edit or move a
  card, set done status and assignees, archive/unarchive cards, add comments and checklist items, update checklist
  items, add cards and columns, and rename or reorder columns.
- **Never exposed:** `DELETE` routes, creating boards, and board-level changes (rename, owner,
  public link, import).

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
cp .env.example .env   # then edit .env
```

## Configuration

All settings come from `.env` (or the process environment). There are no defaults in the code, so a missing
required setting stops the server at startup with a clear message.

| Variable        | Required | Description                                                            |
|-----------------|----------|------------------------------------------------------------------------|
| `AFT_BASE_URL`  | yes      | Base URL the generated tools call                                      |
| `AFT_SPEC_URL`  | yes*     | URL of the Swagger 2.0 / OpenAPI spec                                  |
| `AFT_TIMEOUT`   | yes      | Request timeout in seconds                                             |
| `AFT_USERNAME`  | yes      | AFT account email, sent as HTTP basic auth                             |
| `AFT_PASSWORD`  | yes      | AFT account password                                                   |
| `AFT_SPEC_FILE` | no       | Load the spec from a local file instead of `AFT_SPEC_URL`              |

\* Not needed when `AFT_SPEC_FILE` is set.

`.env` is git-ignored. Never commit real credentials.

### Recommended: a dedicated AFT user

Don't use your own AFT account. Create a separate user for the agent (for example `claude`) and put its email and
password in `.env`.

- **Limit it to specific boards.** In AFT, give that user access only to the boards the agent should work on, with the
  lowest role that does the job: a read-only role for boards it should only look at, an edit role for boards where it
  should make changes. It then cannot see or touch anything else, whatever tools the MCP server exposes.
- **Why not rely on the server alone?** This server's allowlist (see [What is exposed](#what-is-exposed)) limits which
  *kinds* of action are possible. AFT's own permissions limit *which boards* they apply to. Use both. The server cannot
  restrict by board.
- **Attribution.** Comments, edits and assignments made through the MCP are recorded against that user, so you can
  tell the agent's work from yours. It can also be assigned cards.
- **Revocation.** If the credentials leak or the agent misbehaves, disable that one user without affecting your own
  account.

The tools only work where the user has permission. A write on a board where it has read-only access is refused by AFT
with a 403. `get_roles_my_roles` and `get_permissions_mapping` show what the user can currently do.

## Running

```bash
uv run python server.py     # stdio transport
```

## Connecting to Claude and agents

The server speaks MCP over stdio, so any MCP client launches it as a subprocess. Credentials come from the `.env`
next to `server.py`, so the client config needs no secrets and the working directory doesn't matter.

Use the full path to your checkout in place of `/path/to/aft-mcp` in the examples below.

### Claude Code

```bash
claude mcp add aft -- uv run --directory /path/to/aft-mcp python server.py
```

Add `--scope user` to make it available in every project. Check it with `claude mcp list` or `/mcp` inside a session.

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings, then Developer, then Edit Config) and restart the app:

```json
{
  "mcpServers": {
    "aft": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/aft-mcp", "python", "server.py"]
    }
  }
}
```

### Claude Agent SDK

```python
from claude_agent_sdk import ClaudeAgentOptions, query

options = ClaudeAgentOptions(
    mcp_servers={
        "aft": {
            "command": "uv",
            "args": ["run", "--directory", "/path/to/aft-mcp", "python", "server.py"],
        }
    },
    allowed_tools=["mcp__aft__*"],  # or list specific tools
)

async for message in query(prompt="What is on my AFT board?", options=options):
    print(message)
```

### Other MCP clients

Point the client at the same command: `uv run --directory /path/to/aft-mcp python server.py` over stdio.

### Tool names and permissions

Clients prefix tool names with the server name, so `get_boards` appears as `mcp__aft__get_boards`. Use that form in
permission rules. To keep an agent read-only, allow only the `get_*` tools:

```json
{ "permissions": { "allow": ["mcp__aft__get_*"] } }
```

The write tools are `patch_*`, `post_*` and `put_*`.

## How it works

- `server.py` loads config, fetches the spec, builds an authenticated HTTP client, and creates the FastMCP server.
- `swagger_convert.py` is a small Swagger 2.0 to OpenAPI 3 converter. It handles body and form parameters, response
  schemas, `definitions`, and basic auth. Specs that are already OpenAPI 3 pass through unchanged.

## Notes

- Credentials are held by the server, so every tool call acts as that one user. This is meant for local, single-user
  use over stdio.
- Only the `GET` conversion path has been exercised against a live API.
