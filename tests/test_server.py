import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
import structlog
from fastmcp import FastMCP
from pydantic import SecretStr

from aft_mcp import server
from aft_mcp.audit import LoggingError
from aft_mcp.board_guard import BoardGuard
from aft_mcp.settings import Settings, SettingsError
from aft_mcp.swagger_convert import convert

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import LogRecords


def tool_names(spec: dict[str, Any], *, read_only: bool = False) -> set[str]:
    """Build a server from a spec with the real route maps and list its tools."""
    mcp = FastMCP.from_openapi(
        openapi_spec=convert(spec),
        client=httpx2.AsyncClient(base_url="https://aft.test"),
        route_maps=server.build_route_maps(read_only=read_only),
    )
    return {tool.name for tool in asyncio.run(mcp.list_tools())}


def stub_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> list[httpx2.Request]:
    """Route httpx2.get through a MockTransport; return the requests seen."""
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client = httpx2.Client(transport=httpx2.MockTransport(record))

    def fake_get(url: str, **kwargs: Any) -> httpx2.Response:
        kwargs.pop("follow_redirects", None)
        return client.get(url, follow_redirects=True, **kwargs)

    monkeypatch.setattr(httpx2, "get", fake_get)
    return seen


# --- the exposure policy ----------------------------------------------------------


def test_only_safe_reads_and_allowed_writes_become_tools(
    aft_like_spec: dict[str, Any],
) -> None:
    assert tool_names(aft_like_spec) == {
        # Reads
        "get_boards",
        "get_boards_by_board_id",
        "get_boards_columns",
        "get_cards_by_card_id",
        # Allowed writes inside a board
        "post_boards_columns",
        "patch_columns_by_column_id",
        "post_columns_cards",
        "patch_cards_by_card_id",
        "post_cards_comments",
    }


def test_read_only_mode_exposes_only_the_safe_reads(
    aft_like_spec: dict[str, Any],
) -> None:
    assert tool_names(aft_like_spec, read_only=True) == {
        "get_boards",
        "get_boards_by_board_id",
        "get_boards_columns",
        "get_cards_by_card_id",
    }


def test_create_server_honours_read_only(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    aft_like_spec: dict[str, Any],
) -> None:
    stub_http(monkeypatch, lambda _: httpx2.Response(200, json=aft_like_spec))
    mcp = server.create_server(settings.model_copy(update={"read_only": True}))
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names
    assert all(name.startswith("get_") for name in names)


def test_never_exposes_delete_board_writes_or_side_effect_gets(
    aft_like_spec: dict[str, Any],
) -> None:
    names = tool_names(aft_like_spec)
    assert not {n for n in names if n.startswith("delete_")}
    assert "post_boards" not in names  # create a board
    assert "patch_boards_by_board_id" not in names  # board-level change
    assert "get_database_backup" not in names  # GET that creates a backup


# Reviewed snapshots of the exposure lists in server.py. Changing either list fails
# these tests until the snapshot is updated too, so a new or widened route is always a
# deliberate change that shows up in review. Update the README's "What is exposed"
# section in the same pull request.
REVIEWED_WRITES = {
    ("PATCH", r"^/api/cards/\{card_id\}$"),
    ("PATCH", r"^/api/cards/\{card_id\}/done$"),
    ("PATCH", r"^/api/cards/\{card_id\}/archive$"),
    ("PATCH", r"^/api/cards/\{card_id\}/unarchive$"),
    ("POST", r"^/api/cards/batch/archive$"),
    ("POST", r"^/api/cards/batch/unarchive$"),
    ("PUT", r"^/api/cards/\{card_id\}/assignees$"),
    ("POST", r"^/api/cards/\{card_id\}/comments$"),
    ("POST", r"^/api/cards/\{card_id\}/checklist-items$"),
    ("PATCH", r"^/api/checklist-items/\{item_id\}$"),
    ("PATCH", r"^/api/columns/\{column_id\}$"),
    ("POST", r"^/api/boards/\{board_id\}/columns$"),
    ("POST", r"^/api/columns/\{column_id\}/cards$"),
}
REVIEWED_SIDE_EFFECT_GETS = {
    r"^/api/database/backup$",
}


