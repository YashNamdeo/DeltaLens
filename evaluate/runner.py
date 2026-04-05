"""Evaluation runner — benchmark DeltaLens against real repos.

Measures:
- Token reduction ratio (raw tokens vs DeltaLens context)
- Precision/recall of blast-radius prediction
- Latency per pipeline stage
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003

from deltalens.allocator import allocate_budget, estimate_tokens
from deltalens.classifier import classify_file_changes
from deltalens.config import DeltaLensConfig
from deltalens.graph import GraphStore
from deltalens.models import EdgeKind
from deltalens.parser import parse_directory, parse_file
from deltalens.scorer import (
    ScoringContext,
    filter_by_threshold,
    score_impacted_nodes,
)


@dataclass
class EvalResult:
    """Result of evaluating DeltaLens on a repo."""

    repo_name: str
    total_files: int = 0
    total_raw_tokens: int = 0
    total_delta_tokens: int = 0
    reduction_ratio: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    build_time_ms: float = 0.0
    context_time_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


def evaluate_repo(
    repo_root: Path,
    changed_files: list[str],
    ground_truth_impacted: list[str] | None = None,
    token_budget: int = 8000,
) -> EvalResult:
    """Run full evaluation pipeline on a repository.

    Args:
        repo_root: Path to the repository root.
        changed_files: List of files to treat as changed.
        ground_truth_impacted: Expected impacted files (for precision/recall).
        token_budget: Token budget to use.

    Returns:
        EvalResult with metrics.
    """
    result = EvalResult(repo_name=repo_root.name)
    cfg = DeltaLensConfig.load(repo_root)

    # Build graph
    t0 = time.perf_counter()
    store = GraphStore(":memory:")
    parse_results = parse_directory(repo_root, cfg.ignore_patterns)

    for pr in parse_results:
        store.ingest_parse_result(pr)
    store.resolve_unresolved_edges()

    t1 = time.perf_counter()
    result.build_time_ms = (t1 - t0) * 1000

    stats = store.stats()
    result.total_files = stats["files"]

    # Compute raw tokens (all file contents)
    raw_tokens = 0
    for pr in parse_results:
        for node in pr.nodes:
            raw_tokens += estimate_tokens(node.body or node.signature)
    result.total_raw_tokens = raw_tokens

    # Run context pipeline
    t2 = time.perf_counter()

    all_changed_nodes = []
    change_kinds: dict[str, str] = {}

    for fpath in changed_files:
        abs_path = str((repo_root / fpath).resolve())
        old_nodes = store.get_nodes_for_file(abs_path)
        new_result = parse_file(abs_path)
        if not new_result:
            result.errors.append(f"Cannot parse: {fpath}")
            continue

        classified = classify_file_changes(
            old_nodes, new_result.nodes, abs_path
        )
        for nc in classified.node_changes:
            change_kinds[nc.node_id] = nc.kind.value
        all_changed_nodes.extend(
            [n for n in new_result.nodes if n.id in change_kinds]
        )

    if all_changed_nodes:
        seed_ids = [n.id for n in all_changed_nodes]
        all_impl = all(k == "impl" for k in change_kinds.values())
        bfs_results = store.bfs_impacted(
            seed_ids, max_depth=1 if all_impl else 4
        )

        edge_lookup = {nid: EdgeKind.CALLS.value for nid, _ in bfs_results}
        node_lookup = {n.id: n for n in all_changed_nodes}
        for nid, _ in bfs_results:
            if nid not in node_lookup:
                maybe_node = store.get_node(nid)
                if maybe_node:
                    node_lookup[nid] = maybe_node

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
        payload = allocate_budget(scored, token_budget)

        result.total_delta_tokens = payload.total_tokens

    t3 = time.perf_counter()
    result.context_time_ms = (t3 - t2) * 1000

    # Token reduction ratio
    if result.total_delta_tokens > 0:
        result.reduction_ratio = (
            result.total_raw_tokens / result.total_delta_tokens
        )
    else:
        result.reduction_ratio = float("inf")

    # Precision / Recall if ground truth provided
    if ground_truth_impacted and all_changed_nodes:
        predicted = {
            s.node.file_path for s in scored if s.score >= cfg.impact_threshold
        }
        expected = set(
            str((repo_root / f).resolve()) for f in ground_truth_impacted
        )

        tp = len(predicted & expected)
        fp = len(predicted - expected)
        fn = len(expected - predicted)

        result.precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        result.recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        if result.precision + result.recall > 0:
            result.f1 = (
                2
                * (result.precision * result.recall)
                / (result.precision + result.recall)
            )

    store.close()
    return result


def format_eval_result(result: EvalResult) -> str:
    """Format eval result as a readable table."""
    lines = [
        f"Repo: {result.repo_name}",
        f"Files: {result.total_files}",
        f"Raw tokens: {result.total_raw_tokens:,}",
        f"Delta tokens: {result.total_delta_tokens:,}",
        f"Reduction: {result.reduction_ratio:.1f}x",
        f"Build time: {result.build_time_ms:.0f}ms",
        f"Context time: {result.context_time_ms:.0f}ms",
    ]
    if result.f1 > 0:
        lines.extend(
            [
                f"Precision: {result.precision:.2f}",
                f"Recall: {result.recall:.2f}",
                f"F1: {result.f1:.2f}",
            ]
        )
    if result.errors:
        lines.append(f"Errors: {len(result.errors)}")
    return "\n".join(lines)
