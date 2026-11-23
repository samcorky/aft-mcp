import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx2
import mcp_types as mt
import pytest
from fastmcp.server.middleware import MiddlewareContext

from aft_mcp import audit

if TYPE_CHECKING:
    from fastmcp.tools.base import ToolResult

pytestmark = pytest.mark.anyio

STARTED = datetime(2026, 11, 23, 12, 0, 1, tzinfo=UTC)


def call(name: str, arguments: dict[str, Any] | None = None) -> MiddlewareContext[Any]:
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name=name, arguments=arguments),
        timestamp=STARTED,
    )


def test_the_line_has_the_time_tool_ids_and_status() -> None:
    context = call("patch_cards_done", {"card_id": 42, "done": True})
    assert audit.describe(context, [200], failed=False) == (
        "2026-11-23T12:00:01Z patch_cards_done card_id=42 -> 200"
    )


def test_only_id_arguments_are_logged() -> None:
    context = call(
        "post_cards_batch_archive",
        {
            "card_ids": [3, 1],
            "column_id": 7,
            "title": "private text",
            "flag_id": True,  # a bool is not an id
            "user_id": "x",  # nor is a string
            "label_ids": [],  # nothing to show
            "tag_ids": ["a"],
        },
    )
    line = audit.describe(context, [200, 200], failed=False)
    assert line.split(" ", 1)[1] == (
        "post_cards_batch_archive card_ids=3,1 column_id=7 -> 200,200"
    )


def test_a_call_without_arguments_or_requests() -> None:
    assert audit.describe(call("get_x"), [], failed=False) == (
        "2026-11-23T12:00:01Z get_x -> no request sent"
    )
    assert audit.describe(call("get_x"), [], failed=True) == (
        "2026-11-23T12:00:01Z get_x -> error, no request sent"
    )


async def test_the_middleware_logs_a_call_that_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="aft_mcp.audit")

    async def fails(context: MiddlewareContext[Any]) -> ToolResult:
        await audit.record_status(httpx2.Response(500))
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await audit.AuditLog().on_call_tool(call("get_x", {"board_id": 1}), fails)
    assert caplog.messages == ["2026-11-23T12:00:01Z get_x board_id=1 -> 500"]


async def test_statuses_outside_a_tool_call_are_ignored() -> None:
    await audit.record_status(httpx2.Response(200))  # must not raise


def test_configure_sends_bare_lines_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = logging.getLogger("aft_mcp.audit.test")
    monkeypatch.setattr(audit, "AUDIT_LOG", logger)
    audit.configure_audit_log()
    audit.configure_audit_log()  # a second call adds no second handler
    logger.info("a line")
    assert capsys.readouterr().err.splitlines() == ["a line"]
    assert len(logger.handlers) == 1
    assert logger.propagate is False


def test_names_from_the_client_cannot_forge_a_line() -> None:
    forged = "get_x -> 200\n2026-11-23T12:00:02Z delete_cards card_id=1"
    context = call(forged, {"card_id\nfake": 3, "board_id": 1})
    line = audit.describe(context, [], failed=True)
    assert "\n" not in line
    assert line == (
        "2026-11-23T12:00:01Z "
        + json.dumps(forged)
        + " board_id=1 -> error, no request sent"
    )
