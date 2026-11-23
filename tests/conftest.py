from typing import Any

import pytest

ENV = {
    "AFT_BASE_URL": "https://aft.test",
    "AFT_SPEC_URL": "https://aft.test/api/apispec.json",
    "AFT_TIMEOUT": "5",
    "AFT_USERNAME": "agent@example.com",
    "AFT_PASSWORD": "secret",
}


@pytest.fixture
def aft_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set every required AFT_* setting, and make sure AFT_SPEC_FILE is unset."""
    monkeypatch.delenv("AFT_SPEC_FILE", raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return ENV


def _op(**extra: Any) -> dict[str, Any]:
    return {"responses": {"200": {"description": "OK"}}, **extra}


CARD_ID = {"name": "card_id", "in": "path", "required": True, "type": "integer"}
BOARD_ID = {"name": "board_id", "in": "path", "required": True, "type": "integer"}
COLUMN_ID = {"name": "column_id", "in": "path", "required": True, "type": "integer"}
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
            "/api/database/backup": {"get": _op()},  # GET with a side effect
        },
    }
