from pathlib import Path

import pytest

import aft_mcp.settings as settings_module
from aft_mcp.settings import (
    SessionSettings,
    Settings,
    SettingsError,
    ToolSettings,
    default_env_file,
    load_paths,
    load_settings,
)
from tests.support import changed

ALL_SETTINGS = [
    "AFT_MCP_BASE_URL",
    "AFT_MCP_SPEC_URL",
    "AFT_MCP_SPEC_FILE",
    "AFT_MCP_TIMEOUT",
    "AFT_MCP_USERNAME",
    "AFT_MCP_PASSWORD",
    "AFT_MCP_READ_ONLY",
    "AFT_MCP_BOARD_IDS",
    "AFT_MCP_LOG_FORMAT",
    "AFT_MCP_TOKEN",
]


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ALL_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def problems(env_file: Path | None = None) -> list[str]:
    with pytest.raises(SettingsError) as raised:
        load_settings(env_file=env_file)
    return raised.value.problems


def test_loads_every_setting_from_the_environment(aft_env: dict[str, str]) -> None:
    loaded = load_settings(env_file=None)
    assert str(loaded.connection.base_url) == "https://aft.test/"
    assert str(loaded.connection.spec_url) == aft_env["AFT_MCP_SPEC_URL"]
    assert loaded.connection.spec_file is None
    assert loaded.connection.timeout == 5.0
    assert loaded.auth.token is None
    assert loaded.auth.username == "agent@example.com"
    assert loaded.auth.password is not None
    assert loaded.auth.password.get_secret_value() == "secret"


@pytest.mark.usefixtures("no_env")
def test_reads_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    lines = [
        "AFT_MCP_BASE_URL=https://aft.test",
        "AFT_MCP_SPEC_FILE=spec.json",
        "AFT_MCP_TIMEOUT=2",
        "AFT_MCP_USERNAME=agent@example.com",
        "AFT_MCP_PASSWORD=secret",
        "OTHER_TOOL=1",  # settings for other tools are ignored
    ]
    env_file.write_text("\n".join(lines), encoding="utf-8")
    loaded = load_settings(env_file=env_file)
    assert loaded.connection.spec_file is not None
    assert loaded.connection.spec_file.name == "spec.json"
    assert loaded.connection.spec_url is None


def test_the_environment_wins_over_the_env_file(
    aft_env: dict[str, str], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AFT_MCP_TIMEOUT=99\n", encoding="utf-8")
    assert load_settings(env_file=env_file).connection.timeout == 5.0


# --- where the .env file is --------------------------------------------------------


def test_the_default_env_file_is_in_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_env_file() == tmp_path / "aft-mcp" / ".env"


@pytest.mark.parametrize("xdg", [None, "", "relative/dir"])
def test_without_an_absolute_xdg_config_home_it_is_under_dot_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, xdg: str | None
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    if xdg is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    assert default_env_file() == tmp_path / ".config" / "aft-mcp" / ".env"


@pytest.mark.parametrize("appdata", [True, False])
def test_on_windows_it_is_under_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, appdata: bool
) -> None:
    monkeypatch.setattr(settings_module, "_on_windows", lambda: True)
    monkeypatch.setenv("HOME", str(tmp_path))
    if appdata:
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    else:
        monkeypatch.delenv("APPDATA", raising=False)
    expected = tmp_path / ("Roaming" if appdata else "AppData/Roaming") / "aft-mcp"
    assert default_env_file() == expected / ".env"


def test_env_file_path_is_the_default_unless_named(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    assert load_paths().env_file_path == default_env_file()
    monkeypatch.setenv("AFT_MCP_ENV_FILE", "")  # empty counts as unset
    assert load_paths().env_file_path == default_env_file()


def test_env_file_path_uses_a_named_file(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], tmp_path: Path
) -> None:
    named = tmp_path / "prod.env"
    named.write_text("", encoding="utf-8")
    monkeypatch.setenv("AFT_MCP_ENV_FILE", str(named))
    assert load_paths().env_file_path == named
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AFT_MCP_ENV_FILE", "~/prod.env")
    assert load_paths().env_file_path == named


def test_a_named_env_file_must_exist(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], tmp_path: Path
) -> None:
    monkeypatch.setenv("AFT_MCP_ENV_FILE", str(tmp_path / "typo.env"))
    with pytest.raises(SettingsError) as raised:
        load_paths()
    assert raised.value.problems == [
        f"AFT_MCP_ENV_FILE: no such file: {tmp_path / 'typo.env'}"
    ]


def test_a_missing_env_file_leaves_the_environment(aft_env: dict[str, str]) -> None:
    missing = Path("no-such-dir") / ".env"
    assert load_settings(env_file=missing).auth.username == aft_env["AFT_MCP_USERNAME"]


