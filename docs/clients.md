# Connecting clients

The server speaks MCP over stdio, so any MCP client launches it as a subprocess. There are two ways to give the client
a command to run.

## Where the settings live

The server reads its settings from the environment, then from one `.env` file:

- **By default**, `aft-mcp/.env` in your config directory: `~/.config/aft-mcp/.env` on Linux and macOS
  (`$XDG_CONFIG_HOME/aft-mcp/.env` if you set it), `%APPDATA%\aft-mcp\.env` on Windows. Make the directory readable
  only by you, and start the file from [`.env.example`](../.env.example).
- **`AFT_MCP_ENV_FILE`**, set in the client config's environment, names another file instead, for example one per
  AFT instance (`prod.env`, `test.env`). It must exist.
- **The working directory is never read**, so the server behaves the same whichever project the client starts it in.
  If an old `.env` is still there, the server says on stderr that it ignored it.

Without a `.env`, pass every setting as an environment variable in the client config instead. That puts the token in
the client config, so prefer the file.

## With uvx (no checkout)

[`uvx`](https://docs.astral.sh/uv/guides/tools/) (part of uv) installs aft-mcp into a cached, isolated environment and
runs it, so there is nothing to clone or keep in sync:

```bash
uvx --from git+https://github.com/samcorky/aft-mcp@<ref> aft-mcp
```

- **Pin `<ref>`** to a release tag (such as `v2026.11.0`) or a commit, never a branch. uvx installs the pinned version
  once and reuses it, so the code only changes when you change the ref, after reading what changed. `@main` would
  give a different server whenever `main` moves.
- **Upgrading** is changing the ref in the client config and restarting the client. `uvx --refresh ...` rebuilds the
  cached environment if it ever needs it.

Once aft-mcp is published to PyPI ([#21](https://github.com/samcorky/aft-mcp/issues/21)), `--from git+...` becomes
`uvx aft-mcp@2026.11.0`.

## From a checkout

For development, or to run code you have reviewed locally, point the client at your clone. It reads the same settings
file as above:

```bash
uv run --directory /path/to/aft-mcp aft-mcp
```

## Claude Code

```bash
claude mcp add aft -- uvx --from git+https://github.com/samcorky/aft-mcp@<ref> aft-mcp
```

Add `--scope user` to make it available in every project. Check it with `claude mcp list` or `/mcp` inside a session.
From a checkout, use `claude mcp add aft -- uv run --directory /path/to/aft-mcp aft-mcp`. To use another settings
file, add `-e AFT_MCP_ENV_FILE=/path/to/prod.env` before the `--`.

## Claude Desktop

Add this to `claude_desktop_config.json` (Settings, then Developer, then Edit Config) and restart the app:

```json
{
  "mcpServers": {
    "aft": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/samcorky/aft-mcp@<ref>", "aft-mcp"]
    }
  }
}
```

Claude Desktop may not see the `PATH` of your shell; if it can't find `uvx`, give its full path as `command` (`which
uvx`, or `where uvx` on Windows). To use another settings file, add `"env": {"AFT_MCP_ENV_FILE": "/path/to/prod.env"}`.

## Claude Agent SDK

```python
from claude_agent_sdk import ClaudeAgentOptions, query

options = ClaudeAgentOptions(
    mcp_servers={
        "aft": {
            "command": "uvx",
            "args": [
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

## Other MCP clients

Point the client at the same command over stdio: the `uvx` one above, or `uv run --directory /path/to/aft-mcp aft-mcp`
from a checkout.

## Tool names and permissions

Clients prefix tool names with the server name, so `get_boards` appears as `mcp__aft__get_boards`. Use that form in
permission rules. To keep an agent read-only, allow only the `get_*` tools:

```json
{ "permissions": { "allow": ["mcp__aft__get_*"] } }
```

The write tools are `patch_*`, `post_*` and `put_*`. To enforce this in the server rather than the client, set
`AFT_MCP_READ_ONLY=true`: the write tools are then never created (see [Access and auditing](access.md#read-only-mode)).
