"""The ``aft-mcp`` command line: serve, check, tools, version and settings."""

import json
import runpy
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

import httpx2
import pytest

import aft_mcp
from aft_mcp import cli, server
from aft_mcp.settings import SettingsError

if TYPE_CHECKING:
    from pathlib import Path


def run(*tokens: str) -> int:
    """Run the command line as ``aft-mcp <tokens>``; return its exit code."""
    with pytest.raises(SystemExit) as exited:
        cli.app(list(tokens), exit_on_error=False)
    code = exited.value.code
    return 0 if code is None else int(code)


@pytest.fixture
def spec_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    aft_env: dict[str, str],
    aft_like_spec: dict[str, Any],
) -> Path:
    """Settings that load the spec from a file, and no .env or config directory."""
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(aft_like_spec), encoding="utf-8")
    monkeypatch.setenv("AFT_MCP_SPEC_FILE", str(path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return path


def stub_aft(monkeypatch: pytest.MonkeyPatch, status: int) -> list[httpx2.Request]:
    """Answer the client's requests with ``status``; return the requests seen."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json={"user": {"id": 1}})

    real = server.build_client
    transport = httpx2.MockTransport(handler)
    monkeypatch.setattr(server, "build_client", lambda s: real(s, transport))
    return seen


@pytest.mark.parametrize("tokens", [(), ("serve",)])
def test_no_command_or_serve_runs_the_server(
    monkeypatch: pytest.MonkeyPatch, tokens: tuple[str, ...]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(server, "main", lambda: calls.append("main"))
    assert run(*tokens) == 0
    assert calls == ["main"]


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("version") == 0
    assert capsys.readouterr().out.strip() == aft_mcp.__version__


@pytest.mark.usefixtures("spec_file")
def test_check_passes_and_sends_only_one_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = stub_aft(monkeypatch, 200)
    assert run("check") == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split()[:2] for line in out] == [
        ["ok", "settings"],
        ["ok", "spec"],
        ["ok", "sign"],
    ]
    assert "(not found)" in out[0]
    assert [(r.method, r.url.path) for r in seen] == [("GET", "/api/auth/me")]


@pytest.mark.usefixtures("spec_file")
def test_check_fails_when_sign_in_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_aft(monkeypatch, 401)
    assert run("check") == 1
    assert (
        capsys.readouterr()
        .out.splitlines()[-1]
        .startswith("FAILED sign in  /api/auth/me -> 401")
    )


@pytest.mark.usefixtures("spec_file")
def test_check_reports_an_unreachable_aft(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    real = server.build_client
    transport = httpx2.MockTransport(unreachable)
    monkeypatch.setattr(server, "build_client", lambda s: real(s, transport))
    assert run("check") == 1
    assert "ConnectError: refused" in capsys.readouterr().out


def test_check_fails_on_bad_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    aft_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AFT_MCP_BASE_URL")
    assert run("check") == 1
    out = capsys.readouterr().out
    assert out.startswith("FAILED settings")
    assert "AFT_MCP_BASE_URL: Field required" in out


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        (SettingsError(["the spec redirected"]), "the spec redirected"),
        (FileNotFoundError("spec.json"), "FileNotFoundError: spec.json"),
    ],
)
@pytest.mark.usefixtures("spec_file")
def test_check_fails_when_the_spec_cant_be_loaded(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    shown: str,
) -> None:
    def fail(_settings: object) -> None:
        raise error

    monkeypatch.setattr(server, "load_spec", fail)
    assert run("check") == 1
    assert capsys.readouterr().out.splitlines()[-1] == f"FAILED spec     {shown}"


@pytest.mark.usefixtures("spec_file")
def test_tools_lists_routes_and_marks_writes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run("tools") == 0
    lines = capsys.readouterr().out.splitlines()
    assert "GET    /api/boards  get_boards" in lines
    assert any(
        line.startswith("PATCH  /api/cards/{card_id}") and line.endswith("(write)")
        for line in lines
    )
    assert not any("DELETE" in line for line in lines)


@pytest.mark.usefixtures("spec_file")
def test_tools_respects_read_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AFT_MCP_READ_ONLY", "true")
    assert run("tools") == 0
    assert "(write)" not in capsys.readouterr().out


def test_tools_and_settings_fail_on_bad_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    aft_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AFT_MCP_BASE_URL")
    assert run("tools") == 1
    assert run("settings") == 1
    assert capsys.readouterr().out.count("AFT_MCP_BASE_URL: Field required") == 2


def test_settings_shows_values_and_sources_with_secrets_masked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    aft_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "prod.env"
    env_file.write_text("AFT_MCP_INSTANCE=prod\nAFT_MCP_BOARD_IDS=15,12\n", "utf-8")
    secrets = tmp_path / "creds"
    secrets.mkdir()
    (secrets / "AFT_MCP_READ_ONLY").write_text("true", encoding="utf-8")
    monkeypatch.setenv("AFT_MCP_ENV_FILE", str(env_file))
    monkeypatch.setenv("AFT_MCP_SECRETS_DIR", str(secrets))
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.5 52114 192.0.2.1 22")

    assert run("settings") == 0
    out = capsys.readouterr().out
    rows = {line.split()[0]: line.split()[1:] for line in out.splitlines()}
    assert rows["AFT_MCP_BASE_URL"] == ["https://aft.test/", "environment"]
    assert rows["AFT_MCP_INSTANCE"] == ["prod", str(env_file)]
    assert rows["AFT_MCP_BOARD_IDS"] == ["15,12", str(env_file)]
    assert rows["AFT_MCP_READ_ONLY"] == ["True", str(secrets / "AFT_MCP_READ_ONLY")]
    assert rows["AFT_MCP_PASSWORD"] == [cli.MASK, "environment"]
    assert rows["AFT_MCP_TOKEN"] == ["unset", "default"]
    # Read from the environment only.
    assert rows["AFT_MCP_ENV_FILE"] == [str(env_file), "environment"]
    assert rows["AFT_MCP_SECRETS_DIR"] == [str(secrets), "environment"]
    assert rows["SSH_CONNECTION"][-1] == "environment"
    assert rows["CREDENTIALS_DIRECTORY"] == ["unset", "default"]
    assert aft_env["AFT_MCP_PASSWORD"] not in out


def test_installs_the_aft_mcp_command() -> None:
    (script,) = entry_points(group="console_scripts", name="aft-mcp")
    assert script.value == "aft_mcp.cli:main"


def test_python_m_aft_mcp_runs_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "main", lambda: calls.append("main"))
    runpy.run_module("aft_mcp", run_name="__main__")
    assert calls == ["main"]


def test_main_runs_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "app", lambda: calls.append("app"))
    cli.main()
    assert calls == ["app"]
