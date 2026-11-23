"""Keep every AFT request inside the boards named by ``AFT_BOARD_IDS``.

AFT's own roles decide which boards the agent's user can reach. This guard is a
second check in the server, against a role granted too widely by mistake. It sits
under the HTTP client as a transport, so it sees every request a tool sends:

- A request whose path names a board, column, card or checklist item is sent only
  when that item is on an allowed board. So is one whose query or JSON body names
  one (moving a card to another column, or archiving a batch of cards). An id that
  is not a plain number is refused rather than guessed at.
- ``GET /api/boards`` is filtered to the allowed boards, and card search is limited
  to them through its ``board_ids`` parameter.
- A few routes that are not about any board, such as the user's own roles, are
  sent as they are (``NOT_ABOUT_A_BOARD``).
- Every other route is refused. That covers routes that span boards (global boards,
  schedules, notifications, public board links) and any route added to AFT later,
  so the guard fails closed.

AFT has no endpoint that returns a single column or checklist item, so the guard
learns which columns and checklist items belong to each allowed board from that
board's card listing, and refreshes this when it meets one it doesn't know. A card
is always looked up afresh, because cards can move between boards.
"""

import json
import re
from typing import TYPE_CHECKING

import httpx2

if TYPE_CHECKING:
    from collections.abc import Iterator

ID_IN_PATH = [
    (re.compile(r"^/api/boards/(\d+)(?:/|$)"), "board"),
    (re.compile(r"^/api/columns/(\d+)(?:/|$)"), "column"),
    (re.compile(r"^/api/cards/(\d+)(?:/|$)"), "card"),
    (re.compile(r"^/api/checklist-items/(\d+)(?:/|$)"), "checklist item"),
]
# Fields naming one item, in a query or at the top level of a JSON body.
ID_FIELDS = {"board_id": "board", "column_id": "column", "card_id": "card"}
CARD_IDS = "card_ids"
# Routes whose items are named in the body, which is checked for every request.
SCOPED_BY_BODY = re.compile(r"^/api/cards/batch/(?:archive|unarchive)$")
BOARD_LIST = "/api/boards"
CARD_SEARCH = "/api/search/cards"
# Routes about the AFT user or the server rather than a board's contents.
NOT_ABOUT_A_BOARD = re.compile(
    "^/api/(?:{})$".format(
        "|".join(
            [
                "auth/(?:me|check)",
                "roles/(?:my-roles|permissions|permission-model|permission-mappings)",
                "permissions/mapping",
                "version",
                "health/(?:live|ready)",
                "settings/(?:schema|timezone)",
                "branding/(?:app-name|logo)",
            ]
        )
    )
)


class BoardRefusedError(Exception):
    """The request reaches outside the allowed boards."""


