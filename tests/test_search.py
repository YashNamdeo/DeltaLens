"""Tests for hybrid search and symbol normalization."""

import pytest

from deltalens.graph import GraphStore
from deltalens.models import CodeNode, NodeKind
from deltalens.search import (
    SearchResult,
    build_search_query,
    normalize_symbol,
    search_hybrid,
)


class TestNormalizeSymbol:
    def test_camel_case(self):
        tokens = normalize_symbol("getUserById")
        assert "get" in tokens
        assert "user" in tokens
        assert "by" in tokens
        assert "id" in tokens

    def test_snake_case(self):
        tokens = normalize_symbol("get_user_by_id")
        assert "get" in tokens
        assert "user" in tokens

    def test_pascal_case(self):
        tokens = normalize_symbol("UserService")
        assert "user" in tokens
        assert "service" in tokens

    def test_dot_notation(self):
        tokens = normalize_symbol("app.handle")
        assert "app" in tokens
        assert "handle" in tokens

    def test_abbreviation_expansion(self):
        tokens = normalize_symbol("reqHandler")
        assert "req" in tokens
        assert "request" in tokens  # expanded

    def test_empty_string(self):
        tokens = normalize_symbol("")
        assert tokens == []


class TestBuildSearchQuery:
    def test_simple_query(self):
        query = build_search_query("authenticate")
        assert "authenticate" in query

    def test_camel_case_query(self):
        query = build_search_query("getUserById")
        assert "get" in query
        assert "user" in query

    def test_preserves_original_terms(self):
        query = build_search_query("user service")
        assert "user" in query
        assert "service" in query


class TestSearchHybrid:
    @pytest.fixture
    def store(self):
        s = GraphStore(":memory:")
        for name in ["authenticate", "authorize", "getUserById", "createUser"]:
            node = CodeNode(
                id=f"test::{name}",
                kind=NodeKind.FUNCTION,
                name=name,
                qualified_name=name,
                file_path="test.py",
                signature=f"def {name}()",
            )
            s.upsert_node(node)
        s.conn.commit()
        yield s
        s.close()

    def test_basic_search(self, store):
        results = search_hybrid(store, "auth", top_k=5)
        assert len(results) > 0

    def test_search_returns_search_result(self, store):
        results = search_hybrid(store, "user", top_k=5)
        for r in results:
            assert isinstance(r, SearchResult)
            assert isinstance(r.node, CodeNode)
            assert isinstance(r.score, float)