def test_allowed_writes_match_the_reviewed_snapshot() -> None:
    assert set(server.ALLOWED_WRITES) == REVIEWED_WRITES
    assert len(server.ALLOWED_WRITES) == len(REVIEWED_WRITES), "duplicate entry"


def test_side_effect_gets_match_the_reviewed_snapshot() -> None:
    assert set(server.SIDE_EFFECT_GET_PATTERNS) == REVIEWED_SIDE_EFFECT_GETS


@pytest.mark.parametrize(("method", "pattern"), server.ALLOWED_WRITES)
def test_each_allowed_write_is_narrow(method: str, pattern: str) -> None:
    # Rules that hold even if the snapshot above is updated carelessly.
    assert method in {"PATCH", "POST", "PUT"}, "only these write methods are allowed"
    assert pattern.startswith("^/api/"), "must be anchored at the start"
    assert pattern.endswith("$"), "must be anchored at the end"
    assert ".*" not in pattern, "must not use wildcards"
    assert ".+" not in pattern, "must not use wildcards"
    assert not pattern.startswith(r"^/api/boards/\{board_id\}$"), "board-level write"
    assert pattern != "^/api/boards$", "creating boards is not allowed"


# --- the HTTP client ---------------------------------------------------------------


def test_build_client_uses_the_settings(settings: Settings) -> None:
    client = server.build_client(settings)
    assert str(client.base_url) == "https://aft.test/"
    assert client.timeout.read == 5.0
    request = client.build_request("GET", "/api/boards")
    auth_flow = client._build_request_auth(request).auth_flow(request)
    assert next(auth_flow).headers["Authorization"].startswith("Basic ")


def test_build_client_guards_the_boards_when_board_ids_is_set(
    settings: Settings,
) -> None:
    assert not isinstance(server.build_client(settings)._transport, BoardGuard)
    guarded = settings.model_copy(update={"board_ids": frozenset({12})})
    assert isinstance(server.build_client(guarded)._transport, BoardGuard)


def test_build_client_sends_a_token_as_bearer(settings: Settings) -> None:
    with_token = settings.model_copy(
        update={
            "token": SecretStr("aft_token_value"),
            "username": None,
            "password": None,
        }
    )
    client = server.build_client(with_token)
    request = client.build_request("GET", "/api/boards")
    auth = client._build_request_auth(request)
    assert next(auth.auth_flow(request)).headers["Authorization"] == (
        "Bearer aft_token_value"
    )
    assert "aft_token_value" not in repr(auth)
    assert "aft_token_value" not in repr(vars(auth))


def test_build_auth_never_sends_a_missing_password(settings: Settings) -> None:
    incomplete = settings.model_copy(update={"password": None})
    with pytest.raises(SettingsError):
        server.build_auth(incomplete)


# --- loading the spec -------------------------------------------------------------


def test_load_spec_from_file(tmp_path: Path, aft_like_spec: dict[str, Any]) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(aft_like_spec), encoding="utf-8")
    assert server.load_spec_from_file(path) == aft_like_spec


def test_load_spec_from_url_follows_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/old":
            return httpx2.Response(302, headers={"Location": "https://aft.test/new"})
        return httpx2.Response(200, json={"swagger": "2.0"})

    seen = stub_http(monkeypatch, handler)
    assert server.load_spec_from_url(
        "https://aft.test/old", timeout=1, allow_insecure=False
    ) == {"swagger": "2.0"}
    assert [r.url.path for r in seen] == ["/old", "/new"]


