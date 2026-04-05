# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-04-05

### Added
- Tree-sitter based multi-language parser (Python, JavaScript, TypeScript)
- Change classifier: interface vs. implementation change detection
- SQLite-backed code graph with NetworkX BFS traversal
- FTS5 full-text search with symbol normalization
- Impact scorer with edge-type weights and distance decay
- Token budget allocator with three representation tiers
- Hybrid search (BM25 + optional dense vector reranking)
- Language-agnostic entry-point detection
- MCP server with 9 tools for AI assistant integration
- CLI with init, build, update, watch, search, classify, context, serve, install
- File watcher for real-time incremental updates
- Auto-install for Claude Code, Claude Desktop, and Cursor
- Evaluation runner with token reduction and precision/recall metrics
- 96 tests covering parser, classifier, scorer, allocator, graph, and search
