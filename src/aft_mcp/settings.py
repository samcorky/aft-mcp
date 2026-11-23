"""Settings for the AFT MCP server, loaded and validated once at startup.

Values come from the process environment first, then from a ``.env`` file: the one
named by ``AFT_MCP_ENV_FILE``, or else ``aft-mcp/.env`` in the user's config directory
(see ``env_file_path``). The working directory is never read, so starting the server
inside someone else's project can't change where it connects. Only the base URL and a
way to sign in are required; a missing or invalid setting stops the server, and the
error names every bad setting at once.
"""

import os
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urljoin, urlsplit

from pydantic import (
    Field,
    HttpUrl,
    PositiveInt,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX = "AFT_MCP_"
# Names the .env file to read. It is not a setting, so it comes from the environment.
ENV_FILE_VARIABLE = f"{ENV_PREFIX}ENV_FILE"
# The directory under the user's config directory that holds the default .env.
CONFIG_DIR_NAME = "aft-mcp"
# Where AFT serves its own spec, relative to the base URL.
DEFAULT_SPEC_PATH = "api/apispec.json"
# Seconds to wait for AFT before a request fails.
DEFAULT_TIMEOUT = 30

_TOKEN, _USER, _PASSWORD = (
    f"{ENV_PREFIX}{n}" for n in ("TOKEN", "USERNAME", "PASSWORD")
)
_BASE_URL, _SPEC_URL, _ALLOW_INSECURE = (
    f"{ENV_PREFIX}{n}" for n in ("BASE_URL", "SPEC_URL", "ALLOW_INSECURE")
)
NO_SIGN_IN = f"set {_TOKEN}, or both {_USER} and {_PASSWORD}"
TWO_SIGN_INS = f"set {_TOKEN} or {_USER} and {_PASSWORD}, not both"


class SettingsError(RuntimeError):
    """One or more settings are missing or invalid."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"Invalid AFT settings (set them in .env):\n{lines}")


class Settings(BaseSettings):
    """Every setting the server reads; docs/settings.md is generated from it."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # AFT_MCP_SPEC_FILE= in .env means unset, not "."
        extra="ignore",  # .env may hold settings for other tools
        frozen=True,
    )

    # scripts/settings_reference.py writes .env.example and docs/settings.md from
    # these descriptions and examples: run `uv run poe settings-docs` after a change.
    base_url: HttpUrl = Field(
        description=(
            "Base URL the generated tools call. It must use https unless "
            + f"`{ENV_PREFIX}ALLOW_INSECURE` is set."
        ),
        examples=["https://aft.example.com"],
    )
    spec_url: HttpUrl | None = Field(
        default=None,
        description=(
            "URL of AFT's Swagger 2.0 or OpenAPI spec, which the tools are "
            + f"built from. Unset, it is `{DEFAULT_SPEC_PATH}` under "
            + f"`{ENV_PREFIX}BASE_URL`. It must be on the same host and port as "
            + f"`{ENV_PREFIX}BASE_URL`, and so must any redirect it follows."
        ),
        examples=["https://aft.example.com/api/apispec.json"],
    )
    spec_file: Path | None = Field(
        default=None,
        description="Load the spec from this local file instead of `AFT_MCP_SPEC_URL`.",
    )
    timeout: float = Field(
        default=DEFAULT_TIMEOUT,
        gt=0,
        allow_inf_nan=False,
        description="Request timeout in seconds, for the spec and every tool call.",
    )
    # Sign in with an API token (sent as a Bearer token), or with a username and
    # password (sent as HTTP Basic auth). Exactly one of the two.
    token: SecretStr | None = Field(
        default=None,
        min_length=1,
        description=(
            "AFT API token, sent as `Authorization: Bearer` (recommended). Create "
            + "it on an API-only user; set this, or both "
            + f"`{_USER}` and `{_PASSWORD}`. It is never shown in reprs, logs "
            + "or errors."
        ),
    )
    username: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "AFT account email or username, sent as HTTP basic auth, for AFT "
            + f"versions without API tokens. Needs `{_PASSWORD}`; not with "
            + f"`{_TOKEN}`. Use a dedicated user for the agent, not your own account."
        ),
        examples=["agent@example.com"],
    )
    password: SecretStr | None = Field(
        default=None,
        min_length=1,
        description=(
            f"Password of the `{_USER}` account. It is never shown in reprs, "
            + "logs or errors."
        ),
    )
    allow_insecure: bool = Field(
        default=False,
        description=(
            f"Allow `http://` for `{_BASE_URL}` and `{_SPEC_URL}`, for an AFT on a "
            + "trusted network or VPN. The credentials are then sent unencrypted, "
            + "and the server warns about it on every start."
        ),
    )
    # Optional switches: unset means off, the behaviour before they existed.
    read_only: bool = Field(
        default=False,
        description="Expose only the safe reads, so no tool can change anything.",
    )
    board_ids: Annotated[frozenset[PositiveInt] | None, NoDecode] = Field(
        default=None,
        description=(
            "Only let the tools reach these boards; unset allows every "
            + "board the AFT user can see."
        ),
        examples=["12,15"],
    )
    log_format: Literal["logfmt", "json"] = Field(
        default="logfmt",
        description=(
            "How the log on stderr is written: logfmt lines, or one JSON object "
            + "per line for log pipelines."
        ),
        examples=["json"],
    )

    @field_validator("board_ids", mode="before")
    @classmethod
    def _split_board_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _urls_are_safe(self) -> Self:
        # A spec file replaces the spec URL, so the URL isn't checked then.
        spec_url = None if self.spec_file is not None else self.spec_url
        urls = {_BASE_URL: str(self.base_url)}
        if spec_url is not None:
            urls[_SPEC_URL] = str(spec_url)
        for name, url in urls.items():
            if url_scheme(url) != "https" and not self.allow_insecure:
                raise ValueError(
                    f"{name} must use https; set {_ALLOW_INSECURE}=true to allow "
                    + "http on a trusted network"
                )
        if spec_url is not None and url_origin(str(spec_url)) != url_origin(
            str(self.base_url)
        ):
            raise ValueError(
                f"{_SPEC_URL} must be on the same host and port as {_BASE_URL}; "
                + f"load a spec from elsewhere with {ENV_PREFIX}SPEC_FILE"
            )
        return self

    @model_validator(mode="after")
    def _needs_one_way_to_sign_in(self) -> Self:
        basic = (self.username, self.password)
        if self.token is not None:
            if basic != (None, None):
                raise ValueError(TWO_SIGN_INS)
        elif None in basic:
            raise ValueError(NO_SIGN_IN)
        return self

    @property
    def insecure_urls(self) -> list[str]:
        """The URL settings that use plain http (allowed by ``allow_insecure``)."""
        urls = [(_BASE_URL, str(self.base_url))]
        if self.spec_file is None:
            urls.append((_SPEC_URL, self.spec_location))
        return [name for name, url in urls if url_scheme(url) != "https"]

    @property
    def spec_location(self) -> str:
        """The spec URL to fetch: ``spec_url``, or AFT's own under ``base_url``."""
        if self.spec_url is not None:
            return str(self.spec_url)
        return urljoin(str(self.base_url), DEFAULT_SPEC_PATH)


