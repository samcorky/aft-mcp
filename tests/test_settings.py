from pathlib import Path

import pytest

from aft_mcp.settings import ENV_FILE, Settings, SettingsError, load_settings

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
    assert str(loaded.base_url) == "https://aft.test/"
    assert str(loaded.spec_url) == aft_env["AFT_MCP_SPEC_URL"]
    assert loaded.spec_file is None
    assert loaded.timeout == 5.0
    assert loaded.token is None
    assert loaded.username == "agent@example.com"
    assert loaded.password is not None
    assert loaded.password.get_secret_value() == "secret"


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
    assert loaded.spec_file is not None
    assert loaded.spec_file.name == "spec.json"
    assert loaded.spec_url is None


def test_the_environment_wins_over_the_env_file(
    aft_env: dict[str, str], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AFT_MCP_TIMEOUT=99\n", encoding="utf-8")
    assert load_settings(env_file=env_file).timeout == 5.0


def test_the_env_file_is_dot_env_in_the_working_directory() -> None:
    assert Path(".env") == ENV_FILE


def test_a_missing_env_file_leaves_the_environment(aft_env: dict[str, str]) -> None:
    missing = Path("no-such-dir") / ".env"
    assert load_settings(env_file=missing).username == aft_env["AFT_MCP_USERNAME"]


@pytest.mark.usefixtures("no_env")
def test_reports_every_missing_setting_at_once() -> None:
    assert problems() == [
        "AFT_MCP_BASE_URL: Field required",
        "AFT_MCP_TIMEOUT: Field required",
    ]


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
    assert loaded.spec_url is None
    assert loaded.spec_location == spec


def test_a_set_spec_url_is_used_as_is(settings: Settings) -> None:
    assert settings.spec_location == "https://aft.test/api/apispec.json"
    other = settings.model_copy(update={"spec_url": "https://elsewhere.test/s.json"})
    assert other.spec_location == "https://elsewhere.test/s.json"


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
    assert load_settings(env_file=None).spec_file is None
    monkeypatch.setenv("AFT_MCP_PASSWORD", "")
    assert problems() == [
        "set AFT_MCP_TOKEN, or both AFT_MCP_USERNAME and AFT_MCP_PASSWORD"
    ]


def test_read_only_is_off_unless_set(aft_env: dict[str, str]) -> None:
    assert load_settings(env_file=None).read_only is False


@pytest.mark.parametrize(
    ("value", "expected"), [("true", True), ("1", True), ("false", False), ("0", False)]
)
def test_read_only_accepts_true_or_false(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str, expected: bool
) -> None:
    monkeypatch.setenv("AFT_MCP_READ_ONLY", value)
    assert load_settings(env_file=None).read_only is expected


def test_read_only_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_READ_ONLY", "maybe")
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_READ_ONLY: ")


def test_log_format_is_logfmt_unless_set(aft_env: dict[str, str]) -> None:
    assert load_settings(env_file=None).log_format == "logfmt"


def test_log_format_accepts_json_and_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_MCP_LOG_FORMAT", "json")
    assert load_settings(env_file=None).log_format == "json"
    monkeypatch.setenv("AFT_MCP_LOG_FORMAT", "xml")
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_LOG_FORMAT: ")


def test_board_ids_allow_every_board_unless_set(aft_env: dict[str, str]) -> None:
    assert load_settings(env_file=None).board_ids is None


@pytest.mark.parametrize(
    ("value", "expected"), [("12", {12}), ("12,15", {12, 15}), (" 12 , 15, ", {12, 15})]
)
def test_board_ids_are_a_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch,
    aft_env: dict[str, str],
    value: str,
    expected: set[int],
) -> None:
    monkeypatch.setenv("AFT_MCP_BOARD_IDS", value)
    assert load_settings(env_file=None).board_ids == frozenset(expected)


@pytest.mark.parametrize("value", ["twelve", "12;15", "0", "-3"])
def test_board_ids_must_be_positive_numbers(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str
) -> None:
    monkeypatch.setenv("AFT_MCP_BOARD_IDS", value)
    (problem,) = problems()
    assert problem.startswith("AFT_MCP_BOARD_IDS: ")


def test_board_ids_can_be_given_as_a_set(aft_env: dict[str, str]) -> None:
    loaded = Settings(_env_file=None, board_ids=frozenset({12}))
    assert loaded.board_ids == frozenset({12})


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
    assert loaded.token is not None
    assert loaded.token.get_secret_value() == "aft_token_value"
    assert loaded.username is None
    assert loaded.password is None


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
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_the_error_message_lists_each_problem() -> None:
    error = SettingsError(["AFT_MCP_A: bad", "AFT_MCP_B: bad"])
    assert str(error) == (
        "Invalid AFT settings (set them in .env):\n"
        + "  - AFT_MCP_A: bad\n"
        + "  - AFT_MCP_B: bad"
    )
