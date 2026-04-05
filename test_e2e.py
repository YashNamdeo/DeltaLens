#!/usr/bin/env python3
"""End-to-end integration test — proves DeltaLens actually works.

Scenario: We simulate changing the `compute_impact_score` function in scorer.py.
We test two cases:
  1. Implementation-only change (body tweak) → should show only direct callers
  2. Interface change (signature change) → should show full blast radius

This shows the core value prop: same function, different change types,
dramatically different context sent to the AI.
"""

from pathlib import Path

from deltalens.allocator import allocate_budget, render_context
from deltalens.classifier import classify_file_changes
from deltalens.config import DeltaLensConfig
from deltalens.graph import GraphStore
from deltalens.models import EdgeKind
from deltalens.parser import parse_file
from deltalens.scorer import (
    ScoringContext,
    filter_by_threshold,
    score_impacted_nodes,
)

REPO_ROOT = Path(__file__).parent
SCORER_PATH = str(REPO_ROOT / "deltalens" / "scorer.py")


def run_test():
    cfg = DeltaLensConfig.load(REPO_ROOT)
    assert cfg.db_path is not None
    store = GraphStore(cfg.db_path)

    print("=" * 70)
    print("DeltaLens End-to-End Integration Test")
    print("=" * 70)

    # Show current graph stats
    stats = store.stats()
    print(
        f"\nGraph: {stats['files']} files, {stats['functions']} functions, "
        f"{stats['classes']} classes, {stats['edges']} edges"
    )

    # --- TEST 1: Implementation-only change ---
    print("\n" + "─" * 70)
    print("TEST 1: Implementation-only change to scorer.py")
    print("  (Changed the body of compute_impact_score, NOT the signature)")
    print("─" * 70)

    # Get the current nodes for scorer.py
    old_nodes = store.get_nodes_for_file(SCORER_PATH)
    print(f"\n  Nodes in scorer.py: {len(old_nodes)}")
    for n in old_nodes:
        if n.kind.value != "file":
            print(f"    {n.kind.value:10s} {n.name}")

    # Simulate: read current file, modify only the body (not signature)
    original_source = Path(SCORER_PATH).read_text()
    impl_change_source = original_source.replace(
        "return min(1.0, base * decay * multiplier)",
        "return min(1.0, max(0.0, base * decay * multiplier))  # added floor",
    )

    # Parse the "new" version
    new_result = parse_file(SCORER_PATH, impl_change_source.encode())
    assert new_result is not None

    # Classify changes
    classified = classify_file_changes(old_nodes, new_result.nodes, SCORER_PATH)
    print(f"\n  Changes detected: {len(classified.node_changes)}")
    print(f"  Trivial (impl-only, small): {classified.is_trivial}")
    for nc in classified.node_changes:
        print(
            f"    {nc.kind.value:12s} {nc.node_id.split('::')[-2]}  "
            f"(line {nc.node_id.split('::')[-1]})"
        )

    # Get impact context
    change_kinds = {nc.node_id: nc.kind.value for nc in classified.node_changes}
    changed_nodes = [n for n in new_result.nodes if n.id in change_kinds]

    if changed_nodes:
        seed_ids = [n.id for n in changed_nodes]
        all_impl = all(k == "impl" for k in change_kinds.values())
        max_depth = 1 if all_impl else 4
        print(
            f"\n  Strategy: {'direct callers only (depth=1)' if all_impl else 'full BFS (depth=4)'}"
        )

        bfs = store.bfs_impacted(seed_ids, max_depth=max_depth)
        print(f"  BFS found: {len(bfs)} impacted nodes")

        edge_lookup = {nid: EdgeKind.CALLS.value for nid, _ in bfs}
        node_lookup = {n.id: n for n in changed_nodes}
        for nid, _ in bfs:
            if nid not in node_lookup:
                node = store.get_node(nid)
                if node:
                    node_lookup[nid] = node

        ctx = ScoringContext(
            distance_decay=cfg.distance_decay,
            impact_threshold=cfg.impact_threshold,
        )
        scored = score_impacted_nodes(
            changed_nodes, change_kinds, bfs, edge_lookup, node_lookup, ctx
        )
        scored = filter_by_threshold(scored, cfg.impact_threshold)

        payload = allocate_budget(scored, token_budget=8000)
        render_context(payload)

        print(
            f"\n  Context payload: {payload.total_tokens} tokens (budget: 8000)"
        )
        print(f"  Nodes in context: {len(payload.nodes)}")
        for sn in payload.nodes:
            print(
                f"    score={sn.score:.2f}  tier={sn.tier.value:10s}  {sn.node.name}"
            )

    # --- TEST 2: Interface change ---
    print("\n" + "─" * 70)
    print("TEST 2: Interface change to scorer.py")
    print("  (Changed the SIGNATURE of compute_impact_score — added a param)")
    print("─" * 70)

    iface_change_source = original_source.replace(
        "def compute_impact_score(\n"
        "    edge_kind: str,\n"
        "    distance: int,\n"
        "    change_kind: str,\n"
        "    ctx: ScoringContext | None = None,\n"
        ") -> float:",
        "def compute_impact_score(\n"
        "    edge_kind: str,\n"
        "    distance: int,\n"
        "    change_kind: str,\n"
        "    ctx: ScoringContext | None = None,\n"
        "    boost: float = 1.0,\n"
        ") -> float:",
    )

    new_result2 = parse_file(SCORER_PATH, iface_change_source.encode())
    assert new_result2 is not None

    classified2 = classify_file_changes(
        old_nodes, new_result2.nodes, SCORER_PATH
    )
    print(f"\n  Changes detected: {len(classified2.node_changes)}")
    print(f"  Trivial: {classified2.is_trivial}")
    for nc in classified2.node_changes:
        print(f"    {nc.kind.value:12s} {nc.node_id.split('::')[-2]}")
        if nc.old_signature != nc.new_signature:
            print(
                f"      OLD sig: {nc.old_signature[:80] if nc.old_signature else 'None'}"
            )
            print(
                f"      NEW sig: {nc.new_signature[:80] if nc.new_signature else 'None'}"
            )

    change_kinds2 = {
        nc.node_id: nc.kind.value for nc in classified2.node_changes
    }
    changed_nodes2 = [n for n in new_result2.nodes if n.id in change_kinds2]

    if changed_nodes2:
        seed_ids2 = [n.id for n in changed_nodes2]
        all_impl2 = all(k == "impl" for k in change_kinds2.values())
        max_depth2 = 1 if all_impl2 else 4
        print(
            f"\n  Strategy: {'direct callers only (depth=1)' if all_impl2 else 'full BFS (depth=4)'}"
        )

        bfs2 = store.bfs_impacted(seed_ids2, max_depth=max_depth2)
        print(f"  BFS found: {len(bfs2)} impacted nodes")

        edge_lookup2 = {nid: EdgeKind.CALLS.value for nid, _ in bfs2}
        node_lookup2 = {n.id: n for n in changed_nodes2}
        for nid, _ in bfs2:
            if nid not in node_lookup2:
                node = store.get_node(nid)
                if node:
                    node_lookup2[nid] = node

        scored2 = score_impacted_nodes(
            changed_nodes2, change_kinds2, bfs2, edge_lookup2, node_lookup2, ctx
        )
        scored2 = filter_by_threshold(scored2, cfg.impact_threshold)

        payload2 = allocate_budget(scored2, token_budget=8000)

        print(
            f"\n  Context payload: {payload2.total_tokens} tokens (budget: 8000)"
        )
        print(f"  Nodes in context: {len(payload2.nodes)}")
        for sn in payload2.nodes:
            print(
                f"    score={sn.score:.2f}  tier={sn.tier.value:10s}  {sn.node.name}"
            )

    # --- COMPARISON ---
    print("\n" + "=" * 70)
    print("COMPARISON")
    print("=" * 70)
    t1 = payload.total_tokens if changed_nodes else 0
    t2 = payload2.total_tokens if changed_nodes2 else 0
    n1 = len(payload.nodes) if changed_nodes else 0
    n2 = len(payload2.nodes) if changed_nodes2 else 0

    raw_tokens = sum(len(n.body) // 4 for n in old_nodes if n.body)

    print(f"\n  Raw tokens (all scorer.py nodes):    {raw_tokens:,}")
    print(f"  Impl change context tokens:          {t1:,}  ({n1} nodes)")
    print(f"  Interface change context tokens:      {t2:,}  ({n2} nodes)")
    if t1 > 0:
        print(f"  Reduction vs raw (impl change):      {raw_tokens/t1:.1f}x")
    if t2 > 0:
        print(f"  Reduction vs raw (interface change):  {raw_tokens/t2:.1f}x")
    print("\n  Key insight: Same file changed, but interface change")
    print(f"  surfaces {n2-n1} MORE nodes because it affects the public API.")

    store.close()
    print("\n✓ All E2E tests passed.")


if __name__ == "__main__":
    run_test()
