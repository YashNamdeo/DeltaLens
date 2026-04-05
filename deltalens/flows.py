"""Entry-point detection via graph structure and annotation parsing.

Language-agnostic: detects entry points by in-degree (functions nobody calls)
plus annotation patterns as secondary signals.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from deltalens.models import CodeNode, EdgeKind, NodeKind

if TYPE_CHECKING:
    from deltalens.graph import GraphStore

# ---------------------------------------------------------------------------
# Annotation patterns for entry-point detection (secondary signal)
# ---------------------------------------------------------------------------

ENTRY_ANNOTATIONS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"@app\.(route|get|post|put|delete|patch)"),
        re.compile(r"@router\.(route|get|post|put|delete|patch)"),
        re.compile(r"@(api_view|action|task|celery)"),
        re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']'),
        re.compile(r"@click\.(command|group)"),
        re.compile(r"def\s+main\s*\("),
    ],
    "javascript": [
        re.compile(r"app\.(get|post|put|delete|patch|use)\s*\("),
        re.compile(r"router\.(get|post|put|delete|patch|use)\s*\("),
        re.compile(r"export\s+default"),
        re.compile(r"module\.exports"),
    ],
    "typescript": [
        re.compile(r"@(Get|Post|Put|Delete|Patch|Controller)\s*\("),
        re.compile(r"app\.(get|post|put|delete|patch|use)\s*\("),
        re.compile(r"export\s+default"),
    ],
    "go": [
        re.compile(r"func\s+main\s*\("),
        re.compile(r"func\s+init\s*\("),
        re.compile(r"http\.(Handle|HandleFunc)\s*\("),
    ],
    "rust": [
        re.compile(r"fn\s+main\s*\("),
        re.compile(r"#\[(get|post|put|delete|patch)\s*\("),
        re.compile(r"#\[tokio::main\]"),
    ],
    "java": [
        re.compile(r"public\s+static\s+void\s+main\s*\("),
        re.compile(
            r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|RequestMapping)"
        ),
        re.compile(r"@(Controller|RestController|Service|Component)"),
    ],
}


def detect_entry_points(store: GraphStore) -> list[CodeNode]:
    """Detect entry points using graph structure + annotation signals.

    Strategy:
    1. Find functions with in-degree 0 (nobody calls them) — primary signal
    2. Check annotation patterns in function bodies/signatures — secondary signal
    3. Combine both signals

    Args:
        store: Graph store with populated nodes and edges.

    Returns:
        List of entry-point CodeNode objects.
    """
    g = store._get_nx()
    entry_points: list[CodeNode] = []
    seen_ids: set[str] = set()

    # Method 1: In-degree analysis
    for node_id in g.nodes():
        node_data = g.nodes[node_id]
        if node_data.get("kind") != NodeKind.FUNCTION.value:
            continue

        # Count non-CONTAINS incoming edges
        in_edges = [
            (u, v, d)
            for u, v, d in g.in_edges(node_id, data=True)
            if d.get("kind") != EdgeKind.CONTAINS.value
        ]

        if len(in_edges) == 0:
            node = store.get_node(node_id)
            if node and not node.is_test:
                node.is_entry_point = True
                entry_points.append(node)
                seen_ids.add(node_id)

    # Method 2: Annotation pattern matching
    all_funcs = store.conn.execute(
        "SELECT id, language, signature, body FROM nodes WHERE kind = 'function'"
    ).fetchall()

    for row in all_funcs:
        if row["id"] in seen_ids:
            continue

        lang = row["language"]
        patterns = ENTRY_ANNOTATIONS.get(lang, [])
        text = (row["signature"] or "") + "\n" + (row["body"] or "")

        for pat in patterns:
            if pat.search(text):
                node = store.get_node(row["id"])
                if node and not node.is_test:
                    node.is_entry_point = True
                    entry_points.append(node)
                    seen_ids.add(row["id"])
                break

    return entry_points


def get_flow_depth(
    store: GraphStore, entry_id: str, max_depth: int = 10
) -> int:
    """Get the maximum call depth from an entry point.

    Args:
        store: Graph store.
        entry_id: ID of the entry point node.
        max_depth: Maximum depth to traverse.

    Returns:
        Maximum depth reached from this entry point.
    """
    g = store._get_nx()
    if entry_id not in g:
        return 0

    visited: set[str] = set()
    max_found = 0

    def _dfs(node_id: str, depth: int) -> None:
        nonlocal max_found
        if depth > max_depth or node_id in visited:
            return
        visited.add(node_id)
        max_found = max(max_found, depth)

        for _, target, data in g.out_edges(node_id, data=True):
            if data.get("kind") in (EdgeKind.CALLS.value, "calls"):
                _dfs(target, depth + 1)

    _dfs(entry_id, 0)
    return max_found
