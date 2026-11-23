"""Settings for the AFT MCP server, loaded and validated once at startup.

Values come from the process environment first, then from ``.env`` in the working
directory. There are no defaults in the code for required settings: a missing or
invalid setting stops the server, and the error names every bad setting at once.
"""

from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urljoin

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
# Read after the environment, relative to the working directory; it may be missing.
ENV_FILE = Path(".env")
# Where AFT serves its own spec, relative to the base URL.
DEFAULT_SPEC_PATH = "api/apispec.json"

_TOKEN, _USER, _PASSWORD = (
    f"{ENV_PREFIX}{n}" for n in ("TOKEN", "USERNAME", "PASSWORD")
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
        description="Base URL the generated tools call.",
        examples=["https://aft.example.com"],
    )
    spec_url: HttpUrl | None = Field(
        default=None,
        description=(
            "URL of AFT's Swagger 2.0 or OpenAPI spec, which the tools are "
            + f"built from. Unset, it is `{DEFAULT_SPEC_PATH}` under "
            + f"`{ENV_PREFIX}BASE_URL`."
        ),
        examples=["https://aft.example.com/api/apispec.json"],
    )
    spec_file: Path | None = Field(
        default=None,
        description="Load the spec from this local file instead of `AFT_MCP_SPEC_URL`.",
    )
    timeout: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Request timeout in seconds.",
        examples=[30],
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
    def _needs_one_way_to_sign_in(self) -> Self:
        basic = (self.username, self.password)
        if self.token is not None:
            if basic != (None, None):
                raise ValueError(TWO_SIGN_INS)
        elif None in basic:
            raise ValueError(NO_SIGN_IN)
        return self

    @property
    def spec_location(self) -> str:
        """The spec URL to fetch: ``spec_url``, or AFT's own under ``base_url``."""
        if self.spec_url is not None:
            return str(self.spec_url)
        return urljoin(str(self.base_url), DEFAULT_SPEC_PATH)


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


def load_settings(env_file: Path | None) -> Settings:
    """Read and validate every setting.

    Args:
        env_file: The ``.env`` file to read after the environment (``ENV_FILE``),
            or None to read the environment only, as the tests do so that a
            developer's ``.env`` never applies. A missing file is fine: the settings
            then come from the environment alone, as when an MCP client passes them
            in its own configuration.

    Raises:
        SettingsError: Naming every missing or invalid setting.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        raise SettingsError(_describe(error)) from None
