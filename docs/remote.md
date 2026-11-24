# Running aft-mcp on a server

This is optional. For one machine, or credentials you're happy to keep locally, run aft-mcp on the workstation as in
the [quickstart](../README.md#quickstart).

With several workstations, aft-mcp can run on one server instead, and each workstation reaches it over SSH. The AFT
token or password then lives only on the server, and each workstation has its own SSH key, so you can cut off one
machine by removing its key, without resetting the credential the others share.

MCP over stdio makes this simple: a client only needs a command whose stdin and stdout carry the protocol, so
`ssh aft-broker prod` works as well as a local command. aft-mcp needs no HTTP transport or authentication of its own.

```text
Claude Code ── stdio ── ssh aft-broker prod ── SSH ── forced command ── aft-mcp ── https ── AFT
```

## On the server

### A dedicated account

Create an account such as `aft-broker` with a locked password, no extra groups (in particular not `docker`), and no
certificate logins. It only ever runs the broker script.

```bash
useradd --create-home --shell /bin/sh aft-broker
passwd --lock aft-broker
```

Give it a real shell such as `/bin/sh`, not `nologin`: sshd runs the forced command through the account's login shell,
so with `nologin` the broker never starts. `restrict` and `command=` (below) are what stop it from getting a shell.

### One `authorized_keys` line per workstation

```text
restrict,command="/usr/local/bin/aft-mcp-broker laptop prod,test" ssh-ed25519 AAAA... laptop
```

- `restrict` turns off port, agent and X11 forwarding and the PTY.
- `command=` means this key can only run the broker, whatever the client asks for. The workstation's request (`prod`)
  arrives as `SSH_ORIGINAL_COMMAND`.
- The arguments name the workstation (for the log) and the instances it may reach.
- Make the file and its directory owned by root, so the account can't add keys itself, and watch it with
  file-integrity monitoring:

  ```bash
  install -d -o root -g root -m 755 /home/aft-broker/.ssh
  install -o root -g root -m 644 authorized_keys /home/aft-broker/.ssh/authorized_keys
  ```

### Settings stay on the server

Keep one settings file per instance, such as `/etc/aft-broker/prod.env`, owned `root:aft-broker` with mode `0640`, in
a directory only root and the account can enter (`install -d -o root -g aft-broker -m 750 /etc/aft-broker`):

```dotenv
AFT_MCP_BASE_URL=https://aft.example.com
AFT_MCP_TOKEN=<token>
```

Have your configuration management create it empty and never overwrite it, and fill it in with `sudoedit`, so no
secret passes through a repository or the deployment tool. To keep the token out of the file too, use
[secret files](clients.md#secrets-in-files).

### Install a pinned aft-mcp

Install a reviewed release, or a reviewed commit, with every dependency pinned by hash:

```bash
git clone https://github.com/samcorky/aft-mcp /opt/aft-mcp
cd /opt/aft-mcp && git checkout <reviewed-ref>
UV_PYTHON_INSTALL_DIR=/opt/aft-mcp/python uv sync --locked --no-dev --no-editable
```

`UV_PYTHON_INSTALL_DIR` matters: run as root, uv otherwise installs the Python from `.python-version` under
`/root/.local/share/uv`, which the `aft-broker` account can't read, and the broker fails with `Permission denied`.

`--locked` installs exactly what `uv.lock` records, checks each package's hash, and fails if the lock doesn't match
`pyproject.toml`. `--no-editable` copies aft-mcp into `.venv`, so file-integrity monitoring can watch `.venv` alone. uv
is only needed at deploy time; the broker runs `/opt/aft-mcp/.venv/bin/aft-mcp`.

Check each instance once after a deploy: `/opt/aft-mcp/.venv/bin/aft-mcp --env-file /etc/aft-broker/prod.env check`
loads the settings, fetches the spec and signs in with one harmless read.

### The broker script

`/usr/local/bin/aft-mcp-broker`, owned by root:

```sh
#!/bin/sh
# Run by sshd for a broker key: aft-mcp-broker <workstation> <allowed instances>
set -u
workstation=$1
allowed=$2
instance=${SSH_ORIGINAL_COMMAND:-}

# Only a plain name, and only one this key may reach.
case $instance in
  '' | *[!a-z0-9-]*) instance_ok=no ;;
  *) case ",$allowed," in *",$instance,"*) instance_ok=yes ;; *) instance_ok=no ;; esac ;;
esac
if [ "$instance_ok" != yes ]; then
  logger -p auth.warning -t aft-mcp-broker "$workstation refused instance '$instance'"
  echo "aft-mcp-broker: instance not allowed" >&2
  exit 1
fi

logger -p auth.info -t aft-mcp-broker "$workstation start $instance"
# A clean environment: nothing from the SSH session reaches aft-mcp except
# SSH_CONNECTION, which tags every log line with the client's address.
env -i PATH=/usr/bin:/bin \
  SSH_CONNECTION="${SSH_CONNECTION:-}" \
  AFT_MCP_INSTANCE="$instance" \
  AFT_MCP_LOG_SYSLOG=true \
  AFT_MCP_LOG_SYSLOG_FACILITY=auth \
  AFT_MCP_LOG_STDERR=false \
  /opt/aft-mcp/.venv/bin/aft-mcp --env-file "/etc/aft-broker/$instance.env"
status=$?
logger -p auth.info -t aft-mcp-broker "$workstation end $instance ($status)"
exit "$status"
```

The settings it passes:

- `--env-file` picks the instance's settings file; it must exist.
- `AFT_MCP_INSTANCE` names the instance in the server's name, its instructions to the agent and every log line, so the
  agent can tell `prod` from `test`.
- `AFT_MCP_LOG_SYSLOG` and `AFT_MCP_LOG_STDERR=false` keep the audit log on the server. Over SSH, stderr goes back to the
  workstation, so without them the tool-level log would end up there instead. With `SSH_CONNECTION` passed through,
  every line carries `ssh_client`.

aft-mcp also refuses an `http://` AFT URL unless `AFT_MCP_ALLOW_INSECURE=true`, and refuses a spec from another host.

This setup has been run end to end: in a container with a locked `aft-broker` account and the files above, an MCP
client connected with `ssh -T aft-broker prod` and called tools against a real AFT, the audit log reached syslog under
`auth` with `instance` and `ssh_client` on each line, the client received nothing on stderr, and a shell, a PTY, port
forwarding, an instance the key may not reach, a password login and the account adding a key were all refused.

## On each workstation

Add a host alias with that workstation's broker key to `~/.ssh/config`, and pin the server's host key:

```text
Host aft-broker
    HostName broker.example.com
    User aft-broker
    IdentityFile ~/.ssh/aft-broker
    IdentitiesOnly yes
    UserKnownHostsFile ~/.ssh/broker_known_hosts
    StrictHostKeyChecking yes
    LogLevel ERROR
    RequestTTY no
```

`LogLevel ERROR` and `RequestTTY no` (or `ssh -T`) keep banners off stdout, which carries the protocol. Then add one
MCP server per instance:

```bash
claude mcp add aft-prod -- ssh -T aft-broker prod
claude mcp add aft-test -- ssh -T aft-broker test
```

In Claude Desktop, use `"command": "ssh", "args": ["-T", "aft-broker", "prod"]`.

## Revoking access

- **One workstation:** remove its key from `authorized_keys` (or delete its public key and re-run the deployment). The
  credential doesn't change.
- **Everything:** revoke the AFT token, or disable the AFT user.

## Layers

The broker controls where the credential lives and which workstations can use it. What the credential can do is
limited separately, and the layers work together:

1. **AFT:** the account's roles, ideally an API-only user with a token, edit rights only on the boards it works on.
2. **aft-mcp:** the tools it exposes (no deletes, no board-level writes), plus `AFT_MCP_READ_ONLY` and
   `AFT_MCP_BOARD_IDS`; see [Access and auditing](access.md).
3. **The client:** allow the read tools (`mcp__aft-prod__get_*`) and approve each write.

## Other broker shapes

A broker can also hand out a short-lived token, which the client then uses to call the API directly, or make
allowlisted API calls itself so a long-lived token never leaves the server. aft-mcp is the second shape, with the whole
MCP server as the proxy.
