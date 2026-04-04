# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| Latest release | ✅ |
| Older releases | ❌ |

Security fixes are applied to the latest release only.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report privately via GitHub's [Security Advisories](https://github.com/thebnbrkr/agora-code/security/advisories/new) or email the maintainer directly (see GitHub profile).

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix if you have one

You will receive a response within 72 hours. If the report is confirmed, a patch will be released as soon as possible with a coordinated disclosure.

## Scope

Things in scope:
- Local data exposure (SQLite DB contents, session data, API keys)
- Hook scripts executing arbitrary commands
- MCP server attack surface

Things out of scope:
- Issues in dependencies (report to the relevant project)
- Social engineering

## Notes on data storage

agora-code stores session transcripts, file diffs, and learnings in a local SQLite DB at `~/.agora-code/agora.db`. This file may contain sensitive code and conversation history. Protect it accordingly.
