<div align="center">

# aft-mcp

**An MCP server that gives AI agents safe, scoped access to your AFT boards.**

[![PyPI][pypi-badge]][pypi-link]
[![Release][release-badge]][release-link]
[![Container][ghcr-badge]][ghcr-link]
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
Focus, Track), a self-hosted application for tasks. It turns AFT's API into MCP tools, so an agent such as Claude can
read your boards and make safe, reversible changes to them.

## What is exposed

- **Reads:** every `GET` endpoint, except any `GET` that changes state (`SIDE_EFFECT_GET_PATTERNS` in
  `src/aft_mcp/server.py`).
- **Writes:** only reversible changes to things *inside* a board (`ALLOWED_WRITES` in `src/aft_mcp/server.py`): edit or
  move a card, set done status and assignees, archive/unarchive cards, add comments and checklist items, update
  checklist items, add cards and columns, rename or reorder columns, and promote cards to a global board and move them
  between its states. Adding a card or column, and promoting a card, can't be undone through the server (deleting and
  demoting are `DELETE`s), so remove a mistake in the AFT UI.
- **Never exposed:** `DELETE` routes (including demoting a card from a global board), creating boards, and board-level
  or global-board settings (rename, owner, visibility, public link, import).

Read-only mode, a board allowlist and an audit log narrow this further: see [Access and auditing](docs/access.md).

## Quickstart

You need [uv](https://docs.astral.sh/uv/); it fetches Python 3.14 itself if needed.

1. **Make an AFT user for the agent.** In AFT's User Management, create a user (for example `claude`), make it
   **API only**, give it access to the boards the agent should work on, and choose **Create API Token**.

2. **Put the settings in `~/.config/aft-mcp/.env`** (`%APPDATA%\aft-mcp\.env` on Windows), readable only by you
   (`mkdir -p ~/.config/aft-mcp && chmod 700 ~/.config/aft-mcp`). Two settings are enough:

   ```dotenv
   AFT_MCP_BASE_URL=https://aft.example.com
   AFT_MCP_TOKEN=<the token from step 1>
   ```

3. **Add it to Claude Code**, pinned to a release:

   ```bash
   claude mcp add aft -- uvx aft-mcp@2026.11.0
   ```

   Then ask Claude "What is on my AFT boards?", or check the connection with `/mcp`.

## Documentation

- [Settings](docs/settings.md): every variable, with its type, default and allowed values. Start from
  [`.env.example`](.env.example).
- [Connecting clients](docs/clients.md): Claude Code, Claude Desktop, the Agent SDK and other MCP clients, running
  from a checkout, and tool permissions.
- [Access and auditing](docs/access.md): the dedicated AFT user, read-only mode, the board allowlist and the audit log.
- [Running with Docker](docs/docker.md): the image on GHCR, its settings and tags, and building it yourself.
- [Running on a server](docs/remote.md): one aft-mcp on a server, reached from each workstation over SSH, so the
  credential never sits on a workstation.

## How it works

aft-mcp fetches AFT's Swagger 2.0 spec, converts it to OpenAPI 3 (`swagger_convert.py`), and builds one MCP tool per
allowed endpoint with [FastMCP](https://gofastmcp.com) (`server.py`). Settings are loaded and checked once at startup
(`settings.py`). Every tool call acts as the one AFT user whose credentials the server holds, so it is meant for
single-user use over stdio.

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

[pypi-badge]: https://img.shields.io/pypi/v/aft-mcp?logo=pypi&logoColor=white
[pypi-link]: https://pypi.org/project/aft-mcp/
[release-badge]: https://img.shields.io/github/v/release/samcorky/aft-mcp?logo=github
[release-link]: https://github.com/samcorky/aft-mcp/releases/latest
[ghcr-badge]: https://img.shields.io/github/v/release/samcorky/aft-mcp?label=ghcr.io&logo=docker&logoColor=white
[ghcr-link]: https://github.com/samcorky/aft-mcp/pkgs/container/aft-mcp
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
