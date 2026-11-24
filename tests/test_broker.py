"""The SSH broker setup in docs/remote.md, end to end.

A container is set up as the doc describes: a locked ``aft-broker`` account, a
root-owned ``authorized_keys`` whose key may only run the broker, the instance's
settings owned ``root:aft-broker 0640``, aft-mcp installed with the doc's ``uv sync``
command, and the broker script from ``deploy/ssh-broker``. An MCP client then reaches
it the way a workstation does, ``ssh -T aft-broker prod``, against a fake AFT on this
machine. Nothing here touches a real AFT or the machine's own SSH setup.

Needs Docker and ssh, and skips itself without them. It is part of ``poe test`` (and
so CI's test job); ``poe test-fast`` leaves it out as an ``e2e`` test, and
``uv run poe test-broker`` runs it alone.
"""

import json
import shutil
import subprocess
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent
from threading import Thread
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.e2e, pytest.mark.docker, pytest.mark.anyio]

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "remote.md"
SCRIPT = ROOT / "deploy" / "ssh-broker" / "aft-mcp-broker"
BROKER_DIR = Path(__file__).resolve().parent / "broker"
TOKEN = "e2e-token"
# The doc's commands; the test Dockerfile must run the install exactly as given.
INSTALL = (
    "UV_PYTHON_INSTALL_DIR=/opt/aft-mcp/python uv sync --locked --no-dev --no-editable"
)
INSTALL_SCRIPT = (
    "install -o root -g root -m 755 /opt/aft-mcp/deploy/ssh-broker/aft-mcp-broker "
    + "/usr/local/bin/aft-mcp-broker"
)
# What the container's copy of aft-mcp is built from.
SOURCES = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    ".python-version",
    "src",
)
BOARDS = [{"id": 12, "name": "Development"}]
SPEC: dict[str, Any] = {
    "swagger": "2.0",
    "info": {"title": "AFT", "version": "1"},
    "basePath": "/",
    "paths": {"/api/boards": {"get": {"responses": {"200": {"description": "OK"}}}}},
}


def docker_ready() -> bool:
    """Whether Docker is running and ssh and ssh-keygen are installed."""
    if not all(shutil.which(tool) for tool in ("docker", "ssh", "ssh-keygen")):
        return False
    info = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return info.returncode == 0


if not docker_ready():
    pytestmark.append(pytest.mark.skip(reason="needs Docker, ssh and ssh-keygen"))


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing its output as text."""
    return subprocess.run(command, capture_output=True, text=True, check=check)


class FakeAft(BaseHTTPRequestHandler):
    """Serves the spec to anyone and the boards to the right token; records requests."""

    seen: list[tuple[str, str]] = []  # noqa: RUF012 - shared with the test, on purpose

    def do_GET(self) -> None:
        """Answer the spec and boards requests."""
        self.seen.append((self.path, self.headers.get("Authorization", "")))
        if self.path == "/api/apispec.json":  # AFT serves its spec to anyone
            self._send(200, SPEC)
        elif self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(401, {"error": "unauthorised"})
        elif self.path == "/api/boards":
            self._send(200, BOARDS)
        else:
            self._send(404, {"error": "not found"})

    def _send(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the test output quiet."""


