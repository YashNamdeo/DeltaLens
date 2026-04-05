"""Tests for the SQLite graph store."""

import pytest

from deltalens.graph import GraphStore
from deltalens.models import CodeEdge, CodeNode, EdgeKind, NodeKind, ParseResult


@pytest.fixture
def store():
    """In-memory graph store for testing."""
    s = GraphStore(":memory:")
    yield s
    s.close()


def _make_node(
    name: str, file_path: str = "test.py", kind: NodeKind = NodeKind.FUNCTION
) -> CodeNode:
    return CodeNode(
        id=f"{file_path}::function::{name}::1",
        kind=kind,
        name=name,
        qualified_name=name,
        file_path=file_path,
        language="python",
        line_start=1,
        line_end=10,
        signature=f"def {name}()",
        body=f"return '{name}'",
    )


class TestGraphCRUD:
    def test_upsert_and_get_node(self, store):
        node = _make_node("foo")
        store.upsert_node(node)
        store.conn.commit()

        got = store.get_node(node.id)
        assert got is not None
        assert got.name == "foo"
        assert got.signature == "def foo()"

    def test_upsert_edge(self, store):
        a = _make_node("a")
        b = _make_node("b")
        store.upsert_node(a)
        store.upsert_node(b)
        store.upsert_edge(CodeEdge(a.id, b.id, EdgeKind.CALLS))
        store.conn.commit()

        # Verify via NetworkX
        g = store._get_nx()
        assert g.has_edge(a.id, b.id)

    def test_get_nodes_for_file(self, store):
        a = _make_node("a", "src/main.py")
        b = _make_node("b", "src/main.py")
        c = _make_node("c", "src/other.py")
        for n in [a, b, c]:
            store.upsert_node(n)
        store.conn.commit()

        nodes = store.get_nodes_for_file("src/main.py")
        assert len(nodes) == 2
        assert {n.name for n in nodes} == {"a", "b"}

    def test_remove_file(self, store):
        a = _make_node("a", "rm.py")
        b = _make_node("b", "rm.py")
        store.upsert_node(a)
        store.upsert_node(b)
        store.upsert_edge(CodeEdge(a.id, b.id, EdgeKind.CALLS))
        store.conn.commit()

        store.remove_file("rm.py")
        assert store.get_node(a.id) is None
        assert store.get_node(b.id) is None


class TestGraphTraversal:
    def test_bfs_impacted(self, store):
        # a → b → c
        a = _make_node("a")
        b = _make_node("b")
        c = _make_node("c")
        for n in [a, b, c]:
            store.upsert_node(n)
        store.upsert_edge(CodeEdge(b.id, a.id, EdgeKind.CALLS))  # b calls a
        store.upsert_edge(CodeEdge(c.id, b.id, EdgeKind.CALLS))  # c calls b
        store.conn.commit()

        # If a changes, BFS backwards: a → b → c
        results = store.bfs_impacted([a.id], max_depth=3)
        node_ids = {nid for nid, _ in results}
        assert a.id in node_ids
        assert b.id in node_ids
        assert c.id in node_ids

    def test_bfs_respects_max_depth(self, store):
        a = _make_node("a")
        b = _make_node("b")
        c = _make_node("c")
        for n in [a, b, c]:
            store.upsert_node(n)
        store.upsert_edge(CodeEdge(b.id, a.id, EdgeKind.CALLS))
        store.upsert_edge(CodeEdge(c.id, b.id, EdgeKind.CALLS))
        store.conn.commit()

        results = store.bfs_impacted([a.id], max_depth=1)
        node_ids = {nid for nid, _ in results}
        assert a.id in node_ids
        assert b.id in node_ids
        assert c.id not in node_ids  # depth 2, beyond max_depth=1

    def test_get_direct_callers(self, store):
        a = _make_node("a")
        b = _make_node("b")
        store.upsert_node(a)
        store.upsert_node(b)
        store.upsert_edge(CodeEdge(b.id, a.id, EdgeKind.CALLS))
        store.conn.commit()

        callers = store.get_direct_callers(a.id)
        assert len(callers) == 1
        assert callers[0].name == "b"


class TestGraphIngest:
    def test_ingest_parse_result(self, store):
        result = ParseResult(
            file_path="test.py",
            language="python",
            nodes=[_make_node("x")],
            edges=[],
            sha256="abc123",
        )
        store.ingest_parse_result(result)

        assert store.get_node(result.nodes[0].id) is not None
        assert store.get_file_hash("test.py") == "abc123"

    def test_ingest_replaces_old_data(self, store):
        old = ParseResult(
            file_path="test.py",
            language="python",
            nodes=[_make_node("old")],
            edges=[],
            sha256="old",
        )
        store.ingest_parse_result(old)

        new = ParseResult(
            file_path="test.py",
            language="python",
            nodes=[_make_node("new")],
            edges=[],
            sha256="new",
        )
        store.ingest_parse_result(new)

        nodes = store.get_nodes_for_file("test.py")
        names = {n.name for n in nodes}
        assert "new" in names
        assert "old" not in names


class TestGraphStats:
    def test_stats(self, store):
        node = _make_node("a")
        node.kind = NodeKind.FILE
        store.upsert_node(node)
        store.conn.commit()

        stats = store.stats()
        assert stats["nodes"] >= 1
        assert "files" in stats
        assert "edges" in stats


class TestGraphSearch:
    def test_like_search(self, store):
        store.upsert_node(_make_node("authenticate"))
        store.upsert_node(_make_node("authorize"))
        store.conn.commit()

        results = store.search_like("auth")
        assert len(results) == 2

    def test_search_no_results(self, store):
        results = store.search_like("nonexistent_xyz")
        assert len(results) == 0
