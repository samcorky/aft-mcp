# Contributing to aft-mcp

Thanks for your interest in improving aft-mcp. Bug reports, ideas, documentation fixes
and pull requests are all welcome.

## Before you submit

Check every item before you open a pull request:

- [ ] **Tests:** new behaviour and bug fixes come with tests, and coverage stays at 100%.
- [ ] **Checks:** `uv run poe check` passes (ruff format, ruff check, mypy and pytest).
- [ ] **Hooks:** the pre-commit hooks pass. They run on every commit once installed.
- [ ] **Exposure:** if you touched `ALLOWED_WRITES` or `SIDE_EFFECT_GET_PATTERNS`, read
      [Changing what is exposed](#changing-what-is-exposed).
- [ ] **Docs:** the README is updated if behaviour or configuration changed.
- [ ] **No secrets:** no credentials, `.env` contents or private AFT URLs anywhere,
      including tests and screenshots.

## Development setup

You need Python 3.14+ and [uv](https://docs.astral.sh/uv/).

1. Fork and clone the repository.
2. Create a branch from `main` for your change; `main` is protected and only changes
   through pull requests.
3. Install the dependencies and the git hooks:

   ```bash
   uv sync
   uv run poe init
   ```

`poe init` installs three hooks with [prek](https://github.com/j178/prek), a drop-in
replacement for `pre-commit` that reads the same `.pre-commit-config.yaml`:

| Hook         | Runs                                                                                                                     |
|--------------|--------------------------------------------------------------------------------------------------------------------------|
| `pre-commit` | File hygiene, secret scanning (gitleaks), config validation, ruff and mypy. Refuses commits on `main`.                   |
| `commit-msg` | Checks the message follows [Conventional Commits](https://www.conventionalcommits.org/).                               |
| `pre-push`   | The tests (without coverage or `e2e`), and refuses a push that would update `main` on the remote.                        |

The tests never touch the network or your `.env`, so you don't need an AFT instance to
run them. There are three layers:

| File                            | Tests                                                                                       |
|---------------------------------|---------------------------------------------------------------------------------------------|
| `tests/test_swagger_convert.py` | The Swagger 2.0 to OpenAPI 3 converter                                                      |
| `tests/test_settings.py`        | Loading and validating the settings, and keeping the password out of output                 |
| `tests/test_board_guard.py`     | The board allowlist (`AFT_BOARD_IDS`): what it refuses, narrows and lets through            |
| `tests/test_audit.py`           | The audit log lines written for each tool call, and their per-call fields                   |
| `tests/test_server.py`          | Configuration, spec loading, and which endpoints become tools (with the reviewed snapshots) |
| `tests/test_mcp.py`             | A real MCP client: handshake, tool calls and the HTTP each one sends to a stubbed AFT, plus an `e2e` test that runs `python -m aft_mcp` over stdio against a local fake AFT |

To try the server against a real AFT, copy `.env.example` to `.env` and fill it in (see
the README).

## Common tasks

Run `uv run poe` to list every task.

| Task                     | Does                                                         |
|--------------------------|--------------------------------------------------------------|
| `uv run poe check`       | Everything CI checks: format, lint, types and tests          |
| `uv run poe fix`         | Format and apply the automatic lint fixes                    |
| `uv run poe test`        | The tests, with coverage (fails below 100%)                  |
| `uv run poe test-fast`   | The tests without coverage, skipping `e2e`                   |
| `uv run poe hooks`       | Every pre-commit hook on the whole repository                |
| `uv run poe build-check` | Build the wheel and sdist, and run the tests against each    |

## Code style

The ruff and mypy settings follow the conventions of
[menagerist](https://github.com/samcorky/menagerist) and
[calver-scm](https://github.com/samcorky/calver-scm):

- Ruff with an 88-character line, Google-style docstrings, type annotations everywhere
  (`ANN`) and a complexity limit of 8 (`C90`). Tests are exempt from the docstring and
  annotation rules, though mypy still checks them.
- `mypy --strict`, over the tests as well.
- No `from __future__ import annotations`: on Python 3.14 annotations are already
  evaluated lazily, so imports used only in annotations go under `if TYPE_CHECKING:`.
- `JSON` (in `aft_mcp.swagger_convert`) is the type for spec fragments.
- Every setting is a field on `Settings` in `aft_mcp.settings`; code takes a `Settings`
  rather than reading the environment itself.

## Changing what is exposed

`src/aft_mcp/server.py` decides which AFT endpoints become tools. This is the main safety boundary
of the project, so changes to it get extra care:

- Reads: every `GET` is exposed unless it matches `SIDE_EFFECT_GET_PATTERNS`. If you find
  a `GET` that changes state, add it there.
- Writes: only routes in `ALLOWED_WRITES`. A new write should change something inside a
  board and be possible to undo through the MCP server. If it can't be undone through
  the server, say so in the README, as for adding cards and columns.
- Never expose a `DELETE` route, or a board-level write (create, rename, owner, public
  link, import).
- Update the reviewed snapshots (`REVIEWED_WRITES`, `REVIEWED_SIDE_EFFECT_GETS`) and the
  exact tool list in `tests/test_server.py`, and the README's "What is exposed" section,
  in the same pull request. The tests fail until you do.

## Pull requests

- Keep each pull request to one focused change.
- Give it a [Conventional Commits](https://www.conventionalcommits.org/) title, such as
  `feat: add a tool for card labels`, `fix(converter): ...` or `docs: ...`. Add `!`
  after the type (`feat!:`) for a breaking change, such as renaming a tool.
- Say `Closes #123` in the description to link the issue.
- Two checks must pass before merging: `CI passed` (lint, types and tests) and
  `Hooks: prek (autofix)` (every other hook).
- Commits on `main` must be signed and show as **Verified** on GitHub. Set up
  [commit signing](https://docs.github.com/en/authentication/managing-commit-signature-verification)
  (an SSH key is simplest: `git config gpg.format ssh`, `git config user.signingkey
  <path to .pub>`, `git config commit.gpgsign true`, and add the key to GitHub as a
  signing key). Pull requests are merged with a squash or a merge commit, which GitHub
  signs; rebase merging is turned off.
- The [autofix.ci](https://autofix.ci) app runs the hooks on every pull request and may
  push a `style: apply hook fixes` commit, so pull before pushing again. It never edits
  files under `.github/workflows`.
- Dependency, action and hook updates come from Renovate, weekly.

## Versions and releases

Versions follow [CalVer](https://calver.org/) as `YYYY.MM.PATCH`, worked out from git
tags by [calver-scm](https://github.com/samcorky/calver-scm) through hatch-vcs; there is
no version number to edit by hand.

- A release is a signed tag on `main` named `v` plus the version, such as `v2026.11.0`.
  `PATCH` counts releases within the month and starts again at `0` each month.
- Between tags, builds get a development version such as `2026.11.1.dev3`, plus
  `+dirty` when the working tree has uncommitted changes.
- `aft_mcp.__version__` and the version the server reports to MCP clients come from the
  installed package metadata.
- Publish a release from the tag with GitHub's "Generate release notes", which groups
  pull requests by label (see below).

## Labels

Release notes are grouped by pull request label (`.github/release.yml`). The `PR labeller`
workflow adds labels from the title prefix and from the files changed
(`.github/labeller.yml`). The `Labels: at least one` check fails a pull request that has
no label for the kind of change; add one by hand and it reruns.

| Label                                            | From                                               |
|--------------------------------------------------|----------------------------------------------------|
| `breaking-change`                                | `!` after the type, or `BREAKING CHANGE` in the body |
| `enhancement`                                    | `feat:`                                            |
| `bug`                                            | `fix:`                                             |
| `performance`                                    | `perf:`                                            |
| `security`                                       | `security:` or a `(security)` scope                |
| `dependencies`                                   | Renovate, or a change only to the lock and manifest files |
| `maintenance`, `tests`, `CI/CD`, `documentation` | `chore:`, `refactor:`, `build:`, `style:`, `test:`, `ci:`, `docs:`, and matching files |
| `area: server`, `area: converter`                | Changes to the server and settings, or to the converter, in `src/aft_mcp/` (where, not what; added automatically) |
| `skip-release-notes`                             | Added by hand to leave a pull request out          |

## Issues

Open an issue from one of the forms: bug report, feature request or documentation.
Problems with AFT itself go to the [AFT repository](https://github.com/sjefferson99/aft/issues).

## Policies

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report
security issues privately as described in [SECURITY.md](SECURITY.md).
