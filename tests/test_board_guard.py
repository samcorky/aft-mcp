"""The board allowlist (AFT_BOARD_IDS), tested through a real httpx client.

Board 12 is allowed and board 99 is not. ``FakeAft`` serves both, and records every
request that reached it, so each test can check what the guard let through.
"""

from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from aft_mcp.board_guard import BoardGuard, _dict, _list

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.anyio

ALLOWED, OTHER = 12, 99
# column -> board, card -> column, checklist item -> card
COLUMNS = {100: ALLOWED, 900: OTHER}
CARDS = {1000: 100, 9000: 900}
ITEMS = {5000: 1000, 9500: 9000}


class FakeAft:
    """Answers like AFT for boards 12 and 99, recording each request."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.overrides: dict[str, httpx2.Response] = {}

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path]
        if path == "/api/boards":
            boards = [{"id": ALLOWED}, {"id": OTHER}]
            return httpx2.Response(200, json={"success": True, "boards": boards})
        if path.startswith("/api/cards/") and path.count("/") == 3:
            card_id = int(path.rsplit("/", 1)[1])
            if card_id not in CARDS:
                return httpx2.Response(404, json={"error": "not found"})
            card = {"id": card_id, "column_id": CARDS[card_id]}
            return httpx2.Response(200, json={"success": True, "card": card})
        if path.endswith("/cards") and path.startswith("/api/boards/"):
            return httpx2.Response(
                200, json=self.board_listing(int(path.split("/")[3]))
            )
        return httpx2.Response(200, json={"ok": True})

    @staticmethod
    def board_listing(board_id: int) -> dict[str, Any]:
        columns = [
            {
                "id": column_id,
                "cards": [
                    {
                        "id": card_id,
                        "checklist_items": [
                            {"id": item_id, "card_id": card_id}
                            for item_id, owner in ITEMS.items()
                            if owner == card_id
                        ],
                    }
                    for card_id, column in CARDS.items()
                    if column == column_id
                ],
            }
            for column_id, board in COLUMNS.items()
            if board == board_id
        ]
        return {"success": True, "board": {"id": board_id, "columns": columns}}


@pytest.fixture
def fake_aft() -> FakeAft:
    return FakeAft()


@pytest.fixture
async def client(fake_aft: FakeAft) -> AsyncIterator[httpx2.AsyncClient]:
    guard = BoardGuard(httpx2.MockTransport(fake_aft), frozenset({ALLOWED}))
    async with httpx2.AsyncClient(
        base_url="https://aft.test", transport=guard, auth=("agent", "secret")
    ) as connected:
        yield connected


def refusal(response: httpx2.Response) -> str:
    assert response.status_code == 403
    error: str = response.json()["error"]
    assert "AFT_BOARD_IDS" in error
    return error


# --- ids in the path ------------------------------------------------------------------


async def test_a_board_on_the_list_is_sent(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    response = await client.get(f"/api/boards/{ALLOWED}/columns")
    assert response.status_code == 200
    assert fake_aft.paths() == [f"/api/boards/{ALLOWED}/columns"]


async def test_a_board_off_the_list_never_reaches_aft(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    response = await client.get(f"/api/boards/{OTHER}")
    assert (
        refusal(response) == f"board {OTHER} is not on a board allowed by AFT_BOARD_IDS"
    )
    assert fake_aft.requests == []


async def test_a_column_is_checked_against_the_allowed_boards_listing(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    assert (
        await client.patch("/api/columns/100", json={"name": "x"})
    ).status_code == 200
    refusal(await client.post("/api/columns/900/cards", json={"title": "x"}))
    assert fake_aft.paths() == [
        f"/api/boards/{ALLOWED}/cards",  # learn the columns
        "/api/columns/100",
        f"/api/boards/{ALLOWED}/cards",  # 900 is unknown, so look again
    ]
    assert fake_aft.requests[0].url.params["archived"] == "both"


async def test_known_columns_are_not_looked_up_again(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    await client.patch("/api/columns/100", json={})
    await client.patch("/api/columns/100", json={})
    assert fake_aft.paths().count(f"/api/boards/{ALLOWED}/cards") == 1


async def test_a_card_is_looked_up_each_time_since_cards_move(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    assert (await client.patch("/api/cards/1000/done", json={})).status_code == 200
    assert (await client.get("/api/cards/1000")).status_code == 200
    refusal(await client.get("/api/cards/9000"))
    # One lookup for each request, plus the GET itself.
    assert fake_aft.paths().count("/api/cards/1000") == 3
    assert "/api/cards/1000/done" in fake_aft.paths()


async def test_a_card_that_cannot_be_found_is_refused(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    refusal(await client.patch("/api/cards/4242/done", json={}))
    fake_aft.overrides["/api/cards/1000"] = httpx2.Response(200, json={"card": {}})
    refusal(await client.patch("/api/cards/1000/done", json={}))
    assert "/api/cards/1000/done" not in fake_aft.paths()


async def test_a_checklist_item_is_checked_through_its_card(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    ok = await client.patch("/api/checklist-items/5000", json={"checked": True})
    assert ok.status_code == 200
    refusal(await client.patch("/api/checklist-items/9500", json={"checked": True}))
    assert "/api/checklist-items/9500" not in fake_aft.paths()


async def test_a_known_checklist_item_still_checks_its_card(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    await client.patch("/api/checklist-items/5000", json={})
    fake_aft.requests.clear()
    # The card has since moved to a board that is not allowed.
    fake_aft.overrides["/api/cards/1000"] = httpx2.Response(
        200, json={"card": {"column_id": 900}}
    )
    refusal(await client.patch("/api/checklist-items/5000", json={}))
    assert "/api/checklist-items/5000" not in fake_aft.paths()


# --- ids in the body ------------------------------------------------------------------


async def test_moving_a_card_to_a_column_off_the_list_is_refused(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    refusal(await client.patch("/api/cards/1000", json={"column_id": 900}))
    ok = await client.patch("/api/cards/1000", json={"column_id": 100, "title": "x"})
    assert ok.status_code == 200
    assert [r.method for r in fake_aft.requests].count("PATCH") == 1


async def test_a_batch_with_any_card_off_the_list_is_refused(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    refusal(
        await client.post("/api/cards/batch/archive", json={"card_ids": [1000, 9000]})
    )
    ok = await client.post("/api/cards/batch/archive", json={"card_ids": [1000, "x"]})
    assert ok.status_code == 200
    assert fake_aft.paths().count("/api/cards/batch/archive") == 1


async def test_board_and_card_ids_in_a_body_are_checked(
    client: httpx2.AsyncClient,
) -> None:
    refusal(await client.post("/api/anything", json={"board_id": OTHER}))
    refusal(await client.post("/api/anything", json={"card_id": 9000}))
    assert (
        await client.post("/api/anything", json={"card_id": 1000})
    ).status_code == 200


@pytest.mark.parametrize("content", [b"not json", b"[1, 2]", b'{"card_ids": null}'])
async def test_bodies_without_ids_are_sent_as_they_are(
    client: httpx2.AsyncClient, fake_aft: FakeAft, content: bytes
) -> None:
    response = await client.post("/api/notifications", content=content)
    assert response.status_code == 200
    assert fake_aft.requests[-1].content == content


# --- routes across boards -------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/api/global-boards", "/api/global-boards/3/cards", "/api/schedules/7"]
)
async def test_routes_that_span_boards_are_refused(
    client: httpx2.AsyncClient, fake_aft: FakeAft, path: str
) -> None:
    assert "spans boards" in refusal(await client.get(path))
    assert fake_aft.requests == []


async def test_the_board_list_shows_only_allowed_boards(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.get("/api/boards")
    assert response.json() == {"success": True, "boards": [{"id": ALLOWED}]}


@pytest.mark.parametrize(
    "reply",
    [
        httpx2.Response(403, json={"error": "no"}),
        httpx2.Response(200, content=b"<html>"),
        httpx2.Response(200, json=["not", "a", "dict"]),
    ],
)
async def test_other_board_list_replies_pass_through(
    client: httpx2.AsyncClient, fake_aft: FakeAft, reply: httpx2.Response
) -> None:
    fake_aft.overrides["/api/boards"] = reply
    response = await client.get("/api/boards")
    assert response.status_code == reply.status_code
    assert response.content == reply.content


@pytest.mark.parametrize(
    ("asked", "sent"),
    [
        (None, f"{ALLOWED}"),
        (f"{ALLOWED},{OTHER}", f"{ALLOWED}"),
        (f"x,{ALLOWED}", f"{ALLOWED}"),
    ],
)
async def test_card_search_is_limited_to_allowed_boards(
    client: httpx2.AsyncClient, fake_aft: FakeAft, asked: str | None, sent: str
) -> None:
    params = {"q": "bug"} | ({"board_ids": asked} if asked else {})
    assert (await client.get("/api/search/cards", params=params)).status_code == 200
    (request,) = fake_aft.requests
    assert request.url.params["board_ids"] == sent
    assert request.url.params["q"] == "bug"


async def test_a_search_of_only_other_boards_is_refused(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    refusal(await client.get("/api/search/cards", params={"board_ids": f"{OTHER}"}))
    assert fake_aft.requests == []


async def test_routes_not_about_a_board_are_sent(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    assert (await client.get("/api/roles/my-roles")).status_code == 200
    assert fake_aft.paths() == ["/api/roles/my-roles"]


# --- the lookups themselves -----------------------------------------------------------


async def test_lookups_go_as_the_same_user_without_the_body(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    await client.patch("/api/cards/1000", json={"title": "x"})
    lookup, sent = fake_aft.requests[0], fake_aft.requests[-1]
    assert sent.method == "PATCH"
    assert lookup.method == "GET"
    assert lookup.content == b""
    assert lookup.headers["Authorization"] == sent.headers["Authorization"]
    assert "content-type" not in lookup.headers


async def test_a_board_listing_that_fails_or_is_malformed_allows_nothing(
    client: httpx2.AsyncClient, fake_aft: FakeAft
) -> None:
    listing = f"/api/boards/{ALLOWED}/cards"
    for reply in (
        httpx2.Response(500),
        httpx2.Response(200, content=b"oops"),
        httpx2.Response(
            200,
            json={"board": {"columns": [{"cards": [{"checklist_items": [{}]}]}, "x"]}},
        ),
    ):
        fake_aft.overrides[listing] = reply
        refusal(await client.patch("/api/columns/100", json={}))
        refusal(await client.patch("/api/checklist-items/5000", json={}))


async def test_closing_the_guard_closes_the_inner_transport() -> None:
    closed: list[bool] = []

    class Inner(httpx2.AsyncBaseTransport):
        async def aclose(self) -> None:
            closed.append(True)

    await BoardGuard(Inner(), frozenset({ALLOWED})).aclose()
    assert closed == [True]


def test_json_helpers_tolerate_other_shapes() -> None:
    assert _dict({"a": {"b": 1}}, "a") == {"b": 1}
    assert _dict({"a": [1]}, "a") == {}
    assert _dict([1], "a") == {}
    assert _list({"a": [1]}, "a") == [1]
    assert _list({"a": {}}, "a") == []
    assert _list("x", "a") == []
