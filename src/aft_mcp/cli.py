"""The ``aft-mcp`` command: serve over stdio, or check a setup before connecting.

With no arguments it starts the stdio server, as MCP clients expect. The other
commands print to stdout, which is safe because the server isn't running then; their
log lines go to stderr.
"""

import asyncio
from typing import TYPE_CHECKING, Any

import cyclopts
from pydantic import SecretStr

from aft_mcp import __version__, server
from aft_mcp.audit import configure_logging
from aft_mcp.settings import (
    GROUPS,
    AftMcpSettings,
    Settings,
    SettingsError,
    load_paths,
    load_settings,
)

if TYPE_CHECKING:
    from pathlib import Path

app = cyclopts.App(
    name="aft-mcp",
    help="An MCP server that gives AI agents safe, scoped access to AFT boards.",
    version=__version__,
)

MASK = "**********"
# A harmless read that needs a signed-in user and no board.
SIGN_IN_CHECK_PATH = "/api/auth/me"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _load() -> tuple[Settings, Path, Path | None]:
    paths = load_paths()
    env_file, secrets_dir = paths.env_file_path, paths.secrets_dir_path
    return load_settings(env_file, secrets_dir), env_file, secrets_dir


@app.default
def serve() -> None:
    """Start the MCP server over stdio (what MCP clients run)."""
    server.main()


@app.command(name="serve")
def serve_command() -> None:
    """Start the MCP server over stdio; the same as no command."""
    server.main()


@app.command
def version() -> None:
    """Print the version."""
    print(__version__)


@app.command
def check() -> int:
    """Check the settings, the spec and signing in to AFT, without changing anything.

    Returns:
        0 when every step passed, else 1.
    """
    try:
        settings, env_file, _ = _load()
    except SettingsError as error:
        _report(ok=False, step="settings", detail=str(error))
        return 1
    found = "found" if env_file.is_file() else "not found"
    _report(ok=True, step="settings", detail=f"{env_file} ({found})")
    configure_logging(settings.log.format)
    try:
        spec = server.load_spec(settings)
    except Exception as error:
        _report(ok=False, step="spec", detail=_describe_error(error))
        return 1
    _report(ok=True, step="spec", detail=f"{len(spec.get('paths', {}))} paths")
    status = asyncio.run(_sign_in_status(settings))
    if status != 200:
        _report(ok=False, step="sign in", detail=f"{SIGN_IN_CHECK_PATH} -> {status}")
        return 1
    _report(ok=True, step="sign in", detail=f"{SIGN_IN_CHECK_PATH} -> 200")
    return 0


async def _sign_in_status(settings: Settings) -> int | str:
    async with server.build_client(settings) as client:
        try:
            response = await client.get(SIGN_IN_CHECK_PATH)
        except Exception as error:
            return _describe_error(error)
        return response.status_code


def _describe_error(error: Exception) -> str:
    if isinstance(error, SettingsError):
        return "; ".join(error.problems)
    return f"{type(error).__name__}: {error}"


def _report(*, ok: bool, step: str, detail: str) -> None:
    print(f"{'ok' if ok else 'FAILED':6} {step:8} {detail}")


@app.command
def tools() -> int:
    """List the tools the current settings expose: method, path, name, and writes.

    Fetches the spec but makes no other call to AFT.

    Returns:
        0, or 1 when the settings or spec can't be loaded.
    """
    try:
        settings, _, _ = _load()
        configure_logging(settings.log.format)
        mcp = server.create_server(settings)
    except SettingsError as error:
        print(error)
        return 1
    rows = []
    for tool in asyncio.run(mcp.list_tools()):
        route = getattr(tool, "_route", None)
        method = getattr(route, "method", "?")
        path = getattr(route, "path", "?")
        rows.append((path, method, tool.name))
    for path, method, name in sorted(rows):
        mark = "  (write)" if method in WRITE_METHODS else ""
        print(f"{method:6} {path}  {name}{mark}")
    return 0


@app.command(name="settings")
def show_settings() -> int:
    """Show each setting's value and where it came from, with secrets masked.

    Returns:
        0, or 1 when the settings are invalid.
    """
    try:
        settings, env_file, secrets_dir = _load()
    except SettingsError as error:
        print(error)
        return 1
    for attribute, group in GROUPS.items():
        print(f"# {group.title}")
        values: AftMcpSettings = getattr(settings, attribute)
        for field in group.model_fields:
            variable = group.env_name(field)
            value = _shown(getattr(values, field))
            where = _where(values.source_of(field), variable, env_file, secrets_dir)
            print(f"{variable:28} {value:32} {where}")
    return 0


def _where(source: str, variable: str, env_file: Path, secrets: Path | None) -> str:
    """Name a tracked source (``env``, ``dotenv``, ``secret_file``) for people."""
    if source == "env":
        return "environment"
    if source == "dotenv":
        return str(env_file)
    if source == "secret_file" and secrets is not None:
        return str(secrets / variable)
    return source


def _shown(value: Any) -> str:  # noqa: ANN401 - any setting's value
    if value is None:
        return "unset"
    if isinstance(value, SecretStr):
        return MASK
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def main() -> None:
    """Run the ``aft-mcp`` command."""
    app()
