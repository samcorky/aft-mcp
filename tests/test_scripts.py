"""Every script in scripts/ declares what it needs as PEP 723 inline metadata."""

import re
import tomllib
from pathlib import Path

import pytest

SCRIPTS = sorted((Path(__file__).resolve().parents[1] / "scripts").glob("*.py"))
# The reference regular expression from PEP 723.
BLOCK = re.compile(
    r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$"
)


def script_metadata(text: str) -> dict[str, object]:
    """The ``script`` block's TOML, as PEP 723 reads it."""
    blocks = [m for m in BLOCK.finditer(text) if m.group("type") == "script"]
    if len(blocks) != 1:
        msg = f"{len(blocks)} script blocks, not 1"
        raise ValueError(msg)
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in blocks[0].group("content").splitlines(keepends=True)
    )
    return tomllib.loads(content)


def test_there_are_scripts() -> None:
    assert SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_each_script_has_inline_metadata(script: Path) -> None:
    metadata = script_metadata(script.read_text(encoding="utf-8"))
    assert metadata["requires-python"] == ">=3.14"
    assert isinstance(metadata["dependencies"], list)


def test_a_script_without_a_block_is_refused() -> None:
    with pytest.raises(ValueError, match="0 script blocks"):
        script_metadata('"""No metadata."""\n')