class Broker:
    """A running broker container and how to reach it."""

    def __init__(self, name: str, ssh_config: Path) -> None:
        """Remember the container's name and the workstation's ssh config."""
        self.name = name
        self.ssh_config = ssh_config

    def ssh(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run ssh with the test workstation's config, stdin closed."""
        return subprocess.run(
            ["ssh", "-F", str(self.ssh_config), *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )

    def wait_for_log(self, text: str, timeout: float = 15) -> str:
        """Wait until ``text`` is in the host's auth.log; return the log."""
        deadline = time.monotonic() + timeout
        while True:
            log = self.exec("cat /var/log/auth.log")
            if text in log or time.monotonic() > deadline:
                return log
            time.sleep(0.25)

    def exec(self, script: str) -> str:
        """Run a shell script as root in the container; return its stdout."""
        return run("docker", "exec", self.name, "sh", "-c", script).stdout


def build_context(context: Path) -> None:
    """The image's build context: the Dockerfile, aft-mcp's source and the script."""
    (context / "aft-mcp").mkdir(parents=True)
    for name in SOURCES:
        source, target = ROOT / name, context / "aft-mcp" / name
        if source.is_dir():
            shutil.copytree(
                source, target, ignore=shutil.ignore_patterns("__pycache__")
            )
        else:
            shutil.copy2(source, target)
    for name in ("Dockerfile", "entrypoint.sh"):
        shutil.copy2(BROKER_DIR / name, context / name)
    shutil.copy2(SCRIPT, context / "aft-mcp-broker")


def wait_for_sshd(name: str, port: str) -> str:
    """Wait until sshd answers on ``port``; return its ed25519 host key."""
    for _ in range(60):
        key = run(
            "docker", "exec", name, "cat", "/etc/ssh/ssh_host_ed25519_key.pub",
            check=False,
        ).stdout  # fmt: skip
        if key and run("ssh-keyscan", "-p", port, "127.0.0.1", check=False).stdout:
            return " ".join(key.split()[:2])
        time.sleep(0.5)
    msg = "sshd in the broker container did not start"
    raise RuntimeError(msg)


@pytest.fixture(scope="module")
def broker(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Broker]:
    """Build and start the broker host, with a fake AFT it can reach."""
    work = tmp_path_factory.mktemp("broker")
    build_context(work / "context")
    image = "aft-mcp-broker-e2e"
    run("docker", "build", "-q", "-t", image, str(work / "context"))

    # The fake AFT listens on Docker's bridge gateway only, which the container
    # reaches as aft.test.
    gateway = run(
        "docker", "network", "inspect", "bridge",
        "--format", "{{(index .IPAM.Config 0).Gateway}}",
    ).stdout.strip()  # fmt: skip
    fake = ThreadingHTTPServer((gateway, 0), FakeAft)
    Thread(target=fake.serve_forever, daemon=True).start()

    mount = work / "run"
    mount.mkdir(mode=0o700)
    key = mount / "client_key"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "laptop", "-f", str(key))
    (mount / "prod.env").write_text(
        dedent(f"""\
            AFT_MCP_BASE_URL=http://aft.test:{fake.server_address[1]}
            AFT_MCP_ALLOW_INSECURE=true
            AFT_MCP_TOKEN={TOKEN}
        """),
        encoding="utf-8",
    )
    name = f"aft-mcp-broker-e2e-{uuid.uuid4().hex[:8]}"
    run(
        "docker", "run", "-d", "--name", name,
        "--add-host", "aft.test:host-gateway",
        "-p", "127.0.0.1::22",
        "-v", f"{mount}:/run/test:ro",
        image,
    )  # fmt: skip
    try:
        port = run("docker", "port", name, "22/tcp").stdout.split(":")[-1].strip()
        known_hosts = work / "broker_known_hosts"
        known_hosts.write_text(
            f"[127.0.0.1]:{port} {wait_for_sshd(name, port)}\n", encoding="utf-8"
        )
        # The doc's workstation config, pointed at the container.
        ssh_config = work / "ssh_config"
        ssh_config.write_text(
            dedent(f"""\
                Host aft-broker
                    HostName 127.0.0.1
                    Port {port}
                    User aft-broker
                    IdentityFile {key}
                    IdentitiesOnly yes
                    UserKnownHostsFile {known_hosts}
                    StrictHostKeyChecking yes
                    LogLevel ERROR
                    RequestTTY no
            """),
            encoding="utf-8",
        )
        yield Broker(name, ssh_config)
    finally:
        run("docker", "rm", "-f", name, check=False)
        fake.shutdown()


def test_the_doc_and_the_test_use_the_same_files_and_commands() -> None:
    doc = DOC.read_text("utf-8")
    assert INSTALL in doc
    assert INSTALL in (BROKER_DIR / "Dockerfile").read_text("utf-8")
    assert INSTALL_SCRIPT in doc
    assert "(../deploy/ssh-broker/aft-mcp-broker)" in doc


async def test_an_mcp_client_reaches_aft_over_ssh(
    broker: Broker, tmp_path: Path
) -> None:
    FakeAft.seen.clear()
    stderr = tmp_path / "stderr.log"
    transport = StdioTransport(
        command="ssh",
        args=["-T", "-F", str(broker.ssh_config), "aft-broker", "prod"],
        log_file=stderr,
    )
    try:
        async with Client(transport) as client:
            names = {tool.name for tool in await client.list_tools()}
            result = await client.call_tool("get_boards", {})
    except Exception as error:
        log = broker.exec("cat /var/log/auth.log")
        check = broker.exec(
            "su -s /bin/sh aft-broker -c '/opt/aft-mcp/.venv/bin/aft-mcp"
            + " --env-file /etc/aft-broker/prod.env check' 2>&1 || true"
        )
        pytest.fail(
            f"MCP over SSH failed ({error}).\nThe broker host's log:\n{log}\n"
            + f"aft-mcp check on the broker host:\n{check}"
        )

    assert names == {"get_boards"}
    assert result.structured_content == {"result": BOARDS}
    assert ("/api/boards", f"Bearer {TOKEN}") in FakeAft.seen
    # The log stays on the server: nothing, not even a banner, comes back over SSH.
    assert stderr.read_text(encoding="utf-8") == ""

    # The broker logs the end once aft-mcp has exited, just after the client left.
    log = broker.wait_for_log("aft-mcp-broker: laptop end prod (0)")
    assert "aft-mcp-broker: laptop start prod" in log
    audit = [line for line in log.splitlines() if "event=tool_result" in line]
    assert audit, "no audit line reached syslog"
    assert "tool=get_boards outcome=ok" in audit[-1]
    assert "instance=prod" in audit[-1]
    assert "ssh_client=" in audit[-1]


@pytest.mark.parametrize(
    "asked",
    [
        "test",  # the key may reach prod only
        "/bin/sh -c id",  # a shell command
        "",  # nothing
    ],
)
def test_the_broker_refuses_anything_but_an_allowed_instance(
    broker: Broker, asked: str
) -> None:
    result = broker.ssh("-T", "aft-broker", *([asked] if asked else []))
    assert result.returncode == 1
    assert "aft-mcp-broker: instance not allowed" in result.stderr
    log = broker.exec("cat /var/log/auth.log")
    assert f"laptop refused instance '{asked}'" in log


def test_restrict_refuses_a_pty_and_port_forwarding(broker: Broker) -> None:
    pty = broker.ssh("-tt", "aft-broker", "staging")
    assert "PTY allocation request failed" in pty.stderr
    forward = broker.ssh(
        "-o", "ExitOnForwardFailure=yes", "-N",
        "-R", "127.0.0.1:2397:127.0.0.1:22", "aft-broker",
    )  # fmt: skip
    assert forward.returncode != 0
    assert "remote port forwarding failed" in forward.stderr


def test_password_login_is_refused(broker: Broker) -> None:
    result = broker.ssh(
        "-o", "PubkeyAuthentication=no",
        "-o", "PreferredAuthentications=password",
        "-o", "BatchMode=yes",
        "aft-broker", "prod",
    )  # fmt: skip
    assert result.returncode == 255
    assert "Permission denied" in result.stderr


def test_the_account_and_files_are_locked_down(broker: Broker) -> None:
    assert broker.exec("passwd -S aft-broker").split()[1] == "L"  # locked
    assert broker.exec("id -Gn aft-broker").split() == ["aft-broker"]
    owners = broker.exec(
        "stat -c '%U:%G %a' /home/aft-broker/.ssh"
        + " /home/aft-broker/.ssh/authorized_keys"
        + " /etc/aft-broker /etc/aft-broker/prod.env"
    ).splitlines()
    assert owners == [
        "root:root 755",
        "root:root 644",
        "root:aft-broker 750",
        "root:aft-broker 640",
    ]
    adding = broker.exec(
        "su -s /bin/sh aft-broker"
        + " -c 'echo x >> /home/aft-broker/.ssh/authorized_keys' 2>&1 || true"
    )
    assert "Permission denied" in adding
    readers = broker.exec(
        "useradd -M other 2>/dev/null || true;"
        + " su -s /bin/sh aft-broker -c 'test -r /etc/aft-broker/prod.env'"
        + " && echo broker;"
        + " su -s /bin/sh other -c 'test -r /etc/aft-broker/prod.env'"
        + " && echo other || true"
    )
    assert readers.split() == ["broker"]
