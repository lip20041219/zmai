# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ Yes |

## Reporting a Vulnerability

ZMAI handles API keys and credentials. If you find a security vulnerability:

**Do not open a public GitHub Issue.** Instead, report it privately.

### How to Report

1. **Email**: Send details to the project maintainers.
2. **GitHub**: Use the "Report a vulnerability" feature on the repository's Security tab.

Include:

- A description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact
- Suggested fix (optional)

You should receive a response within 48 hours. If the vulnerability is confirmed, we will work on a fix and coordinate disclosure.

### What to Expect

- Confirmation of receipt within 2 business days.
- Status updates every 5 business days.
- Credit in the release notes (if desired).

## Scope

### In scope

- Credential storage (API key leakage, encryption issues)
- Workspace path traversal
- Remote code execution via agent tools
- Authentication bypass

### Out of scope

- General bugs (report via Issues, not security channels)
- Dependency vulnerabilities (ZMAI has zero runtime dependencies)
- Theoretical attacks requiring physical access

## API Key Safety

**Do not commit API keys to the repository.** The project includes:

- A pre-commit hook that checks for `sk-` patterns in source code
- A CI job that scans for accidentally committed keys
- A `.gitignore` that excludes sensitive files

If you accidentally commit an API key, rotate it immediately and contact the maintainers.
