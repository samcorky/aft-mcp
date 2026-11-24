"""Settings for the AFT MCP server, loaded and validated once at startup.

Settings are grouped, like menagerist's: each group is a pydantic-settings class
(``ConnectionSettings``, ``AuthSettings``, ``ToolSettings``, ``LogSettings``), and
``Settings`` holds one of each. A group declared with ``group="log"`` reads
``AFT_MCP_LOG_<FIELD>``; the others read ``AFT_MCP_<FIELD>``.

Values come from the process environment first, then from a ``.env`` file, then from
secret files. Where those two are is itself read from the environment
(``PathSettings``): ``AFT_MCP_ENV_FILE``, or else ``aft-mcp/.env`` in the user's
config directory, and ``AFT_MCP_SECRETS_DIR`` or systemd's ``CREDENTIALS_DIRECTORY``.
The working directory is never read, so starting the server inside someone else's
project can't change where it connects. Only the base URL and a way to sign in are
required; a missing or invalid setting stops the server, and the error names every
bad setting, in every group, at once.
"""

import os
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self
from urllib.parse import urljoin, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    PositiveInt,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import NoDecode, SettingsConfigDict

from aft_mcp._tracking import TrackedSettings
from aft_mcp._types import CSV  # noqa: TC001 - pydantic resolves the annotation at runtime

ENV_PREFIX = "AFT_MCP_"
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


class AftMcpSettings(TrackedSettings):
    """Base for a group of settings; the one place the ``AFT_MCP_`` prefix lives.

    A subclass declared with ``group="log"`` reads ``AFT_MCP_LOG_<FIELD>``. A field with
    a ``validation_alias`` reads that standard variable instead (``SSH_CONNECTION``).
    ``title`` heads the group in the generated settings reference. Each object records
    where each value came from (``source_of``), as menagerist's settings do.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # AFT_MCP_SPEC_FILE= in .env means unset, not "."
        extra="ignore",  # .env may hold settings for other tools and other groups
        frozen=True,
    )

    title: ClassVar[str] = ""

    def __init_subclass__(
        cls,
        group: str | None = None,
        **kwargs: Any,  # noqa: ANN401 - Pydantic class options
    ) -> None:
        """Accept ``group=`` as a class option, for ``__pydantic_init_subclass__``."""
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(
        cls,
        group: str | None = None,
        **kwargs: Any,  # noqa: ANN401 - Pydantic class options
    ) -> None:
        """Give a group's variables their own prefix, such as ``AFT_MCP_LOG_``."""
        super().__pydantic_init_subclass__(**kwargs)
        if group is not None:
            cls.model_config["env_prefix"] = f"{ENV_PREFIX}{group.upper()}_"

    @classmethod
    def env_name(cls, field: str) -> str:
        """The environment variable a field is read from."""
        alias = cls.model_fields[field].validation_alias
        if isinstance(alias, str):
            return alias
        return f"{cls.model_config.get('env_prefix', '')}{field.upper()}"


class ConnectionSettings(AftMcpSettings):
    """Where AFT is, and how the server reaches it and its spec."""

    title = "Connecting to AFT"

    # scripts/settings_reference.py writes .env.example and docs/settings.md from
    # these descriptions and examples: run `uv run poe settings-docs` after a change.
    base_url: HttpUrl = Field(
        description=(
            "Base URL the generated tools call. It must use https unless "
            + f"`{_ALLOW_INSECURE}` is set."
        ),
        examples=["https://aft.example.com"],
    )
    spec_url: HttpUrl | None = Field(
        default=None,
        description=(
            "URL of AFT's Swagger 2.0 or OpenAPI spec, which the tools are "
            + f"built from. Unset, it is `{DEFAULT_SPEC_PATH}` under "
            + f"`{_BASE_URL}`. It must be on the same host and port as "
            + f"`{_BASE_URL}`, and so must any redirect it follows."
        ),
        examples=["https://aft.example.com/api/apispec.json"],
    )
    spec_file: Path | None = Field(
        default=None,
        description=f"Load the spec from this local file instead of `{_SPEC_URL}`.",
    )
    timeout: float = Field(
        default=DEFAULT_TIMEOUT,
        gt=0,
        allow_inf_nan=False,
        description="Request timeout in seconds, for the spec and every tool call.",
    )
    allow_insecure: bool = Field(
        default=False,
        description=(
            f"Allow `http://` for `{_BASE_URL}` and `{_SPEC_URL}`, for an AFT on a "
            + "trusted network or VPN. The credentials are then sent unencrypted, "
            + "and the server warns about it on every start."
        ),
    )

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