@pytest.mark.usefixtures("no_env")
def test_reports_every_missing_setting_at_once() -> None:
    # Each group is checked on its own, so a missing base URL doesn't hide the
    # missing sign-in.
    assert problems() == [
        "AFT_MCP_BASE_URL: Field required",
        "set AFT_MCP_TOKEN, or both AFT_MCP_USERNAME and AFT_MCP_PASSWORD",
    ]


def test_the_timeout_defaults_to_30_seconds(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.delenv("AFT_MCP_TIMEOUT")
    assert load_settings(env_file=None).connection.timeout == 30


@pytest.mark.parametrize(
    ("base_url", "spec"),
    [
        ("https://aft.test", "https://aft.test/api/apispec.json"),
        ("https://aft.test/", "https://aft.test/api/apispec.json"),
        # AFT served under a path keeps that path.
        ("https://host.test/aft/", "https://host.test/aft/api/apispec.json"),
    ],
)
def test_the_spec_url_defaults_to_afts_own(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], base_url: str, spec: str
) -> None:
    monkeypatch.delenv("AFT_MCP_SPEC_URL")
    monkeypatch.setenv("AFT_MCP_BASE_URL", base_url)
    loaded = load_settings(env_file=None)
    assert loaded.connection.spec_url is None
    assert loaded.connection.spec_location == spec


def test_a_set_spec_url_is_used_as_is(settings: Settings) -> None:
    assert settings.connection.spec_location == "https://aft.test/api/apispec.json"
    other = changed(settings, {"spec_url": "https://elsewhere.test/s.json"})
    assert other.connection.spec_location == "https://elsewhere.test/s.json"


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "soon"])
def test_rejects_a_bad_timeout(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str
) -> None:
    monkeypatch.setenv("AFT_MCP_TIMEOUT", value)
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_TIMEOUT: ")


def test_rejects_a_base_url_that_is_not_http(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_BASE_URL", "aft.test")
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_BASE_URL: ")


def test_an_empty_value_counts_as_unset(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_SPEC_FILE", "")
    assert load_settings(env_file=None).connection.spec_file is None
    monkeypatch.setenv("AFT_MCP_PASSWORD", "")
    assert problems() == [
        "set AFT_MCP_TOKEN, or both AFT_MCP_USERNAME and AFT_MCP_PASSWORD"
    ]


def test_read_only_is_off_unless_set(aft_env: dict[str, str]) -> None:
    assert load_settings(env_file=None).tools.read_only is False


@pytest.mark.parametrize(
    ("value", "expected"), [("true", True), ("1", True), ("false", False), ("0", False)]
)
def test_read_only_accepts_true_or_false(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str, expected: bool
) -> None:
    monkeypatch.setenv("AFT_MCP_READ_ONLY", value)
    assert load_settings(env_file=None).tools.read_only is expected


def test_read_only_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_READ_ONLY", "maybe")
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_READ_ONLY: ")


def test_log_format_is_logfmt_unless_set(aft_env: dict[str, str]) -> None:
    assert load_settings(env_file=None).log.format == "logfmt"


def test_log_format_accepts_json_and_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_LOG_FORMAT", "json")
    assert load_settings(env_file=None).log.format == "json"
    monkeypatch.setenv("AFT_MCP_LOG_FORMAT", "xml")
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_LOG_FORMAT: ")


def test_board_ids_allow_every_board_unless_set(aft_env: dict[str, str]) -> None:
    assert load_settings(env_file=None).tools.board_ids is None


@pytest.mark.parametrize(
    ("value", "expected"), [("12", [12]), ("12,15", [12, 15]), (" 12 , 15, ", [12, 15])]
)
def test_board_ids_are_a_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch,
    aft_env: dict[str, str],
    value: str,
    expected: list[int],
) -> None:
    monkeypatch.setenv("AFT_MCP_BOARD_IDS", value)
    assert load_settings(env_file=None).tools.board_ids == expected


@pytest.mark.parametrize("value", ["twelve", "12;15", "0", "-3"])
def test_board_ids_must_be_positive_numbers(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str
) -> None:
    monkeypatch.setenv("AFT_MCP_BOARD_IDS", value)
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_BOARD_IDS: ")


def test_board_ids_can_be_given_as_a_list(aft_env: dict[str, str]) -> None:
    loaded = ToolSettings(_env_file=None, board_ids=[12])
    assert loaded.board_ids == [12]


# --- signing in -------------------------------------------------------------------


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]) -> None:
    """Sign in with a token instead of the username and password from ``aft_env``."""
    monkeypatch.delenv("AFT_MCP_USERNAME")
    monkeypatch.delenv("AFT_MCP_PASSWORD")
    monkeypatch.setenv("AFT_MCP_TOKEN", "aft_token_value")


