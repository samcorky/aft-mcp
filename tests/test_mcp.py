"""Tests that talk to the server the way an MCP client does.

The in-memory tests connect a real FastMCP client to the server built by
``server.create_server()`` and check the HTTP requests each tool call sends to AFT,
which is stubbed with ``httpx2.MockTransport``. The ``e2e`` test launches
``python server.py`` over stdio, as Claude Code or Claude Desktop would, against a
local HTTP server standing in for AFT.
"""

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parent.parent
BOARDS = [{"id": 12, "name": "Development"}]


class FakeAft:
    """Answers the server's HTTP calls and records each request."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.status = 200
        self.payload: Any = BOARDS

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, json=self.payload)


@pytest.fixture
def fake_aft() -> FakeAft:
    return FakeAft()


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    aft_env: dict[str, str],
    aft_like_spec: dict[str, Any],
    fake_aft: FakeAft,
) -> AsyncIterator[Client[Any]]:
    """An MCP client connected in memory to the real server, with AFT stubbed."""
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(aft_like_spec), encoding="utf-8")
    monkeypatch.setenv("AFT_SPEC_FILE", str(spec_file))
    real_build_client = server.build_client
    transport = httpx2.MockTransport(fake_aft)
    monkeypatch.setattr(server, "build_client", lambda: real_build_client(transport))

    async with Client(server.create_server()) as connected:
        yield connected


def basic_auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


# --- connecting -------------------------------------------------------------------


async def test_handshake_reports_the_server_and_its_tools(
    client: Client[Any],
) -> None:
    assert client.server_info is not None
    assert client.server_info.name == "AFT"
    assert client.server_capabilities is not None
    assert client.server_capabilities.tools is not None


async def test_lists_only_the_allowed_tools(client: Client[Any]) -> None:
    names = {tool.name for tool in await client.list_tools()}
    assert "get_boards" in names
    assert "patch_cards_by_card_id" in names
    assert not {n for n in names if n.startswith("delete_")}


async def test_tool_schema_flattens_path_and_body_arguments(
    client: Client[Any],
) -> None:
    tools = {tool.name: tool for tool in await client.list_tools()}
    schema = tools["patch_cards_by_card_id"].input_schema
    assert schema["required"] == ["card_id"]
    assert set(schema["properties"]) == {"card_id", "title", "done"}


# --- calling tools ----------------------------------------------------------------


async def test_read_tool_calls_aft_with_basic_auth(
    client: Client[Any], fake_aft: FakeAft, aft_env: dict[str, str]
) -> None:
    result = await client.call_tool("get_boards", {})

    assert result.structured_content == {"result": BOARDS}
    (request,) = fake_aft.requests
    assert request.method == "GET"
    assert str(request.url) == "https://aft.test/api/boards"
    assert request.headers["Authorization"] == basic_auth(
        aft_env["AFT_USERNAME"], aft_env["AFT_PASSWORD"]
    )


async def test_write_tool_sends_path_params_and_a_json_body(
    client: Client[Any], fake_aft: FakeAft
) -> None:
    fake_aft.payload = {"id": 5, "title": "Renamed"}

    result = await client.call_tool(
        "patch_cards_by_card_id", {"card_id": 5, "title": "Renamed"}
    )

    assert result.data == {"id": 5, "title": "Renamed"}
    (request,) = fake_aft.requests
    assert request.method == "PATCH"
    assert request.url.path == "/api/cards/5"
    assert json.loads(request.content) == {"title": "Renamed"}


async def test_promote_card_to_global_board(
    client: Client[Any], fake_aft: FakeAft
) -> None:
    await client.call_tool(
        "post_global_boards_cards", {"global_board_id": 3, "card_id": 5}
    )

    (request,) = fake_aft.requests
    assert request.method == "POST"
    assert request.url.path == "/api/global-boards/3/cards"
    assert json.loads(request.content) == {"card_id": 5}


async def test_move_card_on_global_board(
    client: Client[Any], fake_aft: FakeAft
) -> None:
    await client.call_tool(
        "patch_global_boards_cards_by_card_id",
        {"global_board_id": 3, "card_id": 5, "state": "done", "after_card_id": 7},
    )

    (request,) = fake_aft.requests
    assert request.method == "PATCH"
    assert request.url.path == "/api/global-boards/3/cards/5"
    assert json.loads(request.content) == {"state": "done", "after_card_id": 7}


@pytest.mark.parametrize(
    "tool",
    ["delete_global_boards_cards_by_card_id", "patch_global_boards_by_global_board_id"],
)
async def test_global_board_demote_and_settings_are_not_tools(
    client: Client[Any], fake_aft: FakeAft, tool: str
) -> None:
    result = await client.call_tool(
        tool, {"global_board_id": 3, "card_id": 5}, raise_on_error=False
    )

    assert result.is_error
    assert fake_aft.requests == []


async def test_excluded_tool_is_unknown_and_never_reaches_aft(
    client: Client[Any], fake_aft: FakeAft
) -> None:
    result = await client.call_tool(
        "delete_cards_by_card_id", {"card_id": 5}, raise_on_error=False
    )

    assert result.is_error
    assert "Unknown tool" in result.content[0].text  # type: ignore[union-attr]
    assert fake_aft.requests == []


async def test_aft_error_comes_back_as_a_tool_error(
    client: Client[Any], fake_aft: FakeAft
) -> None:
    fake_aft.status = 403
    fake_aft.payload = {"error": "Forbidden"}

    result = await client.call_tool("get_boards", {}, raise_on_error=False)

    assert result.is_error
    assert "403" in result.content[0].text  # type: ignore[union-attr]


# --- end to end over stdio --------------------------------------------------------


class AftHandler(BaseHTTPRequestHandler):
    """A stand-in AFT that serves the boards list to the configured user only."""

    expected_auth = ""
    seen: list[tuple[str, str]]

    def do_GET(self) -> None:
        self.seen.append((self.path, self.headers.get("Authorization", "")))
        if self.headers.get("Authorization") != self.expected_auth:
            self._send(401, {"error": "unauthorised"})
        elif self.path == "/api/boards":
            self._send(200, BOARDS)
        else:
            self._send(404, {"error": "not found"})

    def _send(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the test output quiet."""


