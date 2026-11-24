# Connecting clients

The server speaks MCP over stdio, so any MCP client launches it as a subprocess. There are two ways to give the client
a command to run.

## Where the settings live

The server reads its settings from the environment, then from one `.env` file:

- **By default**, `aft-mcp/.env` in your config directory: `~/.config/aft-mcp/.env` on Linux and macOS
  (`$XDG_CONFIG_HOME/aft-mcp/.env` if you set it), `%APPDATA%\aft-mcp\.env` on Windows. Make the directory readable
  only by you, and start the file from [`.env.example`](../.env.example).
- **`--env-file PATH`** (`aft-mcp --env-file ~/.config/aft-mcp/prod.env`), or `AFT_MCP_ENV_FILE` in the client config's
  environment, names another file instead, for example one per AFT instance (`prod.env`, `test.env`). It must exist. Set
  `AFT_MCP_INSTANCE=prod` (or `test`) in each, so the server's name, its instructions to the agent and every log line
  say which instance it is.
- **The working directory is never read**, so the server behaves the same whichever project the client starts it in.
  If an old `.env` is still there, the server says on stderr that it ignored it.

Without a `.env`, pass every setting as an environment variable in the client config instead. That puts the token in
the client config, so prefer the file.

### Secrets in files

On a server, the token or password can come from a file instead of `.env` or the environment. Point
`AFT_MCP_SECRETS_DIR` at a directory where each file is named after a variable and holds its value. The environment and
`.env` still win over it, and the directory must exist.

- **Docker:** mount a secret named `AFT_MCP_TOKEN` and set `AFT_MCP_SECRETS_DIR=/run/secrets`; see
  [Running aft-mcp with Docker](docker.md#secrets-in-files).
- **systemd:** `LoadCredential=AFT_MCP_TOKEN:/etc/aft-mcp/token` in the unit. systemd sets `CREDENTIALS_DIRECTORY`,
  which aft-mcp reads when `AFT_MCP_SECRETS_DIR` isn't set.

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

From the first release on PyPI, the version replaces the git ref: `uvx aft-mcp@2026.11.0`. Each release's files carry
a build-provenance attestation, so you can check where they were built.

## With Docker

Each release is also an image, `ghcr.io/samcorky/aft-mcp:<tag>`, for `linux/amd64` and `linux/arm64`. Mount your
settings file and point `AFT_MCP_ENV_FILE` at it:

```bash
claude mcp add aft -- docker run -i --rm \
  -v ~/.config/aft-mcp/.env:/config/aft-mcp.env:ro -e AFT_MCP_ENV_FILE=/config/aft-mcp.env \
  ghcr.io/samcorky/aft-mcp:<tag>
```

[Running aft-mcp with Docker](docker.md) covers tags, secrets, why not `--env-file`, checking a setup, Claude Desktop,
how the image is built, and building it yourself.

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
file, add `--env-file /path/to/prod.env` after `aft-mcp`, for example one MCP server per instance:

```bash
claude mcp add aft-prod -- uvx --from git+https://github.com/samcorky/aft-mcp@<ref> aft-mcp --env-file ~/.config/aft-mcp/prod.env
claude mcp add aft-test -- uvx --from git+https://github.com/samcorky/aft-mcp@<ref> aft-mcp --env-file ~/.config/aft-mcp/test.env
```

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
uvx`, or `where uvx` on Windows). To use another settings file, add `"--env-file", "/path/to/prod.env"` after
`"aft-mcp"` in `args`.

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

## On a server, over SSH

To keep the credential off workstations, run aft-mcp on a server and point the client at `ssh -T aft-broker prod`; see
[Running on a server](remote.md).

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
