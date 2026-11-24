import io
import json
import logging
import os
from typing import TYPE_CHECKING, Any

import pytest
import structlog

from aft_mcp.audit import HANDLER_NAME, configure_logging
from aft_mcp.settings import Settings, load_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

type LogRecords = Callable[[], list[dict[str, Any]]]

ENV = {
    "AFT_MCP_BASE_URL": "https://aft.test",
    "AFT_MCP_SPEC_URL": "https://aft.test/api/apispec.json",
    "AFT_MCP_TIMEOUT": "5",
    "AFT_MCP_USERNAME": "agent@example.com",
    "AFT_MCP_PASSWORD": "secret",
}


@pytest.fixture
def aft_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set every required AFT_MCP_* setting, and clear every other one."""
    for name in [name for name in os.environ if name.startswith("AFT_MCP_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)  # set when tests run over SSH
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return ENV


@pytest.fixture
def settings(aft_env: dict[str, str]) -> Settings:
    """Settings loaded from ``aft_env`` alone, never from a ``.env`` file."""
    return load_settings(env_file=None)


def _op(**extra: Any) -> dict[str, Any]:
    return {"responses": {"200": {"description": "OK"}}, **extra}


CARD_ID = {"name": "card_id", "in": "path", "required": True, "type": "integer"}
GLOBAL_BOARD_ID = {
    "name": "global_board_id",
    "in": "path",
    "required": True,
    "type": "integer",
}

PROMOTE = {
    "name": "body",
    "in": "body",
    "required": True,
    "schema": {
        "type": "object",
        "properties": {
            "card_id": {"type": "integer"},
            "state": {"type": "string", "enum": ["new", "in_progress", "waiting"]},
        },
        "required": ["card_id"],
    },
}

MOVE = {
    "name": "body",
    "in": "body",
    "required": True,
    "schema": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["new", "in_progress", "waiting", "done"],
            },
            "after_card_id": {"type": "integer", "x-nullable": True},
        },
        "required": ["state"],
    },
}
BOARD_ID = {"name": "board_id", "in": "path", "required": True, "type": "integer"}
COLUMN_ID = {"name": "column_id", "in": "path", "required": True, "type": "integer"}
CARD_UPDATE = {
    "name": "body",
    "in": "body",
    "required": True,
    "schema": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "done": {"type": "boolean"}},
    },
}


@pytest.fixture
def anyio_backend() -> str:
    """Run the async tests (marked ``anyio``) on asyncio only."""
    return "asyncio"


@pytest.fixture
def aft_like_spec() -> dict[str, Any]:
    """A small Swagger 2.0 spec shaped like AFT's, with safe and unsafe routes.

    Every route here is one the allowlist either must or must not expose, so the
    server tests can assert on the exact set of tools built from it.
    """
    return {
        "swagger": "2.0",
        "info": {"title": "AFT", "version": "1"},
        "host": "aft.test",
        "basePath": "/",
        "schemes": ["https"],
        "paths": {
            "/api/boards": {"get": _op(), "post": _op()},
            "/api/boards/{board_id}": {
                "parameters": [BOARD_ID],
                "get": _op(),
                "patch": _op(),  # board-level rename: must not be exposed
                "delete": _op(),
            },
            "/api/boards/{board_id}/columns": {
                "parameters": [BOARD_ID],
                "get": _op(),
                "post": _op(),
            },
            "/api/columns/{column_id}": {
                "parameters": [COLUMN_ID],
                "patch": _op(),
                "delete": _op(),
            },
            "/api/columns/{column_id}/cards": {
                "parameters": [COLUMN_ID],
                "post": _op(),
            },
            "/api/cards/{card_id}": {
                "parameters": [CARD_ID],
                "get": _op(),
                "patch": _op(parameters=[CARD_UPDATE]),
                "delete": _op(),
            },
            "/api/cards/{card_id}/comments": {
                "parameters": [CARD_ID],
                "post": _op(),
            },
            "/api/database/backup": {"get": _op()},  # GET with a side effect
            "/api/global-boards": {"get": _op()},
            "/api/global-boards/{global_board_id}": {
                "parameters": [GLOBAL_BOARD_ID],
                "patch": _op(),  # global board settings: must not be exposed
            },
            "/api/global-boards/{global_board_id}/cards": {
                "parameters": [GLOBAL_BOARD_ID],
                "get": _op(),
                "post": _op(parameters=[PROMOTE]),
            },
            "/api/global-boards/{global_board_id}/cards/{card_id}": {
                "parameters": [GLOBAL_BOARD_ID, CARD_ID],
                "patch": _op(parameters=[MOVE]),
                "delete": _op(),  # demote: must not be exposed
            },
        },
    }


@pytest.fixture(autouse=True)
def log_records() -> Iterator[LogRecords]:
    """Send structlog to memory as JSON for each test; return a reader of the records.

    Without this, structlog's default would print to stdout.
    """
    root, fastmcp, ours = (logging.getLogger(n) for n in ("", "fastmcp", "aft_mcp"))
    saved = (root.level, fastmcp.handlers[:], fastmcp.propagate, ours.level)
    stream = io.StringIO()
    configure_logging("json", stream)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    for handler in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
        root.removeHandler(handler)
        handler.close()  # a syslog handler holds a socket
    root.level, fastmcp.handlers[:], fastmcp.propagate, ours.level = saved