@pytest.fixture
def local_aft(aft_env: dict[str, str]) -> Iterator[tuple[str, list[tuple[str, str]]]]:
    """Serve a fake AFT on a free local port; yield its URL and the requests seen."""
    seen: list[tuple[str, str]] = []
    handler = type(
        "Handler",
        (AftHandler,),
        {
            "seen": seen,
            "expected_auth": basic_auth(
                aft_env["AFT_USERNAME"], aft_env["AFT_PASSWORD"]
            ),
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.e2e
async def test_stdio_server_lists_tools_and_calls_aft(
    tmp_path: Path,
    aft_env: dict[str, str],
    aft_like_spec: dict[str, Any],
    local_aft: tuple[str, list[tuple[str, str]]],
) -> None:
    base_url, seen = local_aft
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(aft_like_spec), encoding="utf-8")
    # Every setting is passed explicitly, so a developer's own .env never applies:
    # load_dotenv() does not override variables that are already set.
    env = {**aft_env, "AFT_BASE_URL": base_url, "AFT_SPEC_FILE": str(spec_file)}
    transport = StdioTransport(
        command=sys.executable, args=["server.py"], env=env, cwd=str(REPO_ROOT)
    )

    async with Client(transport) as stdio_client:
        names = {tool.name for tool in await stdio_client.list_tools()}
        result = await stdio_client.call_tool("get_boards", {})

    assert "get_boards" in names
    assert not {n for n in names if n.startswith("delete_")}
    assert result.structured_content == {"result": BOARDS}
    auth = basic_auth(aft_env["AFT_USERNAME"], aft_env["AFT_PASSWORD"])
    assert seen == [("/api/boards", auth)]
