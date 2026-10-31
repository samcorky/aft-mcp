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


def build_route_maps() -> list[RouteMap]:
    """Expose GET endpoints as MCP tools and exclude everything else."""
    return [
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
