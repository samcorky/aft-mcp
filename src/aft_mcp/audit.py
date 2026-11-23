"""Log every tool call to stderr with structlog, as a record of what the agent did.

AFT records the result of writes, but not reads or refused calls. For each tool call
the server logs one ``aft_response`` line per HTTP request it sent, then one
``tool_result`` line with the outcome. As logfmt (each line also starts with
``timestamp=... level=info logger=aft_mcp.audit``)::

    event=aft_response call=0c3e3ff3 tool=patch_cards_done method=PATCH
        path=/api/cards/42/done status=200 card_id=42
    event=tool_result call=0c3e3ff3 tool=patch_cards_done outcome=ok
        duration_ms=81 card_id=42

The ``call``, ``tool`` and id fields are bound to structlog's context variables for
the length of the call, so every line logged during it carries them. Context
variables only add fields to log lines: no code reads a value back out of them, and
each part logs what it sees itself (the httpx hook the status, the middleware the
outcome).

Request and response bodies are never logged, so card text and credentials stay out
of the log. The renderers quote and escape values, so a tool name from the client
can't forge a line. Over stdio, stderr is separate from the protocol stream, so the
log never disturbs the client. Other libraries' log lines (FastMCP, httpx) are
routed through the same handler and format; see ``configure_logging``.
"""

import logging
import logging.handlers
import os
import re
import secrets
import stat
import sys
import time
from typing import TYPE_CHECKING, Any, Literal, TextIO

import structlog
from fastmcp.server.middleware import Middleware

if TYPE_CHECKING:
    import httpx2
    import mcp_types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.base import ToolResult

type LogFormat = Literal["logfmt", "json"]

log = structlog.get_logger("aft_mcp.audit")


async def log_response(response: httpx2.Response) -> None:
    """An httpx response hook: log the request's method, path and status."""
    log.info(
        "aft_response",
        method=response.request.method,
        path=response.request.url.path,  # no query string, which may hold text
        status=response.status_code,
    )


# Argument names come from the client. Only plain names can become log keys.
PLAIN_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def _is_id_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_id(name: str, value: object) -> bool:
    """Whether an argument is an id (``card_id=42``) or ids (``card_ids=[1, 2]``)."""
    if not PLAIN_NAME.fullmatch(name):
        return False
    if name.endswith("_id"):
        return _is_id_number(value)
    return (
        name.endswith("_ids")
        and isinstance(value, list)
        and bool(value)
        and all(_is_id_number(item) for item in value)
    )


# Caps on what a client can put in a log line, so no record grows too big to send
# (a syslog datagram has a size limit) or to read.
MAX_TOOL_NAME = 128
MAX_ID_ARGUMENTS = 20
MAX_IDS_PER_LIST = 20


def _capped_ids(arguments: dict[str, Any]) -> dict[str, Any]:
    """The id arguments, at most ``MAX_ID_ARGUMENTS`` of them, lists cut short.

    A cut list keeps its first ``MAX_IDS_PER_LIST`` ids and adds ``<name>_count``
    with the full length.
    """
    ids: dict[str, Any] = {}
    for name, value in sorted(arguments.items()):
        if len(ids) >= MAX_ID_ARGUMENTS:
            break
        if not _is_id(name, value):
            continue
        if isinstance(value, list) and len(value) > MAX_IDS_PER_LIST:
            ids[name] = value[:MAX_IDS_PER_LIST]
            ids[f"{name}_count"] = len(value)
        else:
            ids[name] = value
    return ids


def call_fields(context: MiddlewareContext[mt.CallToolRequestParams]) -> dict[str, Any]:
    """The fields bound for one tool call: a call id, the tool and its id arguments.

    The id arguments all end in ``_id`` or ``_ids``, so they can't clash with
    ``call`` or ``tool``.
    """
    arguments: dict[str, Any] = context.message.arguments or {}
    tool = context.message.name
    if len(tool) > MAX_TOOL_NAME:
        tool = tool[:MAX_TOOL_NAME] + "..."
    return {"call": secrets.token_hex(4), "tool": tool, **_capped_ids(arguments)}


