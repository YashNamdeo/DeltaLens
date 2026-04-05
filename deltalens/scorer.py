"""Impact scorer — assigns 0-1 scores to nodes in the blast radius.

Pure functions. No I/O, no side effects. Scores drive the allocator's
decision about what representation tier each node gets.

Score formula:
    score = base_weight(edge_type) × distance_decay(hops) × change_multiplier(kind)
"""

from __future__ import annotations

from dataclasses import dataclass

from deltalens.models import (
    ChangeKind,
    CodeNode,
    EdgeKind,
    ScoredNode,
)

# ---------------------------------------------------------------------------
# Configurable weights
# ---------------------------------------------------------------------------

EDGE_WEIGHTS: dict[str, float] = {
    EdgeKind.CALLS.value: 0.55,  # average of same-module (0.4) and cross-module (0.7)
    EdgeKind.IMPORTS_FROM.value: 0.9,
    EdgeKind.INHERITS.value: 1.0,
    EdgeKind.IMPLEMENTS.value: 0.95,
    EdgeKind.CONTAINS.value: 0.0,  # structural, not impact
    EdgeKind.TESTED_BY.value: 0.3,
    EdgeKind.OVERRIDES.value: 0.85,
}

CHANGE_MULTIPLIER: dict[str, float] = {
    ChangeKind.INTERFACE.value: 1.0,
    ChangeKind.IMPL.value: 0.4,
    ChangeKind.ADDED.value: 0.8,
    ChangeKind.DELETED.value: 0.9,
}

DEFAULT_DISTANCE_DECAY = 0.6  # multiplicative per hop: 1.0, 0.6, 0.36, 0.216...
DEFAULT_IMPACT_THRESHOLD = 0.3


@dataclass
class ScoringContext:
    """Parameters for impact scoring."""

    distance_decay: float = DEFAULT_DISTANCE_DECAY
    impact_threshold: float = DEFAULT_IMPACT_THRESHOLD
    edge_weights: dict[str, float] | None = None
    change_multiplier: dict[str, float] | None = None

    @property
    def weights(self) -> dict[str, float]:
        return self.edge_weights or EDGE_WEIGHTS

    @property
    def multipliers(self) -> dict[str, float]:
        return self.change_multiplier or CHANGE_MULTIPLIER


def compute_impact_score(
    edge_kind: str,
    distance: int,
    change_kind: str,
    ctx: ScoringContext | None = None,
) -> float:
    """Compute the impact score for a single node.

    Args:
        edge_kind: The type of edge connecting this node to the change.
        distance: Number of hops from the changed node.
        change_kind: The kind of change that triggered this analysis.
        ctx: Optional scoring context with custom weights.

    Returns:
        Float score between 0.0 and 1.0.
    """
    c = ctx or ScoringContext()

    base = c.weights.get(edge_kind, 0.5)
    decay = c.distance_decay**distance
    multiplier = c.multipliers.get(change_kind, 0.5)

    return min(1.0, base * decay * multiplier)


def score_impacted_nodes(
    changed_nodes: list[CodeNode],
    change_kinds: dict[str, str],
    bfs_results: list[tuple[str, int]],
    edge_lookup: dict[str, str],
    node_lookup: dict[str, CodeNode],
    ctx: ScoringContext | None = None,
) -> list[ScoredNode]:
    """Score all impacted nodes from a BFS traversal.

    Args:
        changed_nodes: The nodes that were directly changed.
        change_kinds: Map of node_id → ChangeKind value.
        bfs_results: List of (node_id, depth) from BFS.
        edge_lookup: Map of node_id → EdgeKind value for the edge that reached it.
        node_lookup: Map of node_id → CodeNode.
        ctx: Optional scoring context.

    Returns:
        Sorted list of ScoredNode (highest score first).
    """
    c = ctx or ScoringContext()
    scored: list[ScoredNode] = []

    # Changed nodes get score 1.0
    changed_ids = {n.id for n in changed_nodes}
    for node in changed_nodes:
        scored.append(
            ScoredNode(
                node=node,
                score=1.0,
                reasoning="directly changed",
            )
        )

    # Score BFS-discovered nodes
    for node_id, depth in bfs_results:
        if node_id in changed_ids:
            continue  # already scored

        maybe_node = node_lookup.get(node_id)
        if not maybe_node:
            continue
        node = maybe_node

        edge_kind = edge_lookup.get(node_id, EdgeKind.CALLS.value)

        # Use the max change_kind among all changed nodes that could affect this
        # (simplification: use the most impactful change kind)
        max_change = _dominant_change_kind(change_kinds)

        score = compute_impact_score(edge_kind, depth, max_change, c)

        reasoning = _build_reasoning(edge_kind, depth, max_change, score)

        scored.append(
            ScoredNode(
                node=node,
                score=round(score, 3),
                reasoning=reasoning,
            )
        )

    # Sort by score descending
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def is_cross_module(source_path: str, target_path: str) -> bool:
    """Check if two nodes are in different modules (files)."""
    return source_path != target_path


def adjust_call_weight(source_path: str, target_path: str) -> float:
    """Return 0.4 for same-module calls, 0.7 for cross-module."""
    if is_cross_module(source_path, target_path):
        return 0.7
    return 0.4


def _dominant_change_kind(change_kinds: dict[str, str]) -> str:
    """Find the most impactful change kind from a set."""
    priority = [
        ChangeKind.INTERFACE.value,
        ChangeKind.DELETED.value,
        ChangeKind.ADDED.value,
        ChangeKind.IMPL.value,
    ]
    for kind in priority:
        if kind in change_kinds.values():
            return kind
    return ChangeKind.IMPL.value


def _build_reasoning(
    edge_kind: str, depth: int, change_kind: str, score: float
) -> str:
    """Build a human-readable reasoning string for a score."""
    parts = []
    parts.append(f"edge={edge_kind}")
    parts.append(f"depth={depth}")
    parts.append(f"change={change_kind}")
    parts.append(f"score={score:.3f}")
    return " | ".join(parts)


def filter_by_threshold(
    scored: list[ScoredNode], threshold: float = DEFAULT_IMPACT_THRESHOLD
) -> list[ScoredNode]:
    """Filter out nodes below the impact threshold."""
    return [s for s in scored if s.score >= threshold]
