"""Log one line per tool call to stderr, as a record of what the agent did.

AFT records the result of writes, but not reads or refused calls. Each line gives the
time the call started (UTC), the tool, the ids it was called with, and the HTTP
status AFT answered with::

    2026-11-23T12:00:01Z patch_cards_done card_id=42 -> 200

Request and response bodies are never logged, so card text and credentials stay out
of the log. Over stdio, stderr is separate from the protocol stream, so the log never
disturbs the client.
"""

import logging
import sys
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from fastmcp.server.middleware import Middleware

if TYPE_CHECKING:
    import httpx2
    import mcp_types as mt
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from fastmcp.tools.base import ToolResult

AUDIT_LOG = logging.getLogger("aft_mcp.audit")

# The HTTP statuses seen during the tool call in progress. A list rather than a value,
# so a status recorded in a copied context (another task) still reaches the call.
_statuses: ContextVar[list[int] | None] = ContextVar("aft_mcp_statuses", default=None)


async def record_status(response: httpx2.Response) -> None:
    """An httpx response hook: note the status for the tool call in progress."""
    statuses = _statuses.get()
    if statuses is not None:
        statuses.append(response.status_code)


def _is_id_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_id(name: str, value: object) -> bool:
    """Whether an argument is an id (``card_id=42``) or ids (``card_ids=[1, 2]``)."""
    if name.endswith("_id"):
        return _is_id_number(value)
    return (
        name.endswith("_ids")
        and isinstance(value, list)
        and bool(value)
        and all(_is_id_number(item) for item in value)
    )


def _format_id(name: str, value: object) -> str:
    if isinstance(value, list):
        return f"{name}={','.join(str(item) for item in value)}"
    return f"{name}={value}"


def describe(
    context: MiddlewareContext[mt.CallToolRequestParams],
    statuses: list[int],
    *,
    failed: bool,
) -> str:
    """The log line for one tool call; only id arguments are included."""
    arguments: dict[str, Any] = context.message.arguments or {}
    ids = [
        _format_id(name, value)
        for name, value in sorted(arguments.items())
        if _is_id(name, value)
    ]
    if statuses:
        outcome = ",".join(str(status) for status in statuses)
    else:
        outcome = "error, no request sent" if failed else "no request sent"
    started = context.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    return " ".join([started, context.message.name, *ids, "->", outcome])


class AuditLog(Middleware):
    """Write a line to the audit log for every tool call, allowed or not."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Run the tool, then log its name, ids and the statuses AFT returned."""
        statuses: list[int] = []
        token = _statuses.set(statuses)
        failed = True
        try:
            result = await call_next(context)
            failed = False
            return result
        finally:
            _statuses.reset(token)
            AUDIT_LOG.info(describe(context, statuses, failed=failed))


def configure_audit_log() -> None:
    """Send the audit log to stderr, one bare line per call."""
    if AUDIT_LOG.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    AUDIT_LOG.addHandler(handler)
    AUDIT_LOG.setLevel(logging.INFO)
    AUDIT_LOG.propagate = False
