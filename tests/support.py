"""Helpers the tests import; fixtures live in conftest.py."""

from typing import Any

from aft_mcp.settings import GROUPS, Settings

# Each flat name, as tests write it, to its group and field: "log_format" is the
# log group's "format", like the variable AFT_MCP_LOG_FORMAT.
FIELD_GROUPS = {
    (f"log_{field}" if group == "log" else field): (group, field)
    for group, settings_class in GROUPS.items()
    for field in settings_class.model_fields
}


def changed(settings: Settings, update: dict[str, Any]) -> Settings:
    """A copy of ``settings`` with fields replaced, named flat as in FIELD_GROUPS."""
    per_group: dict[str, dict[str, Any]] = {}
    for name, value in update.items():
        group, field = FIELD_GROUPS[name]
        per_group.setdefault(group, {})[field] = value
    return settings.model_copy(
        update={
            group: getattr(settings, group).model_copy(update=fields)
            for group, fields in per_group.items()
        }
    )