class AuditLog(Middleware):
    """Log the outcome of every tool call, allowed or not."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Run the tool with its fields bound, then log the outcome and duration."""
        with structlog.contextvars.bound_contextvars(**call_fields(context)):
            started = time.perf_counter()
            try:
                result = await call_next(context)
            except Exception as error:
                # The exception type only: its message may quote AFT's response.
                log.info(
                    "tool_result",
                    outcome="error",
                    error=type(error).__name__,
                    duration_ms=_elapsed_ms(started),
                )
                raise
            log.info(
                "tool_result",
                outcome="error" if result.is_error else "ok",
                duration_ms=_elapsed_ms(started),
            )
            return result


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


# The handler configure_logging() installs on the root logger, found again by name.
HANDLER_NAME = "aft_mcp"


class LoggingError(RuntimeError):
    """A log destination can't be used, such as a missing syslog socket."""


SYSLOG_IDENT = "aft-mcp: "
# Longer messages are cut, so a long traceback still fits in one datagram.
MAX_SYSLOG_MESSAGE = 8192
TRUNCATED = " ...[truncated]"


class CappedSysLogHandler(logging.handlers.SysLogHandler):
    """A SysLogHandler that cuts long messages instead of failing to send them."""

    def format(self, record: logging.LogRecord) -> str:
        """Format as usual, then cut to ``MAX_SYSLOG_MESSAGE`` characters."""
        message = super().format(record)
        if len(message) > MAX_SYSLOG_MESSAGE:
            return message[: MAX_SYSLOG_MESSAGE - len(TRUNCATED)] + TRUNCATED
        return message


DEFAULT_SYSLOG_ADDRESS = "/dev/log"


def _syslog_handler(facility: str, address: str) -> logging.Handler:
    """A handler for the local syslog, or a LoggingError naming the socket.

    SysLogHandler only reports a missing socket when it first sends, and then only
    to stderr, so the socket is checked here first.
    """
    try:
        if not stat.S_ISSOCK(os.stat(address).st_mode):
            msg = f"can't log to syslog at {address}: not a socket"
            raise LoggingError(msg)
        handler = CappedSysLogHandler(
            address=address,
            facility=logging.handlers.SysLogHandler.facility_names[facility],
        )
    except OSError as error:
        msg = f"can't log to syslog at {address}: {error.strerror or error}"
        raise LoggingError(msg) from None
    handler.ident = SYSLOG_IDENT
    return handler


def configure_logging(
    log_format: LogFormat,
    stream: TextIO = sys.stderr,
    *,
    to_stream: bool = True,
    syslog_facility: str | None = None,
    syslog_address: str = DEFAULT_SYSLOG_ADDRESS,
) -> None:
    """Send every log line, ours and third-party, to ``stream`` (stderr) and syslog.

    structlog runs on top of stdlib logging, and one handler on the root logger
    renders both structlog's lines and those of other libraries (FastMCP, httpx) as
    logfmt or JSON, with the same timestamp, level, logger name and per-call fields.
    stdout belongs to the MCP protocol, so nothing may be logged there.

    FastMCP gives its ``fastmcp`` logger its own Rich handler at import and stops it
    propagating; that is undone here, so its lines take the same path. httpx logs
    each request at INFO with the full URL, which ``aft_response`` already covers
    without the query string, so only its warnings come through.

    Args:
        log_format: ``logfmt`` or ``json``, for every destination.
        stream: Where the stream handler writes; stderr outside the tests.
        to_stream: Write to ``stream`` at all. Over SSH, stderr goes back to the
            client, so a server can turn it off and keep the log in syslog.
        syslog_facility: Also send each line to the local syslog with this facility
            (``user``, ``auth``, ``local0`` and so on); None for no syslog.
        syslog_address: The syslog socket; the tests pass their own.

    Raises:
        LoggingError: The syslog socket can't be reached.
    """
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.processors.LogfmtRenderer(
            key_order=["timestamp", "level", "logger", "event", "call", "tool"],
            drop_missing=True,
        )
    )
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    handlers: list[logging.Handler] = []
    if syslog_facility is not None:
        handlers.append(_syslog_handler(syslog_facility, syslog_address))
    if to_stream:
        handlers.append(logging.StreamHandler(stream))
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )
    root = logging.getLogger()
    for old in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        handler.set_name(HANDLER_NAME)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.WARNING)
    logging.getLogger("aft_mcp").setLevel(logging.INFO)

    fastmcp_logger = logging.getLogger("fastmcp")
    for old in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(old)
    fastmcp_logger.propagate = True