def _id_number(field: str, value: object) -> int:
    """A plain integer id, or BoardRefusedError (a bool or a string is refused)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise BoardRefusedError(
        f"{field} must be a plain number while AFT_BOARD_IDS is set"
    )


def _ids_in_query(request: httpx2.Request) -> Iterator[tuple[str, int]]:
    """Yield the (kind, id) pairs named in the query string."""
    for field, kind in ID_FIELDS.items():
        for value in request.url.params.get_list(field):
            yield kind, _id_number(field, int(value) if value.isdigit() else value)


def _ids_in_body(request: httpx2.Request) -> Iterator[tuple[str, int]]:
    """Yield the (kind, id) pairs named at the top level of a JSON body."""
    try:
        body = json.loads(request.content) if request.content else None
    except ValueError:
        return
    if not isinstance(body, dict):
        return
    for field, kind in ID_FIELDS.items():
        if field in body:
            yield kind, _id_number(field, body[field])
    if CARD_IDS in body:
        card_ids = body[CARD_IDS]
        if not isinstance(card_ids, list):
            raise BoardRefusedError(
                f"{CARD_IDS} must be a list of card ids while AFT_BOARD_IDS is set"
            )
        for card_id in card_ids:
            yield "card", _id_number(CARD_IDS, card_id)


def _dict(value: object, key: str) -> dict[str, object]:
    """``value[key]`` if both are dicts, else an empty dict."""
    found = value.get(key) if isinstance(value, dict) else None
    return found if isinstance(found, dict) else {}


def _list(value: object, key: str) -> list[object]:
    """``value[key]`` if ``value`` is a dict and the item a list, else []."""
    found = value.get(key) if isinstance(value, dict) else None
    return found if isinstance(found, list) else []


class BoardGuard(httpx2.AsyncBaseTransport):
    """A transport that refuses or narrows requests outside the allowed boards."""

    def __init__(
        self, inner: httpx2.AsyncBaseTransport, board_ids: frozenset[int]
    ) -> None:
        self._inner = inner
        self._boards = board_ids
        self._column_board: dict[int, int] = {}
        self._item_card: dict[int, int] = {}

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        """Check the request, send it if allowed, and filter the board list."""
        try:
            await self._admit(request)
        except BoardRefusedError as refused:
            return httpx2.Response(403, json={"error": str(refused)}, request=request)
        response = await self._inner.handle_async_request(request)
        if request.method == "GET" and request.url.path == BOARD_LIST:
            return await self._filter_board_list(request, response)
        return response

    async def aclose(self) -> None:
        """Close the wrapped transport."""
        await self._inner.aclose()

    # --- deciding ------------------------------------------------------------------

    async def _admit(self, request: httpx2.Request) -> None:
        """Raise BoardRefusedError unless the request stays on allowed boards."""
        await self._admit_path(request)
        for kind, item_id in [*_ids_in_query(request), *_ids_in_body(request)]:
            await self._require(kind, item_id, request)

    async def _admit_path(self, request: httpx2.Request) -> None:
        """Check the item the path names, or refuse a path the guard can't scope."""
        path = request.url.path
        raw_path = request.url.raw_path.partition(b"?")[0]
        if b"%" in raw_path or b"/." in raw_path:
            # An encoded or relative segment could be read differently by AFT.
            raise BoardRefusedError(f"{path} is refused while AFT_BOARD_IDS is set")
        for pattern, kind in ID_IN_PATH:
            if match := pattern.match(path):
                await self._require(kind, int(match[1]), request)
                return
        if path == CARD_SEARCH:
            self._narrow_search(request)
        elif not (
            path == BOARD_LIST
            or SCOPED_BY_BODY.match(path)
            or NOT_ABOUT_A_BOARD.match(path)
        ):
            reason = "can't be limited to the boards in AFT_BOARD_IDS"
            raise BoardRefusedError(f"{path} {reason}, so it is refused")

    async def _require(self, kind: str, item_id: int, request: httpx2.Request) -> None:
        """Raise BoardRefusedError unless the item is on an allowed board."""
        if await self._board_of(kind, item_id, request) not in self._boards:
            raise BoardRefusedError(
                f"{kind} {item_id} is not on a board allowed by AFT_BOARD_IDS"
            )

    async def _board_of(
        self, kind: str, item_id: int, request: httpx2.Request
    ) -> int | None:
        """The board an item is on, or None if it can't be found."""
        if kind == "board":
            return item_id
        if kind == "column":
            if item_id not in self._column_board:
                await self._learn_boards(request)
            return self._column_board.get(item_id)
        if kind == "checklist item":
            if item_id not in self._item_card:
                await self._learn_boards(request)
            card_id = self._item_card.get(item_id)
            return (
                None if card_id is None else await self._board_of_card(card_id, request)
            )
        return await self._board_of_card(item_id, request)

    async def _board_of_card(self, card_id: int, request: httpx2.Request) -> int | None:
        """Look the card up afresh, then find the board its column is on."""
        card = _dict(await self._get(request, f"/api/cards/{card_id}"), "card")
        column_id = card.get("column_id")
        if not isinstance(column_id, int):
            return None
        return await self._board_of("column", column_id, request)

    def _narrow_search(self, request: httpx2.Request) -> None:
        """Search only the allowed boards, or the allowed ones among those asked."""
        asked = request.url.params.get("board_ids")
        boards = set(self._boards)
        if asked:
            boards &= {int(part) for part in asked.split(",") if part.strip().isdigit()}
        if not boards:
            raise BoardRefusedError("none of the boards searched are in AFT_BOARD_IDS")
        board_ids = ",".join(str(board) for board in sorted(boards))
        request.url = request.url.copy_set_param("board_ids", board_ids)

    # --- talking to AFT --------------------------------------------------------------

    async def _learn_boards(self, request: httpx2.Request) -> None:
        """Record which columns and checklist items are on each allowed board."""
        for board_id in sorted(self._boards):
            listing = await self._get(
                request, f"/api/boards/{board_id}/cards", b"archived=both"
            )
            for column in _list(_dict(listing, "board"), "columns"):
                self._learn_column(board_id, column)

    def _learn_column(self, board_id: int, column: object) -> None:
        column_id = column.get("id") if isinstance(column, dict) else None
        if isinstance(column_id, int):
            self._column_board[column_id] = board_id
        for card in _list(column, "cards"):
            for item in _list(card, "checklist_items"):
                item_id = item.get("id") if isinstance(item, dict) else None
                card_id = card.get("id") if isinstance(card, dict) else None
                if isinstance(item_id, int) and isinstance(card_id, int):
                    self._item_card[item_id] = card_id

    async def _get(
        self, request: httpx2.Request, path: str, query: bytes = b""
    ) -> object:
        """GET a path as the same user, returning the JSON body or None."""
        lookup = httpx2.Request(
            "GET",
            request.url.copy_with(path=path, query=query),
            headers={
                name: value
                for name, value in request.headers.items()
                if name.lower() in {"authorization", "accept"}
            },
            extensions=request.extensions,
        )
        response = await self._inner.handle_async_request(lookup)
        await response.aread()
        if response.status_code != 200:
            return None
        try:
            body: object = response.json()
        except ValueError:
            return None
        return body

    async def _filter_board_list(
        self, request: httpx2.Request, response: httpx2.Response
    ) -> httpx2.Response:
        """Drop the boards that are not allowed from a ``GET /api/boards`` reply."""
        await response.aread()
        try:
            body = response.json()
        except ValueError:
            return response
        if response.status_code != 200 or not isinstance(body, dict):
            return response
        body["boards"] = [
            board
            for board in _list(body, "boards")
            if isinstance(board, dict) and board.get("id") in self._boards
        ]
        return httpx2.Response(200, json=body, request=request)
