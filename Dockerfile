# aft-mcp over stdio, as a container: docker run -i --rm --env-file prod.env <image>
# Built with docker buildx bake (docker-bake.hcl), as menagerist's image is: uv installs
# the Python from .python-version and the locked dependencies, and the runtime is
# distroless, with no shell, package manager or pip.

FROM debian:trixie-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0 AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 \
    /uv /bin/

WORKDIR /app

ENV \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

COPY .python-version ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv python install

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

# Trim what a stdio server never uses, as menagerist does.
RUN PYTHON_DIR="$(find /opt/python -mindepth 1 -maxdepth 1 -type d -name 'cpython-*' -print -quit)" \
    && STDLIB="$(find "${PYTHON_DIR}/lib" -mindepth 1 -maxdepth 1 -type d -name 'python3.*' -print -quit)" \
    && [ -n "${STDLIB}" ] || { echo "missing Python stdlib directory" >&2; exit 1; } \
    && PY_MM="${STDLIB##*/python}" \
    && rm -rf \
        "${PYTHON_DIR}/include" \
        "${PYTHON_DIR}/lib/tcl"* \
        "${PYTHON_DIR}/lib/tk"* \
        "${PYTHON_DIR}/lib/libtcl"* \
        "${PYTHON_DIR}/lib/libtk"* \
        "${STDLIB}/idlelib" \
        "${STDLIB}/tkinter" \
        "${STDLIB}/turtledemo" \
        "${STDLIB}/ensurepip" \
        "${STDLIB}/site-packages/pip" \
        "${STDLIB}/site-packages/setuptools" \
        "${STDLIB}"/config-"${PY_MM}"* \
    && find /opt/python /app/.venv \
        \( -type d -name 'tests' -o -type d -name 'test' \) -prune -exec rm -rf '{}' + \
    && find /opt/python /app/.venv \
        \( -name '*.pyo' -o -name '*.a' -o -name '_test*.so' -o -name '_ctypes_test*.so' \
           -o -name 'xxlimited*.so' -o -name 'xxsubtype*.so' \) -delete \
    && du -sh "${PYTHON_DIR}" /app/.venv

# libgcc_s is needed at runtime (pydantic-core is a Rust extension that unwinds through
# it) but distroless base doesn't ship it. Mirror it from this build's own multiarch
# directory, so the COPY below works on every platform bake builds for.
RUN for lib in libgcc_s.so.1; do \
        src="$(find /usr/lib -maxdepth 2 -name "${lib}" -print -quit)"; \
        [ -n "${src}" ] || { echo "missing ${lib}" >&2; exit 1; }; \
        dest="/opt/runtime-libs${src#/usr/lib}"; \
        mkdir -p "$(dirname "${dest}")"; \
        cp "${src}" "${dest}"; \
    done


FROM gcr.io/distroless/base-debian13:nonroot@sha256:83e67481ae9fc8c7a6d211113431fb8085619bed49672e583261111bac184f0d AS runtime

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

COPY --from=builder /opt/runtime-libs/ /usr/lib/

WORKDIR /app

ENV \
    PATH="/opt/python/bin:/app/.venv/bin" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/python /opt/python
COPY --from=builder /app/.venv /app/.venv

# Settings come from the environment (docker run --env-file), or from secret files
# with AFT_MCP_SECRETS_DIR=/run/secrets. The image holds no .env of its own.
USER 1000:1000

ENTRYPOINT ["aft-mcp"]
