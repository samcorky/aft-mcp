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
| `pre-push`   | The tests (without coverage), and refuses a push that would update `main` on the remote.                                 |

The tests never touch the network or your `.env`, so you don't need an AFT instance to
run them. To try the server against a real AFT, copy `.env.example` to `.env` and fill
it in (see the README).

## Common tasks

Run `uv run poe` to list every task.

| Task                     | Does                                                         |
|--------------------------|--------------------------------------------------------------|
| `uv run poe check`       | Everything CI checks: format, lint, types and tests          |
| `uv run poe fix`         | Format and apply the automatic lint fixes                    |
| `uv run poe test`        | The tests, with coverage (fails below 100%)                  |
| `uv run poe test-fast`   | The tests without coverage                                   |
| `uv run poe hooks`       | Every pre-commit hook on the whole repository                |

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
- `JSON` (in `swagger_convert.py`) is the type for spec fragments.

## Changing what is exposed

`server.py` decides which AFT endpoints become tools. This is the main safety boundary
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
- The [autofix.ci](https://autofix.ci) app runs the hooks on every pull request and may
  push a `style: apply hook fixes` commit, so pull before pushing again. It never edits
  files under `.github/workflows`.
- Dependency, action and hook updates come from Renovate, weekly.

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
| `area: server`, `area: converter`                | Changes to `server.py` or `swagger_convert.py` (where, not what; added automatically) |
| `skip-release-notes`                             | Added by hand to leave a pull request out          |

## Issues

Open an issue from one of the forms: bug report, feature request or documentation.
Problems with AFT itself go to the [AFT repository](https://github.com/sjefferson99/aft/issues).

## Policies

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report
security issues privately as described in [SECURITY.md](SECURITY.md).
