# Security policy

## Supported versions

Security fixes are made on a best-effort basis for the `main` branch. There are no
older releases to backport to.

## Reporting a vulnerability

Please do not report security issues in public issues or pull requests.

Report them privately through GitHub Security Advisories:

https://github.com/samcorky/aft-mcp/security/advisories/new

If you cannot use that, open a minimal public issue asking for a private contact and
leave out the details.

Vulnerabilities in AFT itself belong to the [AFT project](https://github.com/sjefferson99/aft).

## What counts

This server holds an AFT user's credentials and lets an AI agent act as that user, so
these are in scope:

- A tool that can perform a write outside the allowlist in `server.py` (`ALLOWED_WRITES`),
  including any `DELETE` route or board-level change, or a `GET` with a side effect that
  is not excluded.
- Credentials from `.env` leaking into tool output, logs, error messages or the spec.
- The spec loader or the converter being made to do something unsafe by a malicious or
  malformed API spec.

Out of scope: what the AFT user is allowed to do in AFT itself. The README recommends a
dedicated, board-scoped user for exactly this reason.

## What to include

- A description of the issue and its likely impact
- The commit or version affected, and your environment
- Steps to reproduce, or a minimal proof of concept
- Any fix or mitigation you suggest

## What happens next

We aim to acknowledge a report promptly, may ask for more detail to reproduce it, and
will agree the timing of any disclosure with you. When a fix is available we may
publish an advisory describing the affected versions and the fix.
