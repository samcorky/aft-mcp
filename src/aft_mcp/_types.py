"""Validated setting value types, as in menagerist's ``app.platform.config._types``."""

from typing import Annotated

from pydantic import BeforeValidator
from pydantic_settings import NoDecode


def _split_csv(v: object) -> list[str] | object:
    if isinstance(v, str):
        return [item.strip() for item in v.split(",") if item.strip()]
    return v


# A comma-separated list, such as AFT_MCP_BOARD_IDS=12,15; spaces and empty entries
# are dropped. NoDecode stops pydantic-settings reading it as JSON first.
type CSV[T] = Annotated[list[T], NoDecode, BeforeValidator(_split_csv)]
