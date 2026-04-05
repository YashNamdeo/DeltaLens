# DeltaLens

## Project overview
Semantic-diff-aware code intelligence layer that sits between codebases and AI coding assistants. Combines Tree-sitter AST parsing with diff-semantic classification to send minimum viable context to models.

## Architecture
- `deltalens/parser.py` — Tree-sitter multi-language AST → nodes and edges
- `deltalens/classifier.py` — AST diff → ChangeKind per node (interface vs impl)
- `deltalens/graph.py` — SQLite-backed node/edge storage, BFS traversal, FTS5 search
- `deltalens/incremental.py` — SHA-256 hashing + git diff integration
- `deltalens/scorer.py` — 0–1 impact scoring with edge-type weights
- `deltalens/allocator.py` — Token budget → representation tier selection
- `deltalens/search.py` — BM25 + optional dense hybrid search
- `deltalens/flows.py` — Entry-point detection via in-degree + annotations
- `deltalens/server.py` — MCP server exposing all tools
- `deltalens/cli.py` — CLI entry point (delegates to modules)

## Commands
- `uv sync --extra dev` — install dependencies
- `uv run pytest tests/ -q` — run tests
- `uv run ruff check deltalens/` — lint
- `uv run mypy deltalens/ --strict` — type check

## Conventions
- No circular imports between modules — strict layering
- classifier.py and scorer.py are pure functions — no I/O
- MCP tool definitions live in server.py only
- All public functions type-annotated
- Each module has a corresponding test file
