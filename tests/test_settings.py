from pathlib import Path

import pytest

from aft_mcp import settings as settings_module
from aft_mcp.settings import SettingsError, load_settings

ALL_SETTINGS = [
    "AFT_BASE_URL",
    "AFT_SPEC_URL",
    "AFT_SPEC_FILE",
    "AFT_TIMEOUT",
    "AFT_USERNAME",
    "AFT_PASSWORD",
    "AFT_ENV_FILE",
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
    assert str(loaded.spec_url) == aft_env["AFT_SPEC_URL"]
    assert loaded.spec_file is None
    assert loaded.timeout == 5.0
    assert loaded.username == "agent@example.com"
    assert loaded.password.get_secret_value() == "secret"


@pytest.mark.usefixtures("no_env")
def test_reads_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    lines = [
        "AFT_BASE_URL=https://aft.test",
        "AFT_SPEC_FILE=spec.json",
        "AFT_TIMEOUT=2",
        "AFT_USERNAME=agent@example.com",
        "AFT_PASSWORD=secret",
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
    env_file.write_text("AFT_TIMEOUT=99\n", encoding="utf-8")
    assert load_settings(env_file=env_file).timeout == 5.0


def test_env_file_is_dot_env_in_the_working_directory_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AFT_ENV_FILE", raising=False)
    assert settings_module.env_file_path() == Path(".env")


def test_aft_env_file_names_another_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AFT_ENV_FILE", str(tmp_path / "aft.env"))
    assert settings_module.env_file_path() == tmp_path / "aft.env"


def test_a_missing_env_file_leaves_the_environment(aft_env: dict[str, str]) -> None:
    missing = Path("no-such-dir") / ".env"
    assert load_settings(env_file=missing).username == aft_env["AFT_USERNAME"]


@pytest.mark.usefixtures("no_env")
def test_reports_every_missing_setting_at_once() -> None:
    assert problems() == [
        "AFT_BASE_URL: Field required",
        "AFT_TIMEOUT: Field required",
        "AFT_USERNAME: Field required",
        "AFT_PASSWORD: Field required",
    ]


def test_needs_a_spec_url_or_file(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.delenv("AFT_SPEC_URL")
    assert problems() == ["set AFT_SPEC_URL or AFT_SPEC_FILE"]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "soon"])
def test_rejects_a_bad_timeout(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str], value: str
) -> None:
    monkeypatch.setenv("AFT_TIMEOUT", value)
    (problem,) = problems()
    assert problem.startswith("AFT_TIMEOUT: ")


def test_rejects_a_base_url_that_is_not_http(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_BASE_URL", "aft.test")
    (problem,) = problems()
    assert problem.startswith("AFT_BASE_URL: ")


def test_an_empty_value_counts_as_unset(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    monkeypatch.setenv("AFT_SPEC_FILE", "")
    assert load_settings(env_file=None).spec_file is None
    monkeypatch.setenv("AFT_PASSWORD", "")
    assert problems() == ["AFT_PASSWORD: Field required"]


def test_the_password_never_appears_in_output(
    monkeypatch: pytest.MonkeyPatch, aft_env: dict[str, str]
) -> None:
    loaded = load_settings(env_file=None)
    assert "secret" not in repr(loaded)
    assert "secret" not in str(loaded)
    monkeypatch.setenv("AFT_TIMEOUT", "never")
    with pytest.raises(SettingsError) as raised:
        load_settings(env_file=None)
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_the_error_message_lists_each_problem() -> None:
    error = SettingsError(["AFT_A: bad", "AFT_B: bad"])
    assert str(error) == (
        "Invalid AFT settings (set them in .env):\n  - AFT_A: bad\n  - AFT_B: bad"
    )
