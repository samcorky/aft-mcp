"""scripts/settings_reference.py: what it writes into `.env.example` and the docs.

Whether the committed files are current is checked by `poe settings-docs-check`, in CI
and a pre-commit hook, so these tests work on fresh output instead.
"""

import os
from typing import TYPE_CHECKING, Any

import pytest
import settings_reference
from settings_reference import (
    ENV_EXAMPLE_PATH,
    SETTINGS_DOC_PATH,
    build_sections,
    main,
    reference_files,
    render_env_example,
    render_settings_doc,
    write_reference_files,
)

from aft_mcp.settings import ConnectionSettings, SettingsError, load_settings

if TYPE_CHECKING:
    from pathlib import Path


def _fake_schema(monkeypatch: pytest.MonkeyPatch, properties: dict[str, Any]) -> None:
    schema = {"properties": properties, "required": []}
    monkeypatch.setattr(ConnectionSettings, "model_json_schema", lambda: schema)
    monkeypatch.setattr(
        settings_reference, "GROUPS", {"connection": ConnectionSettings}
    )


@pytest.fixture
def no_aft_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("AFT_MCP_")]:
        monkeypatch.delenv(name)


@pytest.mark.usefixtures("no_aft_env")
def test_the_example_loads_once_a_way_to_sign_in_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every example is a value the settings accept; only the sign-in is missing."""
    env_example = tmp_path / ENV_EXAMPLE_PATH
    env_example.write_text(reference_files()[ENV_EXAMPLE_PATH], encoding="utf-8")
    with pytest.raises(SettingsError, match=r"set AFT_MCP_TOKEN, or both"):
        load_settings(env_file=env_example)
    monkeypatch.setenv("AFT_MCP_TOKEN", "aft_token_value")
    loaded = load_settings(env_file=env_example)
    assert loaded.auth.token is not None
    assert loaded.auth.username is None
    assert loaded.tools.read_only is False
    assert loaded.tools.board_ids is None


@pytest.mark.usefixtures("no_aft_env")
def test_uncommented_optional_examples_load_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An optional example, such as the board list, is valid once uncommented."""
    text = reference_files()[ENV_EXAMPLE_PATH]
    env_file = tmp_path / ".env"
    env_file.write_text(text.replace("# AFT_MCP_", "AFT_MCP_"), encoding="utf-8")
    monkeypatch.setenv("AFT_MCP_PASSWORD", "secret")
    assert load_settings(env_file=env_file).tools.board_ids == [12, 15]


def test_needed_settings_are_set_and_optional_ones_commented() -> None:
    """A copy works once filled in; the optional switches stay off."""
    lines = reference_files()[ENV_EXAMPLE_PATH].splitlines()
    assert "AFT_MCP_BASE_URL=https://aft.example.com" in lines
    assert "# AFT_MCP_SPEC_URL=https://aft.example.com/api/apispec.json" in lines
    assert "# AFT_MCP_TOKEN=" in lines  # sign in with the token, or the pair below
    assert "# AFT_MCP_USERNAME=agent@example.com" in lines
    assert "# AFT_MCP_PASSWORD=" in lines
    assert "# AFT_MCP_READ_ONLY=false" in lines
    assert "# AFT_MCP_BOARD_IDS=12,15" in lines
    assert "# Required: yes." in lines


def test_the_reference_describes_each_variable() -> None:
    """Types, defaults and allowed values are spelled out in the table."""
    doc = reference_files()[SETTINGS_DOC_PATH]
    assert "| `AFT_MCP_TIMEOUT` | no | number | `30` | more than 0 |" in doc
    assert "| `AFT_MCP_READ_ONLY` | no | true or false | `false` | any |" in doc
    assert "comma-separated list of whole numbers | unset | each more than 0" in doc
    assert "| `AFT_MCP_TOKEN` | no | secret | unset | not empty |" in doc
    # Environment-only settings are documented under their real names, aliases too,
    # but never offered in .env.example.
    assert "## Read from the environment only" in doc
    for name in ("AFT_MCP_ENV_FILE", "AFT_MCP_SECRETS_DIR", "CREDENTIALS_DIRECTORY"):
        assert f"| `{name}` | no | path | unset |" in doc
    assert "| `SSH_CONNECTION` | no | text | unset |" in doc
    env_example = reference_files()[ENV_EXAMPLE_PATH]
    assert "SSH_CONNECTION" not in env_example
    assert "CREDENTIALS_DIRECTORY" not in env_example
    assert "| `AFT_MCP_LOG_FORMAT` | no | text | `logfmt` | `logfmt` or `json` |" in doc


def test_a_secret_never_gets_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even an example on a secret field is left out of both files."""
    _fake_schema(
        monkeypatch,
        {
            "token": {
                "description": "A token.",
                "format": "password",
                "examples": ["hunter2"],
            }
        },
    )
    sections = build_sections()
    for text in (render_env_example(sections), render_settings_doc(sections)):
        assert "hunter2" not in text
    assert "# AFT_MCP_TOKEN=" in render_env_example(sections)


def test_values_a_dotenv_reader_would_split_are_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value with a space or `|` is quoted in `.env.example` and escaped in tables."""
    _fake_schema(
        monkeypatch,
        {
            "note": {"description": "A | B.", "default": "two words"},
            "tags": {
                "description": "Tags.",
                "anyOf": [{"type": "array", "items": {"type": "string"}}],
            },
            "blob": {"description": "Anything.", "type": "object"},
        },
    )
    sections = build_sections()
    assert '# AFT_MCP_NOTE="two words"' in render_env_example(sections)
    doc = render_settings_doc(sections)
    assert "| `AFT_MCP_NOTE` | no | text | `two words` | any | A \\| B. |" in doc
    assert (
        "| `AFT_MCP_TAGS` | no | comma-separated list of texts | unset | any |" in doc
    )
    assert "| `AFT_MCP_BLOB` | no | text | unset | any |" in doc


def test_a_field_without_a_description_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An undocumented setting fails the generator rather than shipping blank."""
    _fake_schema(monkeypatch, {"mystery": {"type": "string"}})
    with pytest.raises(
        ValueError, match=r"ConnectionSettings\.mystery needs a description"
    ):
        build_sections()


def test_only_stale_files_are_written(tmp_path: Path) -> None:
    """The first run writes both files, and a second finds nothing to do."""
    assert sorted(write_reference_files(tmp_path)) == sorted(reference_files())
    assert write_reference_files(tmp_path) == []
    assert (tmp_path / ENV_EXAMPLE_PATH).read_text(encoding="utf-8").startswith("#")


def test_main_writes_the_files_under_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("Updated .env.example")
    assert main(["--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "Settings reference is up to date"


def test_check_fails_on_a_stale_file_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--check` is what CI and the pre-commit hook run."""
    assert main(["--root", str(tmp_path), "--check"]) == 1
    assert "Out of date: .env.example, docs/settings.md" in capsys.readouterr().err
    assert not (tmp_path / ENV_EXAMPLE_PATH).exists()
    write_reference_files(tmp_path)
    assert main(["--root", str(tmp_path), "--check"]) == 0