@pytest.mark.usefixtures("token_env")
def test_loads_a_token_instead_of_a_username_and_password() -> None:
    loaded = load_settings(env_file=None)
    assert loaded.auth.token is not None
    assert loaded.auth.token.get_secret_value() == "aft_token_value"
    assert loaded.auth.username is None
    assert loaded.auth.password is None


@pytest.mark.usefixtures("token_env")
@pytest.mark.parametrize("name", ["AFT_MCP_USERNAME", "AFT_MCP_PASSWORD"])
def test_a_token_and_a_username_or_password_is_an_error(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "x")
    assert problems() == [
        "set AFT_MCP_TOKEN or AFT_MCP_USERNAME and AFT_MCP_PASSWORD, not both"
    ]


@pytest.mark.parametrize("name", ["AFT_MCP_USERNAME", "AFT_MCP_PASSWORD"])
def test_a_username_needs_a_password_and_the_other_way_round(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], name: str
) -> None:
    monkeypatch.delenv(name)
    assert problems() == [
        "set AFT_MCP_TOKEN, or both AFT_MCP_USERNAME and AFT_MCP_PASSWORD"
    ]


@pytest.mark.usefixtures("token_env")
def test_the_token_never_appears_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = load_settings(env_file=None)
    assert "aft_token_value" not in repr(loaded)
    assert "aft_token_value" not in str(loaded)
    monkeypatch.setenv("AFT_MCP_USERNAME", "agent@example.com")
    with pytest.raises(SettingsError) as raised:
        load_settings(env_file=None)
    assert "aft_token_value" not in str(raised.value)


def test_the_password_never_appears_in_output(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    loaded = load_settings(env_file=None)
    assert "secret" not in repr(loaded)
    assert "secret" not in str(loaded)
    monkeypatch.setenv("AFT_MCP_TIMEOUT", "never")
    with pytest.raises(SettingsError) as raised:
        load_settings(env_file=None)
    assert "secret" not in str(raised.value)
    # No chained pydantic error, whose input values could hold the password.
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_the_error_message_lists_each_problem() -> None:
    error = SettingsError(["AFT_MCP_A: bad", "AFT_MCP_B: bad"])
    assert str(error) == (
        "Invalid AFT settings (set them in .env):\n"
        + "  - AFT_MCP_A: bad\n"
        + "  - AFT_MCP_B: bad"
    )


# --- https and the spec's host ------------------------------------------------------


@pytest.mark.parametrize("name", ["AFT_MCP_BASE_URL", "AFT_MCP_SPEC_URL"])
def test_http_is_refused_unless_allowed(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], name: str
) -> None:
    monkeypatch.setenv("AFT_MCP_BASE_URL", "http://aft.test")
    monkeypatch.setenv("AFT_MCP_SPEC_URL", "http://aft.test/api/apispec.json")
    if name == "AFT_MCP_SPEC_URL":
        monkeypatch.setenv("AFT_MCP_BASE_URL", "https://aft.test:80")
    (problem,) = problems()
    assert problem.startswith(f"{name} must use https")
    monkeypatch.setenv("AFT_MCP_ALLOW_INSECURE", "true")
    assert load_settings(env_file=None).connection.allow_insecure is True


@pytest.mark.parametrize(
    "spec_url",
    [
        "https://elsewhere.test/api/apispec.json",
        "https://aft.test:8443/api/apispec.json",
    ],
)
def test_the_spec_url_must_be_on_the_base_urls_host_and_port(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], spec_url: str
) -> None:
    monkeypatch.setenv("AFT_MCP_SPEC_URL", spec_url)
    monkeypatch.setenv("AFT_MCP_ALLOW_INSECURE", "true")  # doesn't relax this rule
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_SPEC_URL must be on the same host and port")


