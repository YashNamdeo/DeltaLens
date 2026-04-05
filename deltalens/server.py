"""MCP server — exposes all DeltaLens tools via Model Context Protocol.

All tool definitions live here. No business logic — delegates to modules.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from deltalens.allocator import allocate_budget, render_context
from deltalens.classifier import classify_file_changes
from deltalens.config import DeltaLensConfig
from deltalens.graph import GraphStore
from deltalens.models import ChangeKind, EdgeKind, ScoredNode
from deltalens.parser import parse_directory, parse_file
from deltalens.scorer import (
    ScoringContext,
    filter_by_threshold,
    score_impacted_nodes,
)

logger = logging.getLogger(__name__)

app = Server("deltalens")

_store: GraphStore | None = None
_config: DeltaLensConfig | None = None


def _get_config() -> DeltaLensConfig:
    global _config
    if _config is None:
        _config = DeltaLensConfig.load()
    return _config


def _get_store() -> GraphStore:
    global _store
    if _store is None:
        cfg = _get_config()
        assert cfg.db_path is not None
        _store = GraphStore(cfg.db_path)
    return _store


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="build_graph",
            description="Build the full code graph for a repository. Parses all supported files and creates the node/edge index.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_root": {
                        "type": "string",
                        "description": "Path to repository root",
                    },
                    "ignore_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Glob patterns to ignore",
                    },
                },
                "required": ["repo_root"],
            },
        ),
        Tool(
            name="get_delta_context",
            description="Get scored, tiered context for a set of changed files. Returns the minimum viable context within the token budget.",
            inputSchema={
                "type": "object",
                "properties": {
                    "changed_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of changed file paths",
                    },
                    "token_budget": {
                        "type": "integer",
                        "description": "Maximum token budget (default 8000)",
                        "default": 8000,
                    },
                },
                "required": ["changed_files"],
            },
        ),
        Tool(
            name="classify_change",
            description="Classify changes in a file as interface (signature/API) or implementation (body-only). Key for understanding blast radius.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the changed file",
                    },
                    "old_content": {
                        "type": "string",
                        "description": "Previous file content",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New file content",
                    },
                },
                "required": ["file_path", "old_content", "new_content"],
            },
        ),
        Tool(
            name="get_impact_score",
            description="Get impact scores for specific nodes given their change classifications.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Node IDs to score",
                    },
                    "change_kinds": {
                        "type": "object",
                        "description": "Map of node_id to change kind (interface/impl/added/deleted)",
                    },
                },
                "required": ["node_ids", "change_kinds"],
            },
        ),
        Tool(
            name="search_nodes",
            description="Search for code nodes by name, signature, or docstring.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {"type": "integer", "default": 10},
                    "use_embeddings": {"type": "boolean", "default": False},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_signatures",
            description="Get compressed signatures + docstrings for specific nodes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Node IDs to get signatures for",
                    },
                },
                "required": ["node_ids"],
            },
        ),
        Tool(
            name="get_entry_points",
            description="Detect entry points in the codebase (functions with in-degree 0 or annotated as routes/main).",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_root": {
                        "type": "string",
                        "description": "Path to repository root",
                    },
                },
                "required": ["repo_root"],
            },
        ),
        Tool(
            name="get_graph_stats",
            description="Get statistics about the code graph (node/edge counts, last build time).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_change_history",
            description="Get past change history for a node, including risk flags.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Node ID to get history for",
                    },
                },
                "required": ["node_id"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = _handle_tool(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        logger.exception("Tool %s failed", name)
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": str(e), "tool": name}),
            )
        ]


def _handle_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "build_graph":
        return _tool_build_graph(args)
    elif name == "get_delta_context":
        return _tool_get_delta_context(args)
    elif name == "classify_change":
        return _tool_classify_change(args)
    elif name == "get_impact_score":
        return _tool_get_impact_score(args)
    elif name == "search_nodes":
        return _tool_search_nodes(args)
    elif name == "get_signatures":
        return _tool_get_signatures(args)
    elif name == "get_entry_points":
        return _tool_get_entry_points(args)
    elif name == "get_graph_stats":
        return _tool_get_graph_stats()
    elif name == "get_change_history":
        return _tool_get_change_history(args)
    else:
        return {"error": f"Unknown tool: {name}"}


def _tool_build_graph(args: dict[str, Any]) -> dict[str, Any]:
    repo_root = Path(args["repo_root"])
    ignore = args.get("ignore_patterns", _get_config().ignore_patterns)

    results = parse_directory(repo_root, ignore)
    store = _get_store()

    for result in results:
        store.ingest_parse_result(result)

    resolved = store.resolve_unresolved_edges()

    stats = store.stats()
    stats["edges_resolved"] = resolved
    return {"status": "ok", **stats}


def _tool_get_delta_context(args: dict[str, Any]) -> dict[str, Any]:
    changed_files: list[str] = args["changed_files"]
    budget = args.get("token_budget", _get_config().token_budget)

    store = _get_store()
    cfg = _get_config()

    # Step 1: Get current and classify changes for each file
    all_changed_nodes = []
    change_kinds: dict[str, str] = {}

    for fpath in changed_files:
        old_nodes = store.get_nodes_for_file(fpath)
        new_result = parse_file(fpath)
        if not new_result:
            continue
        new_nodes = new_result.nodes

        classified = classify_file_changes(old_nodes, new_nodes, fpath)

        for nc in classified.node_changes:
            change_kinds[nc.node_id] = nc.kind.value

        # Adaptive mode: skip graph for trivial single-file changes
        if len(changed_files) == 1 and classified.is_trivial:
            # Just return the raw diff context
            nodes_out = [
                ScoredNode(
                    node=n,
                    score=1.0,
                    reasoning="trivial change — direct context only",
                )
                for n in new_nodes
                if n.kind.value != "file"
            ]
            payload = allocate_budget(nodes_out, budget)
            return {
                "mode": "adaptive_trivial",
                "context": render_context(payload),
                "tokens": payload.total_tokens,
            }

        all_changed_nodes.extend([n for n in new_nodes if n.id in change_kinds])

        # Ingest updated file
        store.ingest_parse_result(new_result)

    # Step 2: BFS for impact
    seed_ids = [n.id for n in all_changed_nodes]

    # For impl-only changes, only get direct callers
    all_impl = all(k == ChangeKind.IMPL.value for k in change_kinds.values())
    max_depth = 1 if all_impl else 4

    bfs_results = store.bfs_impacted(seed_ids, max_depth=max_depth)

    # Build lookups
    edge_lookup: dict[str, str] = {}
    for node_id, _depth in bfs_results:
        if node_id not in [n.id for n in all_changed_nodes]:
            edge_lookup[node_id] = EdgeKind.CALLS.value  # default

    node_lookup = {n.id: n for n in all_changed_nodes}
    for node_id, _ in bfs_results:
        if node_id not in node_lookup:
            node = store.get_node(node_id)
            if node:
                node_lookup[node_id] = node

    # Step 3: Score
    ctx = ScoringContext(
        distance_decay=cfg.distance_decay,
        impact_threshold=cfg.impact_threshold,
    )
    scored = score_impacted_nodes(
        all_changed_nodes,
        change_kinds,
        bfs_results,
        edge_lookup,
        node_lookup,
        ctx,
    )
    scored = filter_by_threshold(scored, cfg.impact_threshold)

    # Step 4: Allocate budget
    payload = allocate_budget(scored, budget)

    return {
        "mode": "full_analysis",
        "context": render_context(payload),
        "tokens": payload.total_tokens,
        "budget": budget,
        "node_count": len(payload.nodes),
        "summary": payload.summary,
    }


def _tool_classify_change(args: dict[str, Any]) -> dict[str, Any]:
    file_path = args["file_path"]
    old_content = args["old_content"].encode()
    new_content = args["new_content"].encode()

    old_result = parse_file(file_path, old_content)
    new_result = parse_file(file_path, new_content)

    if not old_result or not new_result:
        return {"error": "Failed to parse file"}

    classified = classify_file_changes(
        old_result.nodes, new_result.nodes, file_path
    )

    return {
        "file_path": file_path,
        "is_trivial": classified.is_trivial,
        "changes": [
            {
                "node_id": nc.node_id,
                "kind": nc.kind.value,
                "old_signature": nc.old_signature,
                "new_signature": nc.new_signature,
            }
            for nc in classified.node_changes
        ],
    }


def _tool_get_impact_score(args: dict[str, Any]) -> dict[str, Any]:
    node_ids: list[str] = args["node_ids"]
    change_kinds: dict[str, str] = args["change_kinds"]

    store = _get_store()
    cfg = _get_config()

    bfs_results = store.bfs_impacted(node_ids, max_depth=4)
    edge_lookup: dict[str, str] = {
        nid: EdgeKind.CALLS.value for nid, _ in bfs_results
    }
    node_lookup: dict[str, Any] = {}
    changed_nodes = []

    for nid in node_ids:
        node = store.get_node(nid)
        if node:
            changed_nodes.append(node)
            node_lookup[nid] = node

    for nid, _ in bfs_results:
        if nid not in node_lookup:
            node = store.get_node(nid)
            if node:
                node_lookup[nid] = node

    ctx = ScoringContext(
        distance_decay=cfg.distance_decay,
        impact_threshold=cfg.impact_threshold,
    )
    scored = score_impacted_nodes(
        changed_nodes, change_kinds, bfs_results, edge_lookup, node_lookup, ctx
    )

    return {
        "scores": [
            {
                "node_id": s.node.id,
                "name": s.node.name,
                "score": s.score,
                "reasoning": s.reasoning,
            }
            for s in scored
        ],
    }


def _tool_search_nodes(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"]
    top_k = args.get("top_k", 10)

    store = _get_store()
    results = store.search_fts(query, limit=top_k)

    return {
        "results": [
            {
                "id": n.id,
                "name": n.name,
                "kind": n.kind.value,
                "file_path": n.file_path,
                "signature": n.signature,
                "line_start": n.line_start,
            }
            for n in results
        ],
    }


def _tool_get_signatures(args: dict[str, Any]) -> dict[str, Any]:
    node_ids: list[str] = args["node_ids"]
    store = _get_store()

    sigs = []
    for nid in node_ids:
        node = store.get_node(nid)
        if node:
            sigs.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "signature": node.signature,
                    "docstring": node.docstring,
                    "file_path": node.file_path,
                    "line_start": node.line_start,
                }
            )

    return {"signatures": sigs}


def _tool_get_entry_points(args: dict[str, Any]) -> dict[str, Any]:
    from deltalens.flows import detect_entry_points

    _ = args["repo_root"]
    store = _get_store()
    entries = detect_entry_points(store)

    return {
        "entry_points": [
            {
                "id": n.id,
                "name": n.name,
                "file_path": n.file_path,
                "line_start": n.line_start,
                "is_entry_point": True,
            }
            for n in entries
        ],
    }


def _tool_get_graph_stats() -> dict[str, Any]:
    store = _get_store()
    return store.stats()


def _tool_get_change_history(args: dict[str, Any]) -> dict[str, Any]:
    node_id = args["node_id"]
    store = _get_store()

    rows = store.conn.execute(
        "SELECT * FROM change_history WHERE node_id = ? ORDER BY changed_at DESC",
        (node_id,),
    ).fetchall()

    return {
        "history": [
            {
                "commit_sha": row["commit_sha"],
                "changed_at": row["changed_at"],
                "change_kind": row["change_kind"],
                "risk_flag": bool(row["risk_flag"]),
                "reviewer": row["reviewer"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


async def run_server() -> None:
    """Start the MCP server on stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream, write_stream, app.create_initialization_options()
        )
