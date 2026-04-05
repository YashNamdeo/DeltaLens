"""Token budget allocator — decides representation tier per node.

Given a token budget and scored impact list, selects how each file/node
should be represented in the context payload:
- Full source with diff markers (high score)
- Function signatures + docstrings (medium score)
- One-line structural summary (low score)
- Name only / excluded (below threshold)

Knows nothing about Tree-sitter or the graph. Pure allocation logic.
"""

from __future__ import annotations

from deltalens.models import ContextPayload, RepresentationTier, ScoredNode

# Approximate token counts per representation type
TOKENS_PER_CHAR = 0.25  # rough: 1 token ≈ 4 chars


def estimate_tokens(text: str) -> int:
    """Estimate token count for a string. Rough heuristic: 1 token ≈ 4 chars."""
    return max(1, int(len(text) * TOKENS_PER_CHAR))


def _render_full(node: ScoredNode) -> str:
    """Render full source representation."""
    parts = [
        f"# {node.node.file_path}:{node.node.line_start}-{node.node.line_end}"
    ]
    parts.append(f"# Score: {node.score:.2f} | {node.reasoning}")
    parts.append(node.node.body or node.node.signature)
    return "\n".join(parts)


def _render_signature(node: ScoredNode) -> str:
    """Render signature-only representation."""
    parts = [f"# {node.node.file_path}:{node.node.line_start}"]
    if node.node.signature:
        parts.append(node.node.signature)
    if node.node.docstring:
        parts.append(f'  """{node.node.docstring}"""')
    return "\n".join(parts)


def _render_summary(node: ScoredNode) -> str:
    """Render one-line summary."""
    return f"{node.node.kind.value} {node.node.qualified_name} @ {node.node.file_path}:{node.node.line_start}"


def assign_tiers(
    scored_nodes: list[ScoredNode],
    high_threshold: float = 0.8,
    mid_threshold: float = 0.5,
    low_threshold: float = 0.3,
) -> list[ScoredNode]:
    """Assign representation tiers based on impact score.

    Args:
        scored_nodes: Nodes with computed impact scores.
        high_threshold: Score above which nodes get full source.
        mid_threshold: Score above which nodes get signatures.
        low_threshold: Score above which nodes get summaries.

    Returns:
        Nodes with tier field populated.
    """
    for node in scored_nodes:
        if node.score >= high_threshold:
            node.tier = RepresentationTier.FULL
        elif node.score >= mid_threshold:
            node.tier = RepresentationTier.SIGNATURE
        elif node.score >= low_threshold:
            node.tier = RepresentationTier.SUMMARY
        else:
            node.tier = RepresentationTier.NAME_ONLY
    return scored_nodes


def allocate_budget(
    scored_nodes: list[ScoredNode],
    token_budget: int = 8000,
    high_threshold: float = 0.8,
    mid_threshold: float = 0.5,
    low_threshold: float = 0.3,
) -> ContextPayload:
    """Allocate token budget across impacted nodes.

    Strategy:
    1. Assign tiers based on score thresholds.
    2. Render each node at its assigned tier.
    3. If total exceeds budget, downgrade lowest-scoring nodes.
    4. Build the final context payload.

    Args:
        scored_nodes: Scored and sorted nodes (highest first).
        token_budget: Maximum tokens to allocate.
        high_threshold: Score threshold for full source.
        mid_threshold: Score threshold for signatures.
        low_threshold: Score threshold for summaries.

    Returns:
        ContextPayload with rendered representations within budget.
    """
    if not scored_nodes:
        return ContextPayload(
            changed_files=[],
            total_tokens=0,
            budget=token_budget,
            nodes=[],
            summary="No impacted nodes found.",
        )

    # Step 1: Assign initial tiers
    nodes = assign_tiers(
        list(scored_nodes), high_threshold, mid_threshold, low_threshold
    )

    # Step 2: Calculate token costs and fit within budget
    total_tokens = 0
    included: list[ScoredNode] = []

    for node in nodes:
        if node.tier == RepresentationTier.NAME_ONLY:
            continue

        cost = _estimate_tier_cost(node)

        if total_tokens + cost > token_budget:
            # Try downgrading
            downgraded_cost = _try_downgrade(node)
            if total_tokens + downgraded_cost <= token_budget:
                total_tokens += downgraded_cost
                included.append(node)
            # else: exclude this node
        else:
            total_tokens += cost
            included.append(node)

    # Collect changed files
    changed_files = list({n.node.file_path for n in included if n.score >= 0.8})

    summary = _build_summary(included, total_tokens, token_budget)

    return ContextPayload(
        changed_files=changed_files,
        total_tokens=total_tokens,
        budget=token_budget,
        nodes=included,
        summary=summary,
    )


def render_context(payload: ContextPayload) -> str:
    """Render the context payload as a string for the model.

    Args:
        payload: The allocated context payload.

    Returns:
        Formatted string ready for model consumption.
    """
    parts: list[str] = []

    parts.append(
        f"## DeltaLens Context ({payload.total_tokens}/{payload.budget} tokens)"
    )
    parts.append(f"Changed files: {', '.join(payload.changed_files) or 'none'}")
    parts.append("")

    for node in payload.nodes:
        if node.tier == RepresentationTier.FULL:
            parts.append(_render_full(node))
        elif node.tier == RepresentationTier.SIGNATURE:
            parts.append(_render_signature(node))
        elif node.tier == RepresentationTier.SUMMARY:
            parts.append(_render_summary(node))
        parts.append("")

    if payload.summary:
        parts.append(f"---\n{payload.summary}")

    return "\n".join(parts)


def _estimate_tier_cost(node: ScoredNode) -> int:
    """Estimate token cost for a node at its current tier."""
    if node.tier == RepresentationTier.FULL:
        text = node.node.body or node.node.signature
        return estimate_tokens(text) + 10  # overhead for headers
    elif node.tier == RepresentationTier.SIGNATURE:
        text = node.node.signature + (node.node.docstring or "")
        return estimate_tokens(text) + 5
    elif node.tier == RepresentationTier.SUMMARY:
        return 3  # one-line summary
    return 0


def _try_downgrade(node: ScoredNode) -> int:
    """Try downgrading a node to a cheaper tier. Returns new cost."""
    if node.tier == RepresentationTier.FULL:
        node.tier = RepresentationTier.SIGNATURE
        return _estimate_tier_cost(node)
    elif node.tier == RepresentationTier.SIGNATURE:
        node.tier = RepresentationTier.SUMMARY
        return 3
    return 0


def _build_summary(
    nodes: list[ScoredNode], total_tokens: int, budget: int
) -> str:
    """Build a summary string for the context payload."""
    full = sum(1 for n in nodes if n.tier == RepresentationTier.FULL)
    sig = sum(1 for n in nodes if n.tier == RepresentationTier.SIGNATURE)
    summ = sum(1 for n in nodes if n.tier == RepresentationTier.SUMMARY)
    return (
        f"{len(nodes)} nodes in context: "
        f"{full} full, {sig} signatures, {summ} summaries. "
        f"Tokens: {total_tokens}/{budget}"
    )