class AuthSettings(AftMcpSettings):
    """How the server signs in: an API token, or a username and password."""

    title = "Signing in"

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

    @model_validator(mode="after")
    def _needs_one_way_to_sign_in(self) -> Self:
        basic = (self.username, self.password)
        if self.token is not None:
            if basic != (None, None):
                raise ValueError(TWO_SIGN_INS)
        elif None in basic:
            raise ValueError(NO_SIGN_IN)
        return self


class ToolSettings(AftMcpSettings):
    """What the tools may do, and which instance they act on."""

    title = "What the tools may do"

    read_only: bool = Field(
        default=False,
        description="Expose only the safe reads, so no tool can change anything.",
    )
    # NoDecode again on the whole type: inside "| None" pydantic-settings would
    # otherwise read "12" as JSON before the CSV split.
    board_ids: Annotated[CSV[PositiveInt] | None, NoDecode] = Field(
        default=None,
        description=(
            "Only let the tools reach these boards; unset allows every "
            + "board the AFT user can see."
        ),
        examples=["12,15"],
    )
    instance: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,31}$",
        description=(
            "A short name for this AFT instance, such as `prod` or `test`. It is "
            + "added to the server's name and instructions, so the agent knows "
            + "which instance it is changing, and to every log line."
        ),
        examples=["prod"],
    )


type SyslogFacility = Literal[
    "user",
    "auth",
    "authpriv",
    "daemon",
    "local0",
    "local1",
    "local2",
    "local3",
    "local4",
    "local5",
    "local6",
    "local7",
]


class LogSettings(AftMcpSettings, group="log"):
    """Where the log goes and how it is written."""

    title = "Logging"

    format: Literal["logfmt", "json"] = Field(
        default="logfmt",
        description=(
            "How the log is written: logfmt lines, or one JSON object per line for "
            + "log pipelines."
        ),
        examples=["json"],
    )
    stderr: bool = Field(
        default=True,
        description=(
            "Write the log to stderr. Over SSH, stderr goes back to the client, so "
            + "a server can set this to false and keep the log in syslog instead."
        ),
    )
    syslog: bool = Field(
        default=False,
        description=(
            "Also send the log to the local syslog (`/dev/log`), so a server keeps "
            + "the audit trail itself. A missing socket stops the server."
        ),
    )
    syslog_facility: SyslogFacility = Field(
        default="user",
        description=(
            "Syslog facility for the log; `auth` or `authpriv` puts it next to "
            + "sshd's lines."
        ),
        examples=["auth"],
    )

    @model_validator(mode="after")
    def _logs_somewhere(self) -> Self:
        if not self.stderr and not self.syslog:
            raise ValueError(
                f"{ENV_PREFIX}LOG_STDERR=false needs {ENV_PREFIX}LOG_SYSLOG=true, "
                + "or the log would go nowhere"
            )
        return self


# Every group, in the order the settings reference lists them.
GROUPS: dict[str, type[AftMcpSettings]] = {
    "connection": ConnectionSettings,
    "auth": AuthSettings,
    "tools": ToolSettings,
    "log": LogSettings,
}


class Settings(BaseModel):
    """Every setting the server reads, one attribute per group."""

    model_config = ConfigDict(frozen=True)

    connection: ConnectionSettings
    auth: AuthSettings
    tools: ToolSettings
    log: LogSettings


