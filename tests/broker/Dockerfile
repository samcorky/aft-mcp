# A broker host set up as docs/remote.md describes, for tests/test_broker.py only.
# The test builds it from a context holding aft-mcp's source and the broker script it
# takes from the doc itself.
FROM debian:trixie-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server busybox-syslogd ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd \
    && ssh-keygen -A

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 \
    /uv /usr/local/bin/uv

# The account, as the doc creates it: a real shell, a locked password, no extra groups.
RUN useradd --create-home --shell /bin/sh aft-broker && passwd --lock aft-broker

# The broker script, taken unchanged from docs/remote.md by the test.
COPY aft-mcp-broker /usr/local/bin/aft-mcp-broker
RUN chmod 755 /usr/local/bin/aft-mcp-broker

# aft-mcp, installed with the doc's command. The test checks the command matches the
# doc. The context has no .git, so the version is given.
COPY aft-mcp /opt/aft-mcp
RUN cd /opt/aft-mcp \
    && SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 UV_PYTHON_INSTALL_DIR=/opt/aft-mcp/python uv sync --locked --no-dev --no-editable \
    && su -s /bin/sh aft-broker -c "/opt/aft-mcp/.venv/bin/aft-mcp version"

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
