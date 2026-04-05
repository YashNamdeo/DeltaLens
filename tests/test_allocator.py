"""Tests for token budget allocation."""

from deltalens.allocator import (
    allocate_budget,
    assign_tiers,
    estimate_tokens,
    render_context,
)
from deltalens.models import CodeNode, NodeKind, RepresentationTier, ScoredNode


def _make_scored(
    name: str, score: float, body: str = "x = 1", sig: str = "def f()"
) -> ScoredNode:
    node = CodeNode(
        id=f"test.py::function::{name}::1",
        kind=NodeKind.FUNCTION,
        name=name,
        qualified_name=name,
        file_path="test.py",
        signature=sig,
        body=body,
        line_start=1,
        line_end=5,
    )
    return ScoredNode(node=node, score=score, reasoning="test")


class TestEstimateTokens:
    def test_short_string(self):
        assert estimate_tokens("hello") >= 1

    def test_proportional(self):
        short = estimate_tokens("abc")
        long = estimate_tokens("a" * 400)
        assert long > short

    def test_empty_string(self):
        assert estimate_tokens("") >= 1


class TestAssignTiers:
    def test_high_score_gets_full(self):
        nodes = [_make_scored("a", 0.9)]
        result = assign_tiers(nodes)
        assert result[0].tier == RepresentationTier.FULL

    def test_mid_score_gets_signature(self):
        nodes = [_make_scored("a", 0.6)]
        result = assign_tiers(nodes)
        assert result[0].tier == RepresentationTier.SIGNATURE

    def test_low_score_gets_summary(self):
        nodes = [_make_scored("a", 0.35)]
        result = assign_tiers(nodes)
        assert result[0].tier == RepresentationTier.SUMMARY

    def test_below_threshold_gets_name_only(self):
        nodes = [_make_scored("a", 0.1)]
        result = assign_tiers(nodes)
        assert result[0].tier == RepresentationTier.NAME_ONLY

    def test_custom_thresholds(self):
        nodes = [_make_scored("a", 0.5)]
        result = assign_tiers(nodes, high_threshold=0.4)
        assert result[0].tier == RepresentationTier.FULL


class TestAllocateBudget:
    def test_empty_nodes(self):
        payload = allocate_budget([], token_budget=8000)
        assert payload.total_tokens == 0
        assert len(payload.nodes) == 0

    def test_basic_allocation(self):
        nodes = [
            _make_scored("high", 0.9, body="return x * 2 + y"),
            _make_scored("mid", 0.6, sig="def process(data: list)"),
            _make_scored("low", 0.35),
        ]
        payload = allocate_budget(nodes, token_budget=8000)
        assert payload.total_tokens > 0
        assert payload.total_tokens <= 8000
        assert len(payload.nodes) > 0

    def test_budget_respected(self):
        # Create many nodes with large bodies
        nodes = [
            _make_scored(f"func_{i}", 0.9, body="x = 1\n" * 200)
            for i in range(50)
        ]
        payload = allocate_budget(nodes, token_budget=100)
        assert payload.total_tokens <= 100

    def test_downgrade_on_overflow(self):
        # One node with huge body, small budget
        nodes = [
            _make_scored("big", 0.9, body="x = 1\n" * 500, sig="def big()"),
        ]
        payload = allocate_budget(nodes, token_budget=20)
        # Should have been downgraded
        if payload.nodes:
            assert payload.nodes[0].tier in (
                RepresentationTier.SIGNATURE,
                RepresentationTier.SUMMARY,
            )

    def test_summary_in_payload(self):
        nodes = [_make_scored("a", 0.9)]
        payload = allocate_budget(nodes, token_budget=8000)
        assert "1 nodes" in payload.summary or "1 full" in payload.summary


class TestRenderContext:
    def test_render_produces_text(self):
        nodes = [_make_scored("a", 0.9, body="return 42")]
        payload = allocate_budget(nodes, token_budget=8000)
        text = render_context(payload)
        assert "DeltaLens Context" in text
        assert len(text) > 0

    def test_render_includes_file_info(self):
        nodes = [_make_scored("a", 0.9, body="return 42")]
        payload = allocate_budget(nodes, token_budget=8000)
        text = render_context(payload)
        assert "test.py" in text
