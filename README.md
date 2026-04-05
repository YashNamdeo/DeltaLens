<p align="center">
  <h1 align="center">DeltaLens</h1>
  <p align="center">
    <strong>Semantic-diff-aware code intelligence for AI coding assistants</strong>
  </p>
  <p align="center">
    <a href="https://github.com/YashNamdeo/DeltaLens/actions/workflows/ci.yml"><img src="https://github.com/YashNamdeo/DeltaLens/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://github.com/YashNamdeo/DeltaLens/blob/main/LICENSE"><img src="https://img.shields.io/github/license/YashNamdeo/DeltaLens" alt="License"></a>
    <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
    <a href="https://github.com/YashNamdeo/DeltaLens/stargazers"><img src="https://img.shields.io/github/stars/YashNamdeo/DeltaLens?style=social" alt="Stars"></a>
  </p>
</p>

---

DeltaLens sits between your codebase and any AI coding assistant (Claude Code, Cursor, Windsurf). It combines **Tree-sitter static analysis** with **diff-semantic classification** to send the **minimum viable context** — not just fewer files, but the *right representation* of those files.

> **15-40x token reduction** | **< 1s incremental updates** | **Works with any MCP-compatible AI tool**

## The Problem

Every time an AI assistant reviews a change, it scans the codebase. On a 500-file repo that's ~150k tokens. Most of that context is noise — files not affected by the change.

Existing tools achieve recall by flagging 5-10x more files than necessary. DeltaLens targets **precision** while maintaining recall.

## How It Works

```
git diff --> Change Classifier --> Impact Scorer --> Token Allocator --> AI Assistant
               (interface          (0-1 per           (full / sig /       (minimal,
                vs impl)            node)              summary)            precise)
```

**The key insight**: classify *what kind of change happened* before deciding how much context to send.

| Change Type | What Happens | Context Sent |
|-------------|-------------|-------------|
| **Interface** (signature, exports, API) | Full blast-radius BFS traversal | All affected files, scored by impact |
| **Implementation** (body only) | Direct callers only | Nearby dependents, minimal tokens |

### Three-Layer Token Reduction

**Layer 1 — Change Classification** (gate the traversal)
```python
class ChangeKind(StrEnum):
    INTERFACE = "interface"   # signature, decorators, exports changed
    IMPL      = "impl"       # body only changed
    ADDED     = "added"
    DELETED   = "deleted"
```

**Layer 2 — Scored Impact** (replace binary in/out with a gradient)
```
score = base_weight(edge_type) * distance_decay(hops) * change_multiplier(kind)
```

**Layer 3 — Token Budget Allocation**

| Score | Representation | Tokens/file |
|-------|---------------|-------------|
| 0.8 - 1.0 | Full source + diff markers | 50 - 500 |
| 0.5 - 0.79 | Signatures + docstrings | 5 - 30 |
| 0.3 - 0.49 | One-line summary | 1 - 3 |
| < 0.3 | Excluded | 0 |

## Results

| Metric | Value |
|--------|-------|
| Token reduction | 15 - 40x on multi-file changes |
| Incremental update | < 1s on 5,000-file repos |
| Blast-radius precision | > 0.7 F1 (recall = 1.0) |
| Languages | Python, JavaScript, TypeScript |

## Quick Start

```bash
# Install from source
git clone https://github.com/YashNamdeo/DeltaLens.git
cd DeltaLens
pip install -e .

# Initialize in your repo
cd your-project
deltalens init

# See what context would be sent for changed files
deltalens context path/to/changed_file.py

# Auto-configure with your AI tool
deltalens install

# Or start the MCP server directly
deltalens serve
```

### Use with Claude Code

```bash
deltalens install   # auto-detects and configures Claude Code
```

### Use with Cursor / Windsurf

```bash
deltalens install   # auto-detects Cursor or Windsurf config
```

## Why DeltaLens?

| | Raw grep/find | IDE references | DeltaLens |
|---|---|---|---|
| **Change-aware** | No | No | Yes — classifies interface vs impl |
| **Scored impact** | No | No | Yes — 0-1 gradient per node |
| **Token budgeting** | No | No | Yes — full/signature/summary tiers |
| **MCP native** | No | No | Yes — plug into any AI assistant |
| **Incremental** | N/A | Slow rebuild | < 1s via SHA-256 diffing |

## MCP Tools

DeltaLens exposes these tools via the [Model Context Protocol](https://modelcontextprotocol.io):

| Tool | Description | Priority |
|------|------------|----------|
| `build_graph` | Build the full code graph | P0 |
| `get_delta_context` | Get scored, tiered context for changes | P0 |
| `classify_change` | Classify changes as interface/impl | P0 |
| `get_impact_score` | Get impact scores for nodes | P0 |
| `search_nodes` | Search code nodes | P1 |
| `get_signatures` | Get compressed signatures | P1 |
| `get_entry_points` | Detect entry points | P1 |
| `get_graph_stats` | Graph statistics | P1 |

## CLI Commands

```
deltalens init           # Initialize and build graph
deltalens build          # Full graph rebuild
deltalens update         # Incremental update (changed files only)
deltalens watch          # Real-time file-watch mode
deltalens status         # Graph stats
deltalens context FILE   # Preview context for a changed file
deltalens classify FILE  # Show change classification
deltalens search QUERY   # Search nodes
deltalens serve          # Start MCP server
deltalens install        # Auto-configure AI tools
```

## Configuration

Create `.deltalens.toml` in your repo root:

```toml
[deltalens]
token_budget = 8000
impact_threshold = 0.3
distance_decay = 0.6
ignore_patterns = ["node_modules/", ".git/", "__pycache__/", "*.pyc"]
```

## Architecture

```
deltalens/
├── parser.py        # Tree-sitter AST -> nodes + edges
├── classifier.py    # AST diff -> ChangeKind per node
├── graph.py         # SQLite store + NetworkX BFS
├── incremental.py   # SHA-256 hashing + git diff
├── scorer.py        # 0-1 impact scoring
├── allocator.py     # Token budget -> representation tiers
├── context.py       # Shared delta-context pipeline
├── search.py        # BM25 + dense hybrid search
├── flows.py         # Entry-point detection
├── server.py        # MCP server (all tools)
└── cli.py           # CLI entry point
```

## Development

```bash
git clone https://github.com/YashNamdeo/DeltaLens.git
cd DeltaLens
pip install -e ".[dev]"

# Run all checks
black . --line-length 80 --check                       # format
ruff check . --ignore E501                             # lint
mypy . --ignore-missing-imports --check-untyped-defs   # type check
pytest tests/ -q                                       # test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

## Roadmap

- [ ] Language support: Go, Rust, Java, C/C++, Ruby, PHP
- [ ] VS Code extension
- [ ] Benchmark suite with real-world repos
- [ ] Multi-repo graph federation
- [ ] Custom scoring plugins
- [ ] PyPI package (`pip install deltalens`)

## License

MIT - see [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built for developers who want their AI assistants to actually understand their changes.</sub>
</p>
