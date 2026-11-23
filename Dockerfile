# aft-mcp over stdio, as a container: docker run -i --rm --env-file prod.env <image>
# Built with docker buildx bake (docker-bake.hcl), as menagerist's image is.

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS builder

COPY --from=ghcr.io/astral-sh/uv@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 \
    /uv /bin/

WORKDIR /app

ENV \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/aft-mcp

# The repository is not in the build context, so the version arrives as a build
# argument, worked out from the tags by CI (calver-scm). Without it the package is 0.0.0.
ARG VERSION=""

# Dependencies first, so this layer is cached until uv.lock changes. --locked
# installs exactly what uv.lock records, checks every hash, and fails if the lock no
# longer matches pyproject.toml.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION:-0.0.0} \
    uv sync --locked --no-install-project

COPY README.md LICENSE ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION:-0.0.0} \
    uv sync --locked --no-editable


FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS runtime

ARG VERSION=""
ARG AFT_MCP_BUILD_COMMIT_SHA=""
ARG AFT_MCP_BUILD_TIMESTAMP=""

LABEL org.opencontainers.image.title="aft-mcp" \
      org.opencontainers.image.description="An MCP server that gives AI agents safe, scoped access to AFT boards." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/samcorky/aft-mcp" \
      org.opencontainers.image.url="https://github.com/samcorky/aft-mcp" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${AFT_MCP_BUILD_COMMIT_SHA}" \
      org.opencontainers.image.created="${AFT_MCP_BUILD_TIMESTAMP}"

ENV \
    PATH="/opt/aft-mcp/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/aft-mcp /opt/aft-mcp

# Settings come from the environment (docker run --env-file), or from secret files
# with AFT_MCP_SECRETS_DIR=/run/secrets. The image holds no .env of its own.
USER 1000:1000

ENTRYPOINT ["aft-mcp"]
