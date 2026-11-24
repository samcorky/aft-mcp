# Running aft-mcp with Docker

Each release is published as an image on GHCR, for `linux/amd64` and `linux/arm64`:

```text
ghcr.io/samcorky/aft-mcp:<tag>
```

MCP clients start aft-mcp themselves and talk to it over stdin and stdout, so there is no long-running container: the
client runs `docker run -i --rm ...` for each session. `-i` keeps stdin open, which the stdio transport needs.

## Which tag

| Tag                       | What it is                                                                 |
|---------------------------|----------------------------------------------------------------------------|
| `2026.11.0`               | One release. Pin this, and change it on purpose after reading what changed |
| `2026.11`                 | The newest release of that month                                           |
| `2026`                    | The newest release of that year                                            |
| `latest`                  | The newest release                                                         |
| `edge`                    | The newest build of `main`; not a release                                  |
| `sha-<commit>`            | The build of one commit on `main`                                          |

For a setup you rely on, pin a release, or pin by digest (`ghcr.io/samcorky/aft-mcp@sha256:...`).

## Settings

The image has no settings of its own. Mount your settings file read-only and tell aft-mcp where it is:

```bash
docker run -i --rm \
  -v ~/.config/aft-mcp/.env:/config/aft-mcp.env:ro \
  -e AFT_MCP_ENV_FILE=/config/aft-mcp.env \
  ghcr.io/samcorky/aft-mcp:<tag>
```

aft-mcp reads the file itself, so it's parsed exactly as outside Docker, and only its path is in the container's
configuration: `docker inspect` shows `AFT_MCP_ENV_FILE`, not your token. See [Settings](settings.md) for every
variable. `AFT_MCP_ENV_FILE` is needed because the default location (`~/.config/aft-mcp/.env`) doesn't apply inside the
container. aft-mcp's own `--env-file` option does the same after the image name (`... ghcr.io/samcorky/aft-mcp:<tag>
--env-file /config/aft-mcp.env`), but `-e AFT_MCP_ENV_FILE` is used here so it isn't mistaken for Docker's `--env-file`.

The image runs as user `1000:1000`. A settings file with mode `600` (readable only by its owner) is readable in the
container only if its owner is also uid 1000, which is usually the first user on a Linux machine (`id -u` shows yours).
Otherwise make the file readable by a group and add `--group-add <gid>`, or use a secret file (below).

### Secrets in files

To keep the token out of the settings file too, mount it as its own file in a directory and point
`AFT_MCP_SECRETS_DIR` at that directory. The file is named after the variable:

```bash
docker run -i --rm \
  -v ~/.config/aft-mcp/.env:/config/aft-mcp.env:ro \
  -v ~/.config/aft-mcp/token:/run/secrets/AFT_MCP_TOKEN:ro \
  -e AFT_MCP_ENV_FILE=/config/aft-mcp.env \
  -e AFT_MCP_SECRETS_DIR=/run/secrets \
  ghcr.io/samcorky/aft-mcp:<tag>
```

With Compose, a `secrets:` entry named `AFT_MCP_TOKEN` lands in `/run/secrets/AFT_MCP_TOKEN` the same way.

### `--env-file`

`docker run --env-file prod.env` also works, but isn't recommended:

- Docker copies every value into the container's configuration, so anyone who can run `docker inspect` sees the token.
- Docker parses the file itself, not as aft-mcp does: quotes stay part of the value (`"agent@example.com"` keeps its
  quotes), and `export` lines don't work.

## Check a setup

Before connecting a client, run the commands that print to the terminal. They make no change in AFT:

```bash
docker run --rm -v ~/.config/aft-mcp/.env:/config/aft-mcp.env:ro -e AFT_MCP_ENV_FILE=/config/aft-mcp.env \
  ghcr.io/samcorky/aft-mcp:<tag> check
```

`check` loads the settings, fetches the spec and signs in with one harmless read. Replace it with `tools` to list the
tools your settings expose, `settings` to see each value and where it came from (secrets masked), or `version`.

## Connecting a client

**Claude Code:**

```bash
claude mcp add aft -- docker run -i --rm \
  -v ~/.config/aft-mcp/.env:/config/aft-mcp.env:ro -e AFT_MCP_ENV_FILE=/config/aft-mcp.env \
  ghcr.io/samcorky/aft-mcp:<tag>
```

**Claude Desktop** (`claude_desktop_config.json`), with the full path to your settings file, since `~` isn't expanded:

```json
{
  "mcpServers": {
    "aft": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/home/you/.config/aft-mcp/.env:/config/aft-mcp.env:ro",
        "-e", "AFT_MCP_ENV_FILE=/config/aft-mcp.env",
        "ghcr.io/samcorky/aft-mcp:<tag>"
      ]
    }
  }
}
```

The log goes to stderr, which the client keeps; see [Access and auditing](access.md#audit-log).

## How the image is built

The `Dockerfile` follows menagerist's:

1. **Builder:** `debian:trixie-slim`, with the `uv` binary copied from the `ghcr.io/astral-sh/uv` image.
   `uv python install` installs the Python named in `.python-version`.
2. **Dependencies:** `uv sync --locked --no-install-project` installs exactly what `uv.lock` records, checking every
   package's hash, and fails if the lock doesn't match `pyproject.toml`. This layer is cached until `uv.lock` changes.
3. **aft-mcp itself:** the source (`src/`) is copied in and `uv sync --locked --no-editable --reinstall-package aft-mcp`
   builds it and installs it as an ordinary package in the virtualenv (`/app/.venv`), not linked to the source. The
   version comes from a `VERSION` build argument, worked out from the git tags by calver-scm, since the repository
   isn't in the image. `--reinstall-package` makes sure it is rebuilt from this source rather than taken from uv's
   cache.
4. **Trimming:** parts of Python a stdio server never uses (tkinter, idlelib, pip, tests) are removed.
5. **Runtime:** only that Python (`/opt/python`) and the virtualenv are copied onto
   `gcr.io/distroless/base-debian13:nonroot`, plus `libgcc_s`, which pydantic-core needs. There is no shell, package
   manager or pip, and it runs as `1000:1000`.

Base images are pinned by digest, and Renovate updates them. Releases are attested: verify an image with
`gh attestation verify oci://ghcr.io/samcorky/aft-mcp:<tag> --owner samcorky`.

## Building it yourself

From a checkout, with Docker and uv:

```bash
uv run poe docker-build
```

That runs `docker buildx bake local` (`docker-bake.hcl`) with the version, commit and time worked out for you, and
tags the result `aft-mcp:local` for your machine's platform. Use `aft-mcp:local` in place of
`ghcr.io/samcorky/aft-mcp:<tag>` in the commands above. Without `poe`, `docker buildx bake local` builds the same image
with the version `0.0.0`; set `VERSION=2026.11.0` (and optionally `COMMIT_SHA`, `BUILD_TIMESTAMP`) in the environment
to label it.

The build context is only what the image needs (`.dockerignore`): never your `.env` or any other file in the checkout.
