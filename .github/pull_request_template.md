<!--
Give the pull request a Conventional Commits title, for example `feat: add a tool
for card labels` or `fix(converter): keep nullable on refs`. The labeller uses it to
group the release notes (see "Labels" in CONTRIBUTING.md).
-->

## Description

<!-- What this changes and why. -->

Closes #

## Type of change

- [ ] Bug fix (non-breaking change that resolves an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (changes behaviour people rely on, such as a tool's name)
- [ ] Documentation
- [ ] Refactor or internal change (no behaviour change)
- [ ] Dependency or CI update

## Checklist

- [ ] The hooks pass: `uv run poe hooks`.
- [ ] `uv run poe check` passes (ruff, mypy and pytest, with coverage at 100%).
- [ ] I have added or updated tests for the changed behaviour.
- [ ] I have updated the README where behaviour or configuration changed.
- [ ] No credentials, `.env` contents or private URLs are included.

### Changes to what is exposed

<!-- Delete this section if the pull request does not touch ALLOWED_WRITES or SIDE_EFFECT_GET_PATTERNS. -->

- [ ] Every new write can be undone through the MCP server, or the README says it cannot.
- [ ] No `DELETE` route and no board-level write (create, rename, owner, public link, import) is exposed.
- [ ] The README's "What is exposed" section and the reviewed snapshots in `tests/test_server.py` are updated.

## Testing notes

<!-- How a reviewer can check it, for example the tools to call against a test AFT board. -->
