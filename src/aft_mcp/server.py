import json
import sys
from typing import TYPE_CHECKING

import httpx2
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

from aft_mcp import __version__
from aft_mcp.audit import AuditLog, configure_audit_log, record_status
from aft_mcp.board_guard import BoardGuard
from aft_mcp.settings import Settings, SettingsError, env_file_path, load_settings
from aft_mcp.swagger_convert import JSON, convert

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp.utilities.openapi.models import HttpMethod


def load_spec_from_file(path: Path) -> JSON:
    """Load the AFT API specification from disk."""
    with path.open(encoding="utf-8") as f:
        spec: JSON = json.load(f)
    return spec


def load_spec_from_url(url: str, timeout: float) -> JSON:
    """Fetch the AFT API specification over HTTP."""
    response = httpx2.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    spec: JSON = response.json()
    return spec


def load_spec(settings: Settings) -> JSON:
    """Load the spec as OpenAPI 3, from AFT_SPEC_FILE if set, else AFT_SPEC_URL."""
    if settings.spec_file is not None:
        spec = load_spec_from_file(settings.spec_file)
    else:
        spec = load_spec_from_url(str(settings.spec_url), settings.timeout)
    return convert(spec)


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
        auth=(settings.username, settings.password.get_secret_value()),
        transport=transport,
        event_hooks={"response": [record_status]},
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


def main() -> None:
    """Load the settings, build the server and serve it over stdio."""
    try:
        settings = load_settings(env_file_path())
    except SettingsError as error:
        sys.exit(str(error))  # exits with status 1, the message on stderr
    configure_audit_log()
    create_server(settings).run()
