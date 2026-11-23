# Agent instructions

aft-mcp is an MCP server that turns the API of [AFT](https://github.com/sjefferson99/aft)
into MCP tools. This file is the shared starting point for coding agents; `CLAUDE.md`
imports it. Human-facing rules live in [CONTRIBUTING.md](CONTRIBUTING.md): link to them,
don't copy them here.

## Read what the task needs

| Task                                        | Guidance                                                                       |
|---------------------------------------------|--------------------------------------------------------------------------------|
| Setup, commands, style, pull requests       | [CONTRIBUTING.md](CONTRIBUTING.md); tasks in `pyproject.toml` (`uv run poe`)   |
| Which endpoints become tools                | "Changing what is exposed" in CONTRIBUTING.md, then `server.py`                |
| Swagger 2.0 to OpenAPI 3 conversion         | `swagger_convert.py` and `tests/test_swagger_convert.py`                       |
| Configuration and connecting clients        | [README.md](README.md)                                                         |

## Sub-agents

Never use sub-agents, for any task, including code review, research or searching. Do all
the work yourself in the main session.

## Rules that matter most

- `uv run poe check` must pass before you call work done: ruff, strict mypy, and pytest
  with coverage at 100%.
- Never widen what the server exposes without being asked. No `DELETE` routes and no
  board-level writes, ever. A change to `ALLOWED_WRITES` or `SIDE_EFFECT_GET_PATTERNS` updates the
  reviewed snapshots and tool list in `tests/test_server.py` and the README in the
  same change; never edit a snapshot just to make a test pass.
- Tests must not use the network or read `.env`. Stub HTTP with `httpx2.MockTransport`
  (see `stub_http` in `tests/test_server.py`) and settings with the `aft_env` fixture.
- `server.py` must stay import-safe: build the server in `main()`, not at module level.
- Never read, print or commit `.env`, and don't put real AFT URLs or credentials in
  tests, docs or commits.

## Posting to GitHub

Anything an agent posts goes out from the user's GitHub account, so readers will assume
the user wrote it unless told otherwise.

- **Label AI-written text.** Every issue body, pull request description and comment
  (summary or inline) must start with this exact line, followed by a blank line:

  `> **⚠️ AI-generated comment, posted on behalf of @<username> — not written by them personally.**`

  `<username>` is the GitHub login of the person you are working for, the account
  `gh` posts as: `gh api user --jq .login`.

  A footer is not enough, because readers see the top first. Check the final body before
  each post, and if something went out without the line, edit it straight away.
- **Write bodies from a file.** Use `gh ... --body-file <path>`, or write JSON with
  Python's `json.dump` and send it with `gh api ... --input <path>`, then confirm with a
  GET. On Windows/Git Bash, `-f body=@path` posts the literal path string, not the file.
- **Review, then wait.** Review the diff yourself in one pass (see Sub-agents), and
  reproduce a problem before reporting it. Post one summary comment plus inline
  comments, then stop. Don't push fixes until the user approves them, and leave
  resolving threads to the user.
- **Never commit or push unless the user asks.** The user commits their own work.