@pytest.mark.parametrize(
    "location",
    [
        "https://elsewhere.test/api/apispec.json",  # another host
        "https://aft.test:8443/api/apispec.json",  # another port
    ],
)
def test_load_spec_from_url_refuses_a_redirect_off_the_host(
    monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "aft.test" and request.url.port is None:
            return httpx2.Response(302, headers={"Location": location})
        return httpx2.Response(200, json={"swagger": "2.0"})

    stub_http(monkeypatch, handler)
    with pytest.raises(SettingsError, match="redirected to another host or port"):
        server.load_spec_from_url(
            "https://aft.test/api/apispec.json", timeout=1, allow_insecure=True
        )


def test_load_spec_from_url_refuses_a_redirect_to_http_unless_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.scheme == "https":
            return httpx2.Response(
                302, headers={"Location": "http://aft.test:443/api/apispec.json"}
            )
        return httpx2.Response(200, json={"swagger": "2.0"})

    stub_http(monkeypatch, handler)
    url = "https://aft.test/api/apispec.json"
    with pytest.raises(SettingsError, match="redirected to http"):
        server.load_spec_from_url(url, timeout=1, allow_insecure=False)
    assert server.load_spec_from_url(url, timeout=1, allow_insecure=True) == {
        "swagger": "2.0"
    }


def test_load_spec_from_url_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_http(monkeypatch, lambda _: httpx2.Response(500))
    with pytest.raises(httpx2.HTTPStatusError):
        server.load_spec_from_url(
            "https://aft.test/api/apispec.json", timeout=1, allow_insecure=False
        )


def test_load_spec_prefers_the_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings: Settings,
    aft_like_spec: dict[str, Any],
) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(aft_like_spec), encoding="utf-8")
    seen = stub_http(monkeypatch, lambda _: httpx2.Response(500))
    from_file = settings.model_copy(update={"spec_file": path})
    assert server.load_spec(from_file)["openapi"] == "3.0.3"
    assert seen == []


def test_load_spec_fetches_the_url(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    aft_env: dict[str, str],
    aft_like_spec: dict[str, Any],
) -> None:
    seen = stub_http(monkeypatch, lambda _: httpx2.Response(200, json=aft_like_spec))
    assert server.load_spec(settings)["openapi"] == "3.0.3"
    assert [str(r.url) for r in seen] == [aft_env["AFT_MCP_SPEC_URL"]]


# --- assembling and running -------------------------------------------------------


def test_create_server(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    aft_like_spec: dict[str, Any],
) -> None:
    stub_http(monkeypatch, lambda _: httpx2.Response(200, json=aft_like_spec))
    mcp = server.create_server(settings)
    assert mcp.name == "AFT"
    assert "get_boards" in {t.name for t in asyncio.run(mcp.list_tools())}