_DEFAULT_PORTS = {"http": 80, "https": 443}


def url_scheme(url: str) -> str:
    """The URL's scheme, lower case."""
    return urlsplit(url).scheme.lower()


def url_origin(url: str) -> tuple[str, int | None]:
    """The host and port a URL connects to, with the scheme's default port filled in.

    The scheme is left out, so ``http://aft.test:443`` and ``https://aft.test`` count
    as the same host and port; ``url_scheme`` checks the scheme separately.
    """
    parts = urlsplit(url)
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())
    return (parts.hostname or "").lower(), port


def _describe(error: ValidationError) -> list[str]:
    """One line per problem, naming the variable; input values are left out."""
    problems = []
    for detail in error.errors(include_input=False):
        message = detail["msg"].removeprefix("Value error, ")
        if detail["loc"]:
            name = f"{ENV_PREFIX}{str(detail['loc'][0]).upper()}"
            message = f"{name}: {message}"
        problems.append(message)
    return problems


def _on_windows() -> bool:
    return os.name == "nt"


def default_env_file() -> Path:
    """``aft-mcp/.env`` in the user's config directory.

    ``$XDG_CONFIG_HOME`` (when absolute) or ``~/.config`` on Linux and macOS, and
    ``%APPDATA%`` on Windows.
    """
    if _on_windows():
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME") or "")
        base = xdg if xdg.is_absolute() else Path.home() / ".config"
    return base / CONFIG_DIR_NAME / ".env"


def env_file_path() -> Path:
    """The ``.env`` file to read: ``AFT_MCP_ENV_FILE`` if set, else the default.

    The default may be missing: the settings then come from the environment alone, as
    when an MCP client passes them in its own configuration. A file named explicitly
    must exist, so a typo isn't silently ignored.

    Raises:
        SettingsError: ``AFT_MCP_ENV_FILE`` names a file that doesn't exist.
    """
    named = os.environ.get(ENV_FILE_VARIABLE)
    if not named:
        return default_env_file()
    path = Path(named).expanduser()
    if not path.is_file():
        raise SettingsError([f"{ENV_FILE_VARIABLE}: no such file: {path}"])
    return path


def load_settings(env_file: Path | None) -> Settings:
    """Read and validate every setting.

    Args:
        env_file: The ``.env`` file to read after the environment (usually
            ``env_file_path()``), or None to read the environment only, as the
            tests do so that a developer's ``.env`` never applies. A missing file is
            fine: the settings then come from the environment alone, as when an MCP
            client passes them in its own configuration.

    Raises:
        SettingsError: Naming every missing or invalid setting.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        raise SettingsError(_describe(error)) from None
