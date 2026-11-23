import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import structlog
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

from aft_mcp import __version__
from aft_mcp.audit import AuditLog, configure_logging, log_response
from aft_mcp.board_guard import BoardGuard
from aft_mcp.settings import (
    ENV_FILE_VARIABLE,
    NO_SIGN_IN,
    Settings,
    SettingsError,
    env_file_path,
    load_settings,
    url_origin,
    url_scheme,
)
from aft_mcp.swagger_convert import JSON, convert

log = structlog.get_logger("aft_mcp.server")

if TYPE_CHECKING:
    from collections.abc import Generator

    from fastmcp.utilities.openapi.models import HttpMethod
    from pydantic import SecretStr


def load_spec_from_file(path: Path) -> JSON:
    """Load the AFT API specification from disk."""
    with path.open(encoding="utf-8") as f:
        spec: JSON = json.load(f)
    return spec


def load_spec_from_url(url: str, timeout: float, *, allow_insecure: bool) -> JSON:
    """Fetch the AFT API specification over HTTP.

    Redirects are followed only while they stay on the same host and port, and on
    https unless ``allow_insecure``: the spec writes every tool's description, so it
    must come from the AFT the tools call.

    Raises:
        SettingsError: A redirect left the host, or dropped to http.
    """
    response = httpx2.get(url, timeout=timeout, follow_redirects=True)
    final = str(response.url)
    if url_origin(final) != url_origin(url):
        raise SettingsError(
            [f"the spec at {url} redirected to another host or port: {final}"]
        )
    if url_scheme(final) != "https" and not allow_insecure:
        raise SettingsError([f"the spec at {url} redirected to http: {final}"])
    response.raise_for_status()
    spec: JSON = response.json()
    return spec


def load_spec(settings: Settings) -> JSON:
    """Load the spec as OpenAPI 3: from the spec file if set, else over HTTP."""
    if settings.spec_file is not None:
        spec = load_spec_from_file(settings.spec_file)
    else:
        spec = load_spec_from_url(
            settings.spec_location,
            settings.timeout,
            allow_insecure=settings.allow_insecure,
        )
    return convert(spec)


class BearerAuth(httpx2.Auth):
    """Send an AFT API token as ``Authorization: Bearer <token>``.

    The token stays a ``SecretStr`` until a request is sent, so it never shows in a
    repr of the client or its auth.
    """

    def __init__(self, token: SecretStr) -> None:
        self._token = token

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response]:
        """Add the token to the request; AFT needs no challenge round trip."""
        request.headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        yield request


def build_auth(settings: Settings) -> httpx2.Auth:
    """The API token if one is set, otherwise HTTP Basic auth."""
    if settings.token is not None:
        return BearerAuth(settings.token)
    if settings.username is None or settings.password is None:
        # Settings validation rules this out; checked again so it can never send
        # an empty password.
        raise SettingsError([NO_SIGN_IN])
    return httpx2.BasicAuth(settings.username, settings.password.get_secret_value())


def build_client(
    settings: Settings,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> httpx2.AsyncClient:
    """Create the HTTP client used to call the AFT API.

    Args:
        settings: The server settings.
        transport: Replaces the network transport; the tests pass a mock here.
    """
    if settings.board_ids:
        transport = BoardGuard(
            transport or httpx2.AsyncHTTPTransport(), settings.board_ids
        )
    return httpx2.AsyncClient(
        base_url=str(settings.base_url),
        timeout=settings.timeout,
        auth=build_auth(settings),
        transport=transport,
        event_hooks={"response": [log_response]},
    )


# GET endpoints that change state, so must not be exposed as read-only tools.
SIDE_EFFECT_GET_PATTERNS = [
    r"^/api/database/backup$",  # creates a database backup
]


# Write operations allowed as tools: changes to things inside a board that can be
# undone, plus adding columns and cards. No creating or deleting boards, no deleting
# columns or cards, and nothing board-level (rename, owner, public link, import).
# DELETE is never exposed.
ALLOWED_WRITES: list[tuple[HttpMethod, str]] = [
    # Edit title/description, move between columns.
    ("PATCH", r"^/api/cards/\{card_id\}$"),
    ("PATCH", r"^/api/cards/\{card_id\}/done$"),
    ("PATCH", r"^/api/cards/\{card_id\}/archive$"),
    ("PATCH", r"^/api/cards/\{card_id\}/unarchive$"),
    ("POST", r"^/api/cards/batch/archive$"),
    ("POST", r"^/api/cards/batch/unarchive$"),
    ("PUT", r"^/api/cards/\{card_id\}/assignees$"),
    ("POST", r"^/api/cards/\{card_id\}/comments$"),
    ("POST", r"^/api/cards/\{card_id\}/checklist-items$"),
    ("PATCH", r"^/api/checklist-items/\{item_id\}$"),
    # Rename / reorder a column.
    ("PATCH", r"^/api/columns/\{column_id\}$"),
    # Add a column or a card. There is no MCP delete to undo these.
    ("POST", r"^/api/boards/\{board_id\}/columns$"),
    ("POST", r"^/api/columns/\{column_id\}/cards$"),
]


def build_route_maps(*, read_only: bool) -> list[RouteMap]:
    """Expose safe GET endpoints and allowed writes as tools; exclude the rest.

    Args:
        read_only: Leave out every write, so only the safe GET endpoints remain.
    """
    writes = [] if read_only else ALLOWED_WRITES
    return [
        *(
            RouteMap(methods=["GET"], pattern=pattern, mcp_type=MCPType.EXCLUDE)
            for pattern in SIDE_EFFECT_GET_PATTERNS
        ),
        *(
            RouteMap(methods=[method], pattern=pattern, mcp_type=MCPType.TOOL)
            for method, pattern in writes
        ),
        RouteMap(methods=["GET"], mcp_type=MCPType.TOOL),
        RouteMap(mcp_type=MCPType.EXCLUDE),
    ]


def create_server(settings: Settings) -> FastMCP:
    """Assemble the MCP server from the OpenAPI spec, with the audit log."""
    mcp = FastMCP.from_openapi(
        openapi_spec=load_spec(settings),
        client=build_client(settings),
        name="AFT",
        version=__version__,
        route_maps=build_route_maps(read_only=settings.read_only),
    )
    mcp.add_middleware(AuditLog())
    return mcp


def warn_about_working_directory_env(env_file: Path) -> None:
    """Say on stderr that a ``.env`` in the working directory is not read.

    Before settings moved to the config directory, the server read ``./.env``. This
    points anyone upgrading at the new place instead of failing silently.
    """
    local = Path(".env")
    if local.is_file() and local.resolve() != env_file.resolve():
        print(
            "aft-mcp: ignoring .env in the working directory; settings are read "
            + f"from {env_file} (set {ENV_FILE_VARIABLE} to use another file)",
            file=sys.stderr,
        )


def main() -> None:
    """Load the settings, build the server and serve it over stdio."""
    try:
        env_file = env_file_path()
        settings = load_settings(env_file)
    except SettingsError as error:
        sys.exit(str(error))  # exits with status 1, the message on stderr
    warn_about_working_directory_env(env_file)
    configure_logging(settings.log_format)
    for name in settings.insecure_urls:
        log.warning("insecure_url", setting=name, note="credentials sent unencrypted")
    try:
        mcp = create_server(settings)
    except SettingsError as error:
        sys.exit(str(error))
    mcp.run()
