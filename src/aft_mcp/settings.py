"""Settings for the AFT MCP server, loaded and validated once at startup.

Values come from the process environment first, then from a ``.env`` file: the one
named by ``AFT_ENV_FILE``, or ``.env`` in the working directory. There are no defaults
in the code for required settings: a missing or invalid setting stops the server, and
the error names every bad setting at once.
"""

import os
from pathlib import Path
from typing import Annotated, Literal, Self

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

ENV_PREFIX = "AFT_"
# Names the .env file to read; it is not a field, so set it in the environment.
ENV_FILE_VARIABLE = "AFT_ENV_FILE"


class SettingsError(RuntimeError):
    """One or more settings are missing or invalid."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"Invalid AFT settings (set them in .env):\n{lines}")


class Settings(BaseSettings):
    """Every setting the server reads; the README's configuration table follows it."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # AFT_SPEC_FILE= in .env means unset, not "."
        extra="ignore",  # .env may hold settings for other tools, and AFT_ENV_FILE
        frozen=True,
    )

    base_url: HttpUrl
    spec_url: HttpUrl | None = None
    spec_file: Path | None = None
    timeout: float = Field(gt=0, allow_inf_nan=False)
    username: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)
    # Optional switches: unset means off, the behaviour before they existed.
    read_only: bool = False
    # How the audit log on stderr is written: logfmt lines, or one JSON object each.
    log_format: Literal["logfmt", "json"] = "logfmt"
    # Comma-separated, e.g. AFT_BOARD_IDS=12,15; unset allows every board.
    board_ids: Annotated[frozenset[PositiveInt] | None, NoDecode] = None

    @field_validator("board_ids", mode="before")
    @classmethod
    def _split_board_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _needs_a_spec(self) -> Self:
        if self.spec_url is None and self.spec_file is None:
            raise ValueError(f"set {ENV_PREFIX}SPEC_URL or {ENV_PREFIX}SPEC_FILE")
        return self


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


def env_file_path() -> Path:
    """The ``.env`` file to read: ``AFT_ENV_FILE`` if set, else ``./.env``.

    A missing file is fine: the settings then come from the environment alone, as
    when an MCP client passes them in its own configuration.
    """
    return Path(os.environ.get(ENV_FILE_VARIABLE) or ".env")


def load_settings(env_file: Path | None) -> Settings:
    """Read and validate every setting.

    Args:
        env_file: The ``.env`` file to read after the environment (usually
            ``env_file_path()``), or None to read the environment only, as the tests
            do so that a developer's ``.env`` never applies.

    Raises:
        SettingsError: Naming every missing or invalid setting.
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        raise SettingsError(_describe(error)) from None