def test_main_loads_the_settings_then_runs(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    calls: list[object] = []

    class FakeServer:
        def __init__(self, given: Settings) -> None:
            calls.append(given)

        def run(self) -> None:
            calls.append("run")

    monkeypatch.setattr(server, "load_settings", lambda *_: settings)
    monkeypatch.setattr(server, "create_server", FakeServer)
    server.main()
    assert calls == [settings, "run"]


def test_main_warns_about_each_http_url(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, log_records: LogRecords
) -> None:
    insecure = settings.model_copy(
        update={
            "base_url": "http://aft.test",
            "spec_url": None,
            "allow_insecure": True,
        }
    )
    monkeypatch.setattr(server, "load_settings", lambda *_: insecure)
    monkeypatch.setattr(server, "configure_logging", lambda *_, **__: None)
    monkeypatch.setattr(
        server, "create_server", lambda _s: type("S", (), {"run": lambda _self: None})()
    )
    server.main()
    warnings = [r for r in log_records() if r["event"] == "insecure_url"]
    assert [w["setting"] for w in warnings] == ["AFT_MCP_BASE_URL", "AFT_MCP_SPEC_URL"]
    assert {w["level"] for w in warnings} == {"warning"}


def test_main_exits_when_building_the_server_finds_a_settings_problem(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def refuse(_settings: Settings) -> FastMCP:
        raise SettingsError(["the spec at x redirected to http: y"])

    monkeypatch.setattr(server, "load_settings", lambda *_: settings)
    monkeypatch.setattr(server, "configure_logging", lambda *_, **__: None)
    monkeypatch.setattr(server, "create_server", refuse)
    with pytest.raises(SystemExit) as exited:
        server.main()
    assert "redirected to http" in str(exited.value.code)


def test_main_exits_listing_every_settings_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_env_file: Path | None, _secrets: Path | None) -> Settings:
        raise SettingsError(["AFT_MCP_BASE_URL: missing", "AFT_MCP_TIMEOUT: missing"])

    monkeypatch.setattr(server, "load_settings", fail)
    with pytest.raises(SystemExit) as exited:
        server.main()
    assert "AFT_MCP_BASE_URL: missing" in str(exited.value.code)
    assert "AFT_MCP_TIMEOUT: missing" in str(exited.value.code)


def test_main_exits_when_the_named_env_file_is_missing(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    monkeypatch.setenv("AFT_MCP_ENV_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(SystemExit) as exited:
        server.main()
    assert "AFT_MCP_ENV_FILE: no such file" in str(exited.value.code)


def test_a_dot_env_in_the_working_directory_gets_a_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.env"
    server.warn_about_working_directory_env(config)  # no ./.env: nothing to say
    assert capsys.readouterr().err == ""

    Path(".env").write_text("AFT_MCP_BASE_URL=https://elsewhere.test\n", "utf-8")
    server.warn_about_working_directory_env(config)
    err = capsys.readouterr().err
    assert "ignoring .env in the working directory" in err
    assert str(config) in err
    assert "elsewhere" not in err  # never reads it

    server.warn_about_working_directory_env(Path(".env"))  # it is the named file
    assert capsys.readouterr().err == ""


def test_importing_the_module_has_no_side_effects() -> None:
    # Regression: the server used to be built at import time, which needed the
    # network and a full .env just to import it.
    assert not hasattr(server, "mcp")


def test_without_an_instance_the_server_is_plain_aft(settings: Settings) -> None:
    assert server.server_identity(settings) == ("AFT", None)


def test_the_instance_names_the_server_and_its_instructions(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    aft_like_spec: dict[str, Any],
) -> None:
    stub_http(monkeypatch, lambda _: httpx2.Response(200, json=aft_like_spec))
    mcp = server.create_server(settings.model_copy(update={"instance": "prod"}))
    assert mcp.name == "AFT (prod)"
    assert mcp.instructions is not None
    assert "AFT instance named 'prod'" in mcp.instructions


def test_main_adds_the_instance_to_every_log_line(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, log_records: LogRecords
) -> None:
    class LoggingServer:
        def run(self) -> None:
            structlog.get_logger("aft_mcp.test").info("serving")

    labelled = settings.model_copy(update={"instance": "test"})
    monkeypatch.setattr(server, "load_settings", lambda *_: labelled)
    monkeypatch.setattr(server, "configure_logging", lambda *_, **__: None)
    monkeypatch.setattr(server, "create_server", lambda _s: LoggingServer())
    server.main()
    assert log_records()[-1]["instance"] == "test"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("203.0.113.5 52114 192.0.2.1 22", "203.0.113.5:52114"), ("", None)],
)
def test_ssh_client_address(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: str | None
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", value)
    assert server.ssh_client_address() == expected


def test_main_passes_the_log_settings_and_binds_the_ssh_client(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, log_records: LogRecords
) -> None:
    calls: list[tuple[object, ...]] = []

    class LoggingServer:
        def run(self) -> None:
            structlog.get_logger("aft_mcp.test").info("serving")

    to_syslog = settings.model_copy(
        update={"log_syslog": True, "log_stderr": False, "log_syslog_facility": "auth"}
    )
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.5 52114 192.0.2.1 22")
    monkeypatch.setattr(server, "load_settings", lambda *_: to_syslog)
    monkeypatch.setattr(
        server, "configure_logging", lambda *a, **k: calls.append((a, k))
    )
    monkeypatch.setattr(server, "create_server", lambda _s: LoggingServer())
    server.main()
    assert calls == [
        (("logfmt",), {"to_stream": False, "syslog_facility": "auth"}),
    ]
    assert log_records()[-1]["ssh_client"] == "203.0.113.5:52114"


def test_main_exits_when_syslog_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def unreachable(*_: object, **__: object) -> None:
        raise LoggingError("can't log to syslog at /dev/log: No such file")

    monkeypatch.setattr(server, "load_settings", lambda *_: settings)
    monkeypatch.setattr(server, "configure_logging", unreachable)
    with pytest.raises(SystemExit) as exited:
        server.main()
    assert exited.value.code == "aft-mcp: can't log to syslog at /dev/log: No such file"
