"""scripts/compare_package_metadata.py: the image and the wheel describe one release."""

import json
import zipfile
from typing import TYPE_CHECKING

import compare_package_metadata as compare
import pytest

if TYPE_CHECKING:
    from pathlib import Path

METADATA = """\
Metadata-Version: 2.5
Name: aft-mcp
Version: 2026.11.0
Summary: An MCP server.
License-Expression: MIT
Requires-Python: >=3.14
Requires-Dist: fastmcp>=4.1.4
Requires-Dist: cyclopts>=5.3.2
Description-Content-Type: text/markdown

# aft-mcp

Requires-Dist: not a header, part of the description.
"""


def make_wheel(tmp_path: Path, metadata: str = METADATA) -> Path:
    wheel = tmp_path / "aft_mcp-2026.11.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("aft_mcp-2026.11.0.dist-info/METADATA", metadata)
        archive.writestr("aft_mcp/__init__.py", "")
    return wheel


def image_runner(found: dict[str, list[str]], seen: list[list[str]]) -> compare.Runner:
    def run(command: list[str]) -> str:
        seen.append(command)
        return json.dumps(found)

    return run


def test_reads_the_wheels_headers_only(tmp_path: Path) -> None:
    metadata = compare.wheel_metadata(make_wheel(tmp_path))
    assert metadata["Version"] == ["2026.11.0"]
    assert metadata["Requires-Dist"] == ["cyclopts>=5.3.2", "fastmcp>=4.1.4"]
    assert set(metadata) == set(compare.FIELDS)


def test_a_wheel_needs_exactly_one_metadata_file(tmp_path: Path) -> None:
    wheel = tmp_path / "broken.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("aft_mcp/__init__.py", "")
    with pytest.raises(ValueError, match="has 0 METADATA files"):
        compare.wheel_metadata(wheel)


def test_runs_importlib_metadata_inside_the_image() -> None:
    seen: list[list[str]] = []
    found = {"Version": ["2026.11.0"], "Requires-Dist": ["b", "a"]}
    metadata = compare.image_metadata("aft-mcp:local", image_runner(found, seen))
    assert metadata["Requires-Dist"] == ["a", "b"]  # order doesn't matter
    assert metadata["Summary"] == []  # missing fields are empty
    (command,) = seen
    assert command[:6] == [
        "docker",
        "run",
        "--rm",
        "--entrypoint",
        compare.IMAGE_PYTHON,
        "aft-mcp:local",
    ]
    assert command[-len(compare.FIELDS) - 1 :] == [compare.PACKAGE, *compare.FIELDS]


def test_matching_metadata_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = make_wheel(tmp_path)
    same = compare.wheel_metadata(wheel)
    assert compare.main([str(wheel), "aft-mcp:local"], image_runner(same, [])) == 0
    assert "carry the same aft-mcp metadata" in capsys.readouterr().out


def test_a_different_version_or_dependency_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = make_wheel(tmp_path)
    other = compare.wheel_metadata(wheel) | {
        "Version": ["0.0.0"],
        "Requires-Dist": ["fastmcp>=4.1.4"],
    }
    assert compare.main([str(wheel), "aft-mcp:local"], image_runner(other, [])) == 1
    err = capsys.readouterr().err
    assert "Version: wheel ['2026.11.0'], image ['0.0.0']" in err
    assert "Requires-Dist:" in err
    assert "Summary" not in err


def test_needs_a_wheel_and_an_image(capsys: pytest.CaptureFixture[str]) -> None:
    assert compare.main(["only-one"]) == 2
    assert "usage:" in capsys.readouterr().err
