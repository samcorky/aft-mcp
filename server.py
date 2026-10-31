import json
import os
from pathlib import Path

import httpx2
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

from swagger_convert import convert

load_dotenv()


def require_env(name: str) -> str:
    """Return a required setting from the environment (.env), or fail clearly."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required setting {name}; set it in .env")
    return value


def load_spec_from_file(path: Path) -> dict:
    """Load the AFT API specification from disk."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_spec_from_url(url: str, timeout: float) -> dict:
    """Fetch the AFT API specification over HTTP."""
    response = httpx2.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def load_spec() -> dict:
    """Load the spec from AFT_SPEC_FILE if set, otherwise AFT_SPEC_URL, as OpenAPI 3."""
    spec_file = os.getenv("AFT_SPEC_FILE")
    if spec_file:
        spec = load_spec_from_file(Path(spec_file))
    else:
        spec = load_spec_from_url(
            require_env("AFT_SPEC_URL"), float(require_env("AFT_TIMEOUT"))
        )
    return convert(spec)


def build_client() -> httpx2.AsyncClient:
    """Create the HTTP client used to call the AFT API."""
    return httpx2.AsyncClient(
        base_url=require_env("AFT_BASE_URL"),
        timeout=float(require_env("AFT_TIMEOUT")),
        auth=(require_env("AFT_USERNAME"), require_env("AFT_PASSWORD")),
    )


# GET endpoints that change state, so must not be exposed as read-only tools.
SIDE_EFFECT_GET_PATTERNS = [
    r"^/api/database/backup$",  # creates a database backup
]


# Write operations allowed as tools: changes to things inside a board that can be
# undone, plus adding columns and cards. No creating or deleting boards, no deleting columns or cards, and nothing board-level
# (rename, owner, public link, import). DELETE is never exposed.
ALLOWED_WRITES = [
    ("PATCH", r"^/api/cards/\{card_id\}$"),  # edit title/description, move between columns
    ("PATCH", r"^/api/cards/\{card_id\}/done$"),
    ("PATCH", r"^/api/cards/\{card_id\}/archive$"),
    ("PATCH", r"^/api/cards/\{card_id\}/unarchive$"),
    ("POST", r"^/api/cards/batch/archive$"),
    ("POST", r"^/api/cards/batch/unarchive$"),
    ("PUT", r"^/api/cards/\{card_id\}/assignees$"),
    ("POST", r"^/api/cards/\{card_id\}/comments$"),
    ("POST", r"^/api/cards/\{card_id\}/checklist-items$"),
    ("PATCH", r"^/api/checklist-items/\{item_id\}$"),
    ("PATCH", r"^/api/columns/\{column_id\}$"),  # rename / reorder
    ("POST", r"^/api/boards/\{board_id\}/columns$"),  # add a column (no MCP delete to undo it)
    ("POST", r"^/api/columns/\{column_id\}/cards$"),  # add a card (no MCP delete to undo it)
]


def build_route_maps() -> list[RouteMap]:
    """Expose safe GET endpoints and allowed writes as MCP tools; exclude everything else."""
    return [
        *(
            RouteMap(methods=["GET"], pattern=pattern, mcp_type=MCPType.EXCLUDE)
            for pattern in SIDE_EFFECT_GET_PATTERNS
        ),
        *(
            RouteMap(methods=[method], pattern=pattern, mcp_type=MCPType.TOOL)
            for method, pattern in ALLOWED_WRITES
        ),
        RouteMap(methods=["GET"], mcp_type=MCPType.TOOL),
        RouteMap(mcp_type=MCPType.EXCLUDE),
    ]


def create_server() -> FastMCP:
    """Assemble the MCP server from the OpenAPI spec."""
    return FastMCP.from_openapi(
        openapi_spec=load_spec(),
        client=build_client(),
        name="AFT",
        route_maps=build_route_maps(),
    )


mcp = create_server()

if __name__ == "__main__":
    mcp.run()
