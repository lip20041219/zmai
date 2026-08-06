# Changelog

All notable changes to ZMAI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Model-agnostic agent runtime (DeepSeek / Claude / Gemini backends)
- SWE Agent with read, write, edit, grep, shell, git tools
- Interactive REPL with command history
- Multi-source configuration (file, env, CLI arguments)
- Credential management with environment variable, file, and OS-native store support
- Workspace manager with path traversal protection
- Working memory (in-memory) and long-term memory (JSONL file) system
- Zero third-party dependency design (Python stdlib only)
- CLI subcommands: `config`, `auth`, `doctor`, `plugin`
- Plugin system for custom backends
- `zmai doctor` diagnostic tool
- Pre-commit hook for API key leak detection
- CI with GitHub Actions (lint, type check, test on Ubuntu + Windows, Python 3.10–3.12)
- 600+ tests across all subsystems