class PathSettings(AftMcpSettings):
    """Where the other settings are read from; from the environment only.

    These can't be in ``.env``, because they say which ``.env`` to read.
    """

    title = "Where the settings are read from"

    env_file: Path | None = Field(
        default=None,
        description=(
            "The `.env` file to read instead of `aft-mcp/.env` in your config "
            + "directory; it must exist."
        ),
    )
    secrets_dir: Path | None = Field(
        default=None,
        description=(
            "A directory of secret files, each named after a variable (such as "
            + f"`{_TOKEN}`) and holding its value; it must exist."
        ),
    )
    credentials_directory: Path | None = Field(
        default=None,
        validation_alias="CREDENTIALS_DIRECTORY",
        description=(
            "Set by systemd for `LoadCredential=`; the secret files' directory when "
            + f"`{ENV_PREFIX}SECRETS_DIR` isn't set."
        ),
    )

    @field_validator("env_file")
    @classmethod
    def _env_file_exists(cls, value: Path | None) -> Path | None:
        if value is not None:
            value = value.expanduser()
            if not value.is_file():
                msg = f"no such file: {value}"
                raise ValueError(msg)
        return value

    @field_validator("secrets_dir", "credentials_directory")
    @classmethod
    def _directory_exists(cls, value: Path | None) -> Path | None:
        if value is not None:
            value = value.expanduser()
            if not value.is_dir():
                msg = f"no such directory: {value}"
                raise ValueError(msg)
        return value

    @property
    def env_file_path(self) -> Path:
        """The ``.env`` to read: ``env_file``, else the default (may be missing)."""
        return self.env_file or default_env_file()

    @property
    def secrets_dir_path(self) -> Path | None:
        """The secret files' directory: ``secrets_dir``, else systemd's, else none.

        In it, a file named after a variable (``AFT_MCP_TOKEN``) holds that setting's
        value, with surrounding whitespace removed. This suits Docker secrets
        (``/run/secrets``) and systemd credentials (``LoadCredential=``).
        """
        return self.secrets_dir or self.credentials_directory


class SessionSettings(AftMcpSettings, group="session"):
    """Facts about the session the server runs in, from standard variables.

    ``SSH_CONNECTION`` is set by sshd, so it is read under its own name, not
    ``AFT_MCP_SESSION_*``; it is not a setting an operator writes.
    """

    title = "The session"

    ssh_connection: str | None = Field(
        default=None,
        validation_alias="SSH_CONNECTION",
        description=(
            "Set by sshd: the client's address and port, then the server's. When "
            + "set, every log line carries `ssh_client`."
        ),
    )

    @property
    def ssh_client(self) -> str | None:
        """``ip:port`` of the SSH client, when the server runs in an SSH session."""
        fields = (self.ssh_connection or "").split()
        return f"{fields[0]}:{fields[1]}" if len(fields) >= 2 else None


# Settings read from the environment only, never from .env: where .env and the secret
# files are, and what sshd or systemd set. The reference lists them after GROUPS.
ENVIRONMENT_ONLY: dict[str, type[AftMcpSettings]] = {
    "paths": PathSettings,
    "session": SessionSettings,
}


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


def _describe(error: ValidationError, group: type[AftMcpSettings]) -> list[str]:
    """One line per problem, naming the variable; input values are left out."""
    problems = []
    for detail in error.errors(include_input=False):
        message = detail["msg"].removeprefix("Value error, ")
        if detail["loc"]:
            field = str(detail["loc"][0])
            name = (
                group.env_name(field)
                if field in group.model_fields
                else field  # a validation alias, already the variable's name
            )
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


def load_paths() -> PathSettings:
    """Read where the settings come from.

    Raises:
        SettingsError: A named file or directory doesn't exist, so a typo isn't
            silently ignored.
    """
    try:
        return PathSettings(_env_file=None)
    except ValidationError as error:
        raise SettingsError(_describe(error, PathSettings)) from None


def load_settings(env_file: Path | None, secrets_dir: Path | None = None) -> Settings:
    """Read and validate every group of settings.

    Args:
        env_file: The ``.env`` file to read after the environment (usually
            ``load_paths().env_file_path``), or None to read the environment only,
            as the tests do so that a developer's ``.env`` never applies. A missing
            file is fine: the settings then come from the environment alone, as when
            an MCP client passes them in its own configuration.
        secrets_dir: A directory of secret files, read last: the environment and
            ``.env`` win over it.

    Raises:
        SettingsError: Naming every missing or invalid setting, in every group.
    """
    problems: list[str] = []
    groups: dict[str, AftMcpSettings] = {}
    for name, group in GROUPS.items():
        try:
            groups[name] = group(_env_file=env_file, _secrets_dir=secrets_dir)
        except ValidationError as error:
            problems += _describe(error, group)
    if problems:
        raise SettingsError(problems)
    return Settings.model_validate(groups)
