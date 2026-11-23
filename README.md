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
- **Read-only mode:** with `AFT_READ_ONLY=true` no writes are exposed at all, only the reads above. Use it for a
  look-only connection, such as a second instance against a production AFT.
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

All settings come from the process environment, then from a `.env` file (a variable already set in the environment
wins). The file is `.env` in the working directory, or the one `AFT_ENV_FILE` names; it is optional when the client
passes every setting itself. Settings are loaded once at startup into a typed settings class (`aft_mcp.settings`, using
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)). There are no defaults in the code,
so a missing or invalid setting stops the server, and the error lists every bad setting at once. The password is never
shown in reprs, logs or errors.

| Variable        | Required | Description                                                            |
|-----------------|----------|------------------------------------------------------------------------|
| `AFT_BASE_URL`  | yes      | Base URL the generated tools call (`http://` or `https://`)            |
| `AFT_SPEC_URL`  | yes*     | URL of the Swagger 2.0 / OpenAPI spec                                  |
| `AFT_TIMEOUT`   | yes      | Request timeout in seconds, above zero                                 |
| `AFT_USERNAME`  | yes      | AFT account email, sent as HTTP basic auth                             |
| `AFT_PASSWORD`  | yes      | AFT account password                                                   |
| `AFT_SPEC_FILE` | no       | Load the spec from a local file instead of `AFT_SPEC_URL`              |
| `AFT_READ_ONLY` | no       | `true` to expose only the safe reads, no writes (default `false`)      |
| `AFT_ENV_FILE`  | no       | Path of the `.env` file to read, instead of `./.env` (environment only) |

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
uv run aft-mcp              # stdio transport; python -m aft_mcp does the same
```

## Connecting to Claude and agents

The server speaks MCP over stdio, so any MCP client launches it as a subprocess. `uv run --directory` starts it in
your checkout, so it reads the `.env` there and the client config needs no secrets.

Use the full path to your checkout in place of `/path/to/aft-mcp` in the examples below. To run it without a checkout,
use `uvx --from git+https://github.com/samcorky/aft-mcp aft-mcp` as the command, and pass the settings as environment
variables in the client config or point `AFT_ENV_FILE` at a `.env` file.

### Claude Code

```bash
claude mcp add aft -- uv run --directory /path/to/aft-mcp aft-mcp
```

Add `--scope user` to make it available in every project. Check it with `claude mcp list` or `/mcp` inside a session.

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings, then Developer, then Edit Config) and restart the app:

```json
{
  "mcpServers": {
    "aft": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/aft-mcp", "aft-mcp"]
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
            "args": ["run", "--directory", "/path/to/aft-mcp", "aft-mcp"],
        }
    },
    allowed_tools=["mcp__aft__*"],  # or list specific tools
)

async for message in query(prompt="What is on my AFT board?", options=options):
    print(message)
```

### Other MCP clients

Point the client at the same command: `uv run --directory /path/to/aft-mcp aft-mcp` over stdio.

### Tool names and permissions

Clients prefix tool names with the server name, so `get_boards` appears as `mcp__aft__get_boards`. Use that form in
permission rules. To keep an agent read-only, allow only the `get_*` tools:

```json
{ "permissions": { "allow": ["mcp__aft__get_*"] } }
```

The write tools are `patch_*`, `post_*` and `put_*`. To enforce this in the server rather than the client, set
`AFT_READ_ONLY=true`: the write tools are then never created.

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
