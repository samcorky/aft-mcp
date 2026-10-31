# aft-mcp

An [MCP](https://modelcontextprotocol.io) server for the AFT API. It fetches AFT's Swagger 2.0 spec, converts it to
OpenAPI 3, and exposes every `GET` endpoint as an MCP tool using [FastMCP](https://gofastmcp.com).

Only read-only (`GET`) endpoints are exposed; everything else is excluded.

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

## Running

```bash
uv run python server.py     # stdio transport
```

To use it from Claude Code:

```bash
claude mcp add aft -- uv run --directory /path/to/aft-mcp python server.py
```

Other MCP clients need the same command in their server config.

## How it works

- `server.py` loads config, fetches the spec, builds an authenticated HTTP client, and creates the FastMCP server.
- `swagger_convert.py` is a small Swagger 2.0 to OpenAPI 3 converter. It handles body and form parameters, response
  schemas, `definitions`, and basic auth. Specs that are already OpenAPI 3 pass through unchanged.

## Notes

- Credentials are held by the server, so every tool call acts as that one user. This is meant for local, single-user
  use over stdio.
- Only the `GET` conversion path has been exercised against a live API.
