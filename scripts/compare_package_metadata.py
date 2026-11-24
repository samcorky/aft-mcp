"""Check the Docker image and the wheel carry the same aft-mcp package metadata.

The image installs aft-mcp with ``uv sync`` and a ``VERSION`` build argument, while
the wheel for PyPI is built by hatch-vcs from the git tags. They should describe the
same release; this fails the build when they don't, before either is published.

Usage::

    python scripts/compare_package_metadata.py dist/aft_mcp-*.whl aft-mcp:local

It reads the wheel's ``METADATA`` and runs ``importlib.metadata`` inside the image,
then compares the fields below. Run by ``build.yml``, in CI and before publishing.
"""

import json
import subprocess
import sys
import zipfile
from email.parser import HeaderParser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

PACKAGE = "aft-mcp"
# What makes two builds the same release; Requires-Dist is compared as a set.
FIELDS = (
    "Name",
    "Version",
    "Summary",
    "License-Expression",
    "Requires-Python",
    "Requires-Dist",
)
# The image's Python; distroless has no shell or python on PATH to call otherwise.
IMAGE_PYTHON = "/app/.venv/bin/python"
_IN_IMAGE = """\
import importlib.metadata as m, json, sys
d = m.metadata(sys.argv[1])
print(json.dumps({f: d.get_all(f) or [] for f in sys.argv[2:]}))
"""

type Metadata = dict[str, list[str]]
type Runner = Callable[[list[str]], str]


def parse_metadata(text: str) -> Metadata:
    """The compared fields of a core-metadata file, each as a sorted list."""
    headers = HeaderParser().parsestr(text)
    return {field: sorted(headers.get_all(field) or []) for field in FIELDS}


def wheel_metadata(wheel: Path) -> Metadata:
    """Read the compared fields from a wheel's ``*.dist-info/METADATA``."""
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            msg = f"{wheel} has {len(names)} METADATA files, not 1"
            raise ValueError(msg)
        return parse_metadata(archive.read(names[0]).decode("utf-8"))


def _docker(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def image_metadata(image: str, run: Runner = _docker) -> Metadata:
    """Read the compared fields from the package installed in ``image``."""
    output = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            IMAGE_PYTHON,
            image,
            "-c",
            _IN_IMAGE,
            PACKAGE,
            *FIELDS,
        ]
    )
    found: dict[str, list[str]] = json.loads(output)
    return {field: sorted(found.get(field, [])) for field in FIELDS}


def differences(wheel: Metadata, image: Metadata) -> list[str]:
    """One line per field that differs, naming both values."""
    return [
        f"{field}: wheel {wheel[field]!r}, image {image[field]!r}"
        for field in FIELDS
        if wheel[field] != image[field]
    ]


def main(argv: Sequence[str], run: Runner = _docker) -> int:
    """Compare a wheel with an image; print the result and return 0 or 1."""
    if len(argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} WHEEL IMAGE", file=sys.stderr)
        return 2
    wheel, image = Path(argv[0]), argv[1]
    found = differences(wheel_metadata(wheel), image_metadata(image, run))
    if found:
        print(f"{wheel.name} and {image} differ:", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"{wheel.name} and {image} carry the same {PACKAGE} metadata")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
