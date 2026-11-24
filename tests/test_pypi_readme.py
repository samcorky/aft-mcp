"""The README PyPI shows, rendered by hatch-fancy-pypi-readme as the build does."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pypi_readme() -> str:
    """The description the wheel and sdist carry, built by pyproject.toml's rules."""
    return subprocess.run(
        [sys.executable, "-m", "hatch_fancy_pypi_readme", str(ROOT / "pyproject.toml")],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout


def test_every_link_is_absolute(pypi_readme: str) -> None:
    # PyPI has no repository to resolve a relative link against, so it would 404.
    inline = re.findall(r"\]\(((?!https?://)(?!#)[^)\s]+)\)", pypi_readme)
    references = re.findall(r"(?m)^\[[^\]]+\]: ((?!https?://)\S+)$", pypi_readme)
    assert inline == []
    assert references == []
    assert (
        "https://github.com/samcorky/aft-mcp/blob/main/docs/settings.md" in pypi_readme
    )


def test_it_is_the_user_facing_readme(pypi_readme: str) -> None:
    assert pypi_readme.startswith("An [MCP](https://modelcontextprotocol.io) server")
    assert '<div align="center">' not in pypi_readme  # no badge header
    sections = re.findall(r"(?m)^## (.+)$", pypi_readme)
    assert sections == [
        "What is exposed",
        "Quickstart",
        "Documentation",
        "How it works",
        "Licence",
    ]
