"""Tests for impact scoring."""

from deltalens.models import ChangeKind, CodeNode, EdgeKind, NodeKind
from deltalens.scorer import (
    ScoringContext,
    adjust_call_weight,
    compute_impact_score,
    filter_by_threshold,
    is_cross_module,
)


class TestComputeImpactScore:
    def test_direct_interface_call(self):
        score = compute_impact_score(
            EdgeKind.CALLS.value,
            distance=1,
            change_kind=ChangeKind.INTERFACE.value,
        )
        # base=0.55 × decay=0.6 × multiplier=1.0 = 0.33
        assert 0.3 < score < 0.4

    def test_direct_impl_call(self):
        score = compute_impact_score(
            EdgeKind.CALLS.value, distance=1, change_kind=ChangeKind.IMPL.value
        )
        # base=0.55 × decay=0.6 × multiplier=0.4 = 0.132
        assert score < 0.2

    def test_inheritance_interface(self):
        score = compute_impact_score(
            EdgeKind.INHERITS.value,
            distance=1,
            change_kind=ChangeKind.INTERFACE.value,
        )
        # base=1.0 × decay=0.6 × multiplier=1.0 = 0.6
        assert 0.55 < score < 0.65

    def test_score_decreases_with_distance(self):
        s1 = compute_impact_score(
            EdgeKind.CALLS.value, 1, ChangeKind.INTERFACE.value
        )
        s2 = compute_impact_score(
            EdgeKind.CALLS.value, 2, ChangeKind.INTERFACE.value
        )
        s3 = compute_impact_score(
            EdgeKind.CALLS.value, 3, ChangeKind.INTERFACE.value
        )
        assert s1 > s2 > s3

    def test_score_capped_at_1(self):
        score = compute_impact_score(
            EdgeKind.INHERITS.value,
            distance=0,
            change_kind=ChangeKind.INTERFACE.value,
        )
        assert score <= 1.0

    def test_tested_by_low_weight(self):
        score = compute_impact_score(
            EdgeKind.TESTED_BY.value,
            distance=1,
            change_kind=ChangeKind.INTERFACE.value,
        )
        assert score < 0.2

    def test_custom_weights(self):
        ctx = ScoringContext(
            edge_weights={EdgeKind.CALLS.value: 1.0},
            distance_decay=1.0,  # no decay
            change_multiplier={ChangeKind.INTERFACE.value: 1.0},
        )
        score = compute_impact_score(
            EdgeKind.CALLS.value,
            distance=5,
            change_kind=ChangeKind.INTERFACE.value,
            ctx=ctx,
        )
        assert score == 1.0


class TestCrossModule:
    def test_same_module(self):
        assert not is_cross_module("src/a.py", "src/a.py")

    def test_cross_module(self):
        assert is_cross_module("src/a.py", "src/b.py")

    def test_call_weight_same(self):
        assert adjust_call_weight("a.py", "a.py") == 0.4

    def test_call_weight_cross(self):
        assert adjust_call_weight("a.py", "b.py") == 0.7


class TestFilterByThreshold:
    def test_filters_below_threshold(self):
        from deltalens.models import ScoredNode

        node = CodeNode(
            id="test",
            kind=NodeKind.FUNCTION,
            name="test",
            qualified_name="test",
            file_path="test.py",
        )
        scored = [
            ScoredNode(node=node, score=0.8),
            ScoredNode(node=node, score=0.5),
            ScoredNode(node=node, score=0.2),
            ScoredNode(node=node, score=0.1),
        ]
        filtered = filter_by_threshold(scored, 0.3)
        assert len(filtered) == 2
        assert all(s.score >= 0.3 for s in filtered)
