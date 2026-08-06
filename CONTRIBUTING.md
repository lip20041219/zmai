# Contributing to ZMAI

Thank you for considering contributing to ZMAI! This document outlines the process.

## Code of Conduct

This project and everyone participating in it is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## How to Contribute

### 1. Fork the Repository

Click the "Fork" button on the top-right of the repository page on GitHub.

### 2. Clone Your Fork

```bash
git clone https://github.com/your-username/zmai.git
cd zmai
```

### 3. Create a Branch

Create a branch for your changes:

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

### 4. Set Up the Development Environment

ZMAI has zero third-party runtime dependencies. Install the project and dev tools:

```bash
pip install -e ".[dev]"
```

This installs:
- **pytest** — test runner
- **ruff** — linter and formatter
- **mypy** — static type checker

Optionally install the pre-commit hook (checks for API key leaks):

```bash
pip install pre-commit
pre-commit install
```

### 5. Make Your Changes

- Follow the existing code style (ruff will enforce this).
- Keep zero third-party dependencies — do not add new `pip install` dependencies to the runtime.
- ZMAI supports Python 3.10+.

### 6. Run Tests

```bash
pytest tests/ --ignore=tests/test_live_api.py -q
```

This runs the mock-based test suite (no API keys required). The live API tests are excluded by default — they need actual API keys.

All tests should pass before submitting a PR.

### 7. Run Lint

```bash
ruff check src/ tests/
```

### 8. Run Type Check

```bash
mypy src/zmai/
```

Note: mypy reports known type issues that are being fixed incrementally. Focus on not introducing new type errors.

### 9. Commit Your Changes

```bash
git add .
git commit -m "brief description of your change"
```

The pre-commit hook will check for accidentally committed API keys.

### 10. Push and Open a Pull Request

```bash
git push origin feature/your-feature-name
```

Then open a Pull Request on GitHub against the `main` branch.

## Pull Request Guidelines

- **Keep PRs focused** — one feature or fix per PR.
- **Write a clear description** — what the change does and why.
- **Reference issues** — if your PR addresses an issue, include `Closes #123` in the description.
- **Update tests** — add tests for new functionality.
- **No regressions** — CI must pass (lint, type check, tests).

## Style Guide

- Python 3.10+ syntax.
- Type annotations for all public APIs.
- Docstrings in English.
- Imports sorted (ruff handles this automatically with `--fix`).

## Adding a New Backend

Backends are auto-discovered via the plugin system. Create a file that defines a class inheriting from `zmai.gateway.Backend` and implementing `invoke()`, `stream()`, and `capabilities`. See existing backends in `src/zmai/gateway/backends/` for reference.

## Questions?

Open a [Discussion](https://github.com/zmai/zmai/discussions) or an [Issue](https://github.com/zmai/zmai/issues).