def test_the_spec_url_may_use_another_path_or_explicit_default_port(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_SPEC_URL", "https://AFT.test:443/other/spec.json")
    assert load_settings(env_file=None).connection.spec_location == (
        "https://aft.test/other/spec.json"
    )


def test_insecure_urls_lists_the_http_settings_in_use(
    aft_env: dict[str, str],
) -> None:
    loaded = load_settings(env_file=None)
    assert loaded.connection.insecure_urls == []
    plain = changed(loaded, {"base_url": "http://aft.test", "spec_url": None})
    assert plain.connection.insecure_urls == ["AFT_MCP_BASE_URL", "AFT_MCP_SPEC_URL"]
    from_file = changed(plain, {"spec_file": Path("spec.json")})
    assert from_file.connection.insecure_urls == ["AFT_MCP_BASE_URL"]


def test_a_spec_file_makes_the_spec_url_unchecked(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_SPEC_URL", "http://elsewhere.test/spec.json")
    monkeypatch.setenv("AFT_MCP_SPEC_FILE", "spec.json")
    assert load_settings(env_file=None).connection.spec_file == Path("spec.json")


# --- secrets from files -------------------------------------------------------------


def test_no_secrets_dir_unless_named(aft_env: dict[str, str]) -> None:
    assert load_paths().secrets_dir_path is None


@pytest.mark.parametrize("variable", ["AFT_MCP_SECRETS_DIR", "CREDENTIALS_DIRECTORY"])
def test_the_secrets_dir_comes_from_either_variable(
    monkeypatch: pytest.MonkeyPatch,
    aft_env: dict[str, str],
    tmp_path: Path,
    variable: str,
) -> None:
    monkeypatch.setenv(variable, str(tmp_path))
    assert load_paths().secrets_dir_path == tmp_path


def test_aft_mcp_secrets_dir_wins_over_systemds(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "ours").mkdir()
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("AFT_MCP_SECRETS_DIR", str(tmp_path / "ours"))
    assert load_paths().secrets_dir_path == tmp_path / "ours"


def test_a_named_secrets_dir_must_exist(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], tmp_path: Path
) -> None:
    monkeypatch.setenv("AFT_MCP_SECRETS_DIR", str(tmp_path / "missing"))
    with pytest.raises(SettingsError) as raised:
        load_paths()
    assert raised.value.problems == [
        f"AFT_MCP_SECRETS_DIR: no such directory: {tmp_path / 'missing'}"
    ]


def test_a_secret_file_provides_the_token(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], tmp_path: Path
) -> None:
    monkeypatch.delenv("AFT_MCP_USERNAME")
    monkeypatch.delenv("AFT_MCP_PASSWORD")
    (tmp_path / "AFT_MCP_TOKEN").write_text("from-a-file\n", encoding="utf-8")
    loaded = load_settings(env_file=None, secrets_dir=tmp_path)
    assert loaded.auth.token is not None
    assert loaded.auth.token.get_secret_value() == "from-a-file"
    assert "from-a-file" not in repr(loaded)


def test_the_environment_wins_over_a_secret_file(
    aft_env: dict[str, str], tmp_path: Path
) -> None:
    (tmp_path / "AFT_MCP_PASSWORD").write_text("from-a-file", encoding="utf-8")
    loaded = load_settings(env_file=None, secrets_dir=tmp_path)
    assert loaded.auth.password is not None
    assert loaded.auth.password.get_secret_value() == aft_env["AFT_MCP_PASSWORD"]


def test_the_instance_is_unset_by_default(aft_env: dict[str, str]) -> None:
    assert load_settings(env_file=None).tools.instance is None


@pytest.mark.parametrize("value", ["Prod", "prod env", "-prod", "x" * 33, "prod\n"])
def test_the_instance_is_a_short_lower_case_name(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str
) -> None:
    monkeypatch.setenv("AFT_MCP_INSTANCE", "prod-2")
    assert load_settings(env_file=None).tools.instance == "prod-2"
    monkeypatch.setenv("AFT_MCP_INSTANCE", value)
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_INSTANCE: ")


def test_logging_goes_to_stderr_only_by_default(aft_env: dict[str, str]) -> None:
    loaded = load_settings(env_file=None)
    assert (loaded.log.stderr, loaded.log.syslog) == (True, False)
    assert loaded.log.syslog_facility == "user"


def test_turning_off_stderr_needs_syslog(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_LOG_STDERR", "false")
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_LOG_STDERR=false needs AFT_MCP_LOG_SYSLOG=true")
    monkeypatch.setenv("AFT_MCP_LOG_SYSLOG", "true")
    monkeypatch.setenv("AFT_MCP_LOG_SYSLOG_FACILITY", "authpriv")
    assert load_settings(env_file=None).log.syslog_facility == "authpriv"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("203.0.113.5 52114 192.0.2.1 22", "203.0.113.5:52114"), ("", None), (None, None)],
)
def test_the_ssh_client_comes_from_ssh_connection(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
    else:
        monkeypatch.setenv("SSH_CONNECTION", value)
    assert SessionSettings().ssh_client == expected


def test_every_group_reports_its_problems_at_once(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_TIMEOUT", "never")
    monkeypatch.setenv("AFT_MCP_READ_ONLY", "maybe")
    monkeypatch.setenv("AFT_MCP_LOG_FORMAT", "xml")
    assert [problem.split(":")[0] for problem in problems()] == [
        "AFT_MCP_TIMEOUT",
        "AFT_MCP_READ_ONLY",
        "AFT_MCP_LOG_FORMAT",
    ]
