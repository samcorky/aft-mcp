import asyncio
import io
import json
import logging
import socket
from typing import TYPE_CHECKING, Any

import httpx2
import mcp_types as mt
import pytest
import structlog
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from aft_mcp import audit

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.conftest import LogRecords

pytestmark = pytest.mark.anyio


def call(name: str, arguments: dict[str, Any] | None = None) -> MiddlewareContext[Any]:
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name=name, arguments=arguments)
    )


def response(
    status: int, method: str = "GET", url: str = "https://aft.test/x"
) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request(method, url))


def ok(text: str = "done") -> ToolResult:
    return ToolResult(content=text)


def without_common_fields(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        assert record.pop("timestamp")
        assert record.pop("level") == "info"
        assert record.pop("logger") == "aft_mcp.audit"
    return records


def test_only_id_arguments_are_bound() -> None:
    fields = audit.call_fields(
        call(
            "post_cards_batch_archive",
            {
                "card_ids": [3, 1],
                "column_id": 7,
                "title": "private text",
                "flag_id": True,  # a bool is not an id
                "user_id": "x",  # nor is a string
                "label_ids": [],  # nothing to show
                "tag_ids": ["a"],
                "bad\nname_id": 1,  # not a plain name
            },
        )
    )
    call_id = fields.pop("call")
    assert len(call_id) == 8
    assert fields == {
        "tool": "post_cards_batch_archive",
        "card_ids": [3, 1],
        "column_id": 7,
    }


async def test_a_call_logs_each_response_then_the_result(
    log_records: LogRecords,
) -> None:
    async def tool(context: MiddlewareContext[Any]) -> ToolResult:
        await audit.log_response(
            response(200, "PATCH", "https://aft.test/api/cards/42/done?q=secret")
        )
        return ok()

    await audit.AuditLog().on_call_tool(
        call("patch_cards_done", {"card_id": 42, "done": True}), tool
    )

    records = without_common_fields(log_records())
    call_id = records[0]["call"]
    assert records[0] == {
        "event": "aft_response",
        "call": call_id,
        "tool": "patch_cards_done",
        "card_id": 42,
        "method": "PATCH",
        "path": "/api/cards/42/done",
        "status": 200,
    }
    assert records[1].pop("duration_ms") >= 0
    assert records[1] == {
        "event": "tool_result",
        "call": call_id,
        "tool": "patch_cards_done",
        "card_id": 42,
        "outcome": "ok",
    }
    assert "secret" not in json.dumps(records)  # the query string is left out


async def test_a_call_that_raises_logs_the_error_type_only(
    log_records: LogRecords,
) -> None:
    async def fails(context: MiddlewareContext[Any]) -> ToolResult:
        await audit.log_response(response(500))
        raise RuntimeError("AFT said: private text")

    with pytest.raises(RuntimeError):
        await audit.AuditLog().on_call_tool(call("get_x", {"board_id": 1}), fails)

    response_record, result = without_common_fields(log_records())
    assert response_record["status"] == 500
    assert result["outcome"] == "error"
    assert result["error"] == "RuntimeError"
    assert "private text" not in json.dumps(log_records())


async def test_an_error_result_is_logged_as_an_error(log_records: LogRecords) -> None:
    async def tool(context: MiddlewareContext[Any]) -> ToolResult:
        return ToolResult(content="nope", is_error=True)

    await audit.AuditLog().on_call_tool(call("get_x"), tool)
    assert log_records()[0]["outcome"] == "error"


async def test_the_call_fields_are_gone_after_the_call(log_records: LogRecords) -> None:
    async def tool(context: MiddlewareContext[Any]) -> ToolResult:
        return ok()

    await audit.AuditLog().on_call_tool(call("get_x", {"board_id": 1}), tool)
    await audit.log_response(response(200))  # outside any call, e.g. at startup

    last = log_records()[-1]
    assert last["event"] == "aft_response"
    assert not {"call", "tool", "board_id"} & last.keys()


async def test_concurrent_calls_keep_their_own_fields(log_records: LogRecords) -> None:
    async def tool(context: MiddlewareContext[Any]) -> ToolResult:
        await asyncio.sleep(0)  # let the other call run in between
        await audit.log_response(
            response(200, url=f"https://aft.test/{context.message.name}")
        )
        await asyncio.sleep(0)
        return ok()

    middleware = audit.AuditLog()
    await asyncio.gather(
        middleware.on_call_tool(call("get_a", {"board_id": 1}), tool),
        middleware.on_call_tool(call("get_b", {"board_id": 2}), tool),
    )

    records = log_records()
    assert len(records) == 4
    for record in records:
        assert record["board_id"] == {"get_a": 1, "get_b": 2}[record["tool"]]
        if record["event"] == "aft_response":
            assert record["path"] == f"/{record['tool']}"
    calls = {
        record["tool"]: record["call"]
        for record in records
        if record["event"] == "tool_result"
    }
    assert calls["get_a"] != calls["get_b"]
    for record in records:
        assert record["call"] == calls[record["tool"]]


async def test_names_from_the_client_cannot_forge_a_logfmt_line() -> None:
    stream = io.StringIO()
    audit.configure_logging("logfmt", stream)

    async def tool(context: MiddlewareContext[Any]) -> ToolResult:
        return ok()

    forged = "get_x outcome=ok\ntimestamp=2026-11-23T12:00:02Z event=tool_result"
    await audit.AuditLog().on_call_tool(
        call(forged, {"card_id\nfake": 3, "board_id": 1}), tool
    )

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("timestamp=")
    assert " level=info logger=aft_mcp.audit event=tool_result call=" in lines[0]
    assert f"tool={json.dumps(forged)}" in lines[0]  # quoted, newline escaped
    assert "fake" not in lines[0].replace(json.dumps(forged), "")


def test_json_format_writes_one_object_per_line() -> None:
    stream = io.StringIO()
    audit.configure_logging("json", stream)
    structlog.get_logger("aft_mcp.test").info("hello", n=1)
    record = json.loads(stream.getvalue())
    assert record["event"] == "hello"
    assert record["n"] == 1
    assert record["timestamp"].endswith("Z")


async def test_third_party_logs_share_the_format_and_call_fields(
    log_records: LogRecords,
) -> None:
    async def tool(context: MiddlewareContext[Any]) -> ToolResult:
        logging.getLogger("fastmcp.server").error("Error calling tool")
        logging.getLogger("httpx").info("HTTP Request: GET https://aft.test/x?q=1")
        return ok()

    await audit.AuditLog().on_call_tool(call("get_x", {"board_id": 1}), tool)

    fastmcp_record, result = log_records()
    assert fastmcp_record["logger"] == "fastmcp.server"
    assert fastmcp_record["level"] == "error"
    assert fastmcp_record["event"] == "Error calling tool"
    assert fastmcp_record["call"] == result["call"]
    assert fastmcp_record["board_id"] == 1
    assert result["logger"] == "aft_mcp.audit"  # httpx's INFO line was dropped


def test_fastmcp_logs_through_the_root_handler_only() -> None:
    fastmcp = logging.getLogger("fastmcp")
    assert fastmcp.handlers == []
    assert fastmcp.propagate is True
    names = [h.get_name() for h in logging.getLogger().handlers]
    assert names.count(audit.HANDLER_NAME) == 1


def test_configuring_again_replaces_the_handler() -> None:
    stream = io.StringIO()
    audit.configure_logging("logfmt", stream)
    names = [h.get_name() for h in logging.getLogger().handlers]
    assert names.count(audit.HANDLER_NAME) == 1
    logging.getLogger("fastmcp").warning("careful")
    assert stream.getvalue().startswith("timestamp=")
    assert "level=warning logger=fastmcp event=careful" in stream.getvalue()


def test_an_exception_is_rendered_on_one_line() -> None:
    stream = io.StringIO()
    audit.configure_logging("logfmt", stream)
    try:
        raise ValueError("bad")
    except ValueError:
        logging.getLogger("fastmcp").exception("failed")
    [line] = stream.getvalue().splitlines()
    assert "exception=" in line
    assert "ValueError: bad" in line


@pytest.fixture
def syslog_socket(tmp_path: Path) -> Iterator[tuple[str, socket.socket]]:
    """A local Unix datagram socket standing in for /dev/log."""
    path = str(tmp_path / "log")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    sock.settimeout(2)
    yield path, sock
    sock.close()


def test_syslog_gets_the_same_line_with_ident_and_facility(
    syslog_socket: tuple[str, socket.socket],
) -> None:
    path, sock = syslog_socket
    stream = io.StringIO()
    audit.configure_logging(
        "logfmt", stream, syslog_facility="auth", syslog_address=path
    )
    logging.getLogger("fastmcp").warning("careful")
    message = sock.recv(4096).decode()
    # <priority> is facility * 8 + severity: auth is 4, warning is 4.
    assert message.startswith("<36>aft-mcp: timestamp=")
    assert "level=warning logger=fastmcp event=careful" in message
    assert "event=careful" in stream.getvalue()


def test_stderr_can_be_turned_off_when_syslog_is_on(
    syslog_socket: tuple[str, socket.socket],
) -> None:
    path, sock = syslog_socket
    stream = io.StringIO()
    audit.configure_logging(
        "json", stream, to_stream=False, syslog_facility="user", syslog_address=path
    )
    structlog.get_logger("aft_mcp.test").info("only syslog")
    assert '"event": "only syslog"' in sock.recv(4096).decode()
    assert stream.getvalue() == ""
    names = [h.get_name() for h in logging.getLogger().handlers]
    assert names.count(audit.HANDLER_NAME) == 1


def test_a_missing_syslog_socket_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(audit.LoggingError, match="can't log to syslog at"):
        audit.configure_logging(
            "logfmt", syslog_facility="user", syslog_address=str(tmp_path / "none")
        )


def test_a_syslog_address_that_is_not_a_socket_is_an_error(tmp_path: Path) -> None:
    not_a_socket = tmp_path / "file"
    not_a_socket.write_text("", encoding="utf-8")
    with pytest.raises(audit.LoggingError, match="not a socket"):
        audit.configure_logging(
            "logfmt", syslog_facility="user", syslog_address=str(not_a_socket)
        )


def test_the_fields_a_client_controls_are_capped() -> None:
    arguments: dict[str, Any] = {f"a{n:02}_id": n for n in range(30)}
    arguments["card_ids"] = list(range(100))
    fields = audit.call_fields(call("x" * 500, arguments))
    assert fields["tool"] == "x" * audit.MAX_TOOL_NAME + "..."
    ids = {k: v for k, v in fields.items() if k not in {"call", "tool"}}
    assert len([k for k in ids if not k.endswith("_count")]) == audit.MAX_ID_ARGUMENTS
    capped = audit.call_fields(call("get_x", {"card_ids": list(range(100))}))
    assert capped["card_ids"] == list(range(audit.MAX_IDS_PER_LIST))
    assert capped["card_ids_count"] == 100


def test_a_long_syslog_message_is_cut_not_lost(
    syslog_socket: tuple[str, socket.socket],
) -> None:
    path, sock = syslog_socket
    audit.configure_logging(
        "logfmt", io.StringIO(), syslog_facility="user", syslog_address=path
    )
    logging.getLogger("fastmcp").error("x" * 100_000)
    message = sock.recv(200_000).decode()
    assert message.rstrip("\x00").endswith(audit.TRUNCATED)
    assert len(message) < audit.MAX_SYSLOG_MESSAGE + 100
