# Contributing to DeltaLens

Thanks for your interest in contributing to DeltaLens! This guide will help you get started.

## Getting Started

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Git

### Setup

```bash
# Clone the repo
git clone https://github.com/YashNamdeo/deltalens.git
cd deltalens

# Install with dev dependencies
uv sync --extra dev

# Or with pip
pip install -e ".[dev]"
```

## Development Workflow

### Before Submitting a PR

Run all checks locally — these are the same checks CI runs on every pull request:

```bash
# 1. Lint
uv run ruff check deltalens/

# 2. Type check
uv run mypy deltalens/ --strict

# 3. Run tests
uv run pytest tests/ -q

# With coverage (optional but encouraged)
uv run pytest tests/ --tb=short -q --cov=deltalens --cov-report=term-missing
```

All three must pass before your PR can be merged. CI runs these across Python 3.10–3.13.

### Quick Check (one command)

```bash
uv run ruff check deltalens/ && uv run mypy deltalens/ --strict && uv run pytest tests/ -q
```

## Code Style

- **Linter**: [Ruff](https://docs.astral.sh/ruff/) — config in `pyproject.toml`
- **Type checking**: mypy with `--strict` — all public functions must be type-annotated
- **No circular imports** — strict layering between modules
- **Pure functions** in `classifier.py` and `scorer.py` — no I/O side effects
- **MCP tools** live in `server.py` only

## Project Structure

```
deltalens/
├── parser.py        # Tree-sitter AST -> nodes + edges
├── classifier.py    # AST diff -> ChangeKind (pure)
├── graph.py         # SQLite store + BFS traversal
├── incremental.py   # SHA-256 hashing + git diff
├── scorer.py        # 0-1 impact scoring (pure)
├── allocator.py     # Token budget -> representation tiers
├── search.py        # BM25 + dense hybrid search
├── flows.py         # Entry-point detection
├── server.py        # MCP server (all tools)
└── cli.py           # CLI entry point
```

Each module has a corresponding test file in `tests/`.

## What to Contribute

### Good First Issues

Look for issues labeled [`good first issue`](https://github.com/YashNamdeo/deltalens/labels/good%20first%20issue).

### Areas We'd Love Help With

- **Language support** — Adding Tree-sitter grammars for new languages (Go, Rust, Java, C/C++, etc.)
- **Search improvements** — Better ranking, query expansion
- **Benchmarks** — Real-world token reduction measurements
- **Documentation** — Tutorials, examples, integrations
- **IDE integrations** — VS Code extension, JetBrains plugin

## Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Make your changes
4. Run all checks (lint, type check, tests)
5. Commit with a clear message
6. Push to your fork and open a Pull Request

### Commit Messages

Use clear, descriptive commit messages:

```
feat: add Go language support to parser
fix: handle empty files in incremental update
docs: add MCP integration example
test: add edge cases for scorer decay function
```

### PR Guidelines

- Keep PRs focused — one feature or fix per PR
- Add tests for new functionality
- Update type annotations for any new public functions
- Reference any related issues in the PR description

## Adding Language Support

To add a new language:

1. Add the Tree-sitter grammar dependency to `pyproject.toml`
2. Register the language in `parser.py` (`LANGUAGES` dict and grammar loader)
3. Add node-type mappings in `parser.py` (`NODE_TYPE_MAP`)
4. Create a test fixture in `tests/fixtures/`
5. Add test cases in `tests/test_multilang.py`

## Questions?

Open an issue or start a discussion — we're happy to help!
