"""Tests for the change classifier — the core innovation."""

from deltalens.classifier import (
    _classify_node_change,
    classify_diff_text,
    classify_file_changes,
)
from deltalens.models import ChangeKind, CodeNode, NodeKind


def _make_node(
    name: str,
    signature: str = "",
    body: str = "",
    kind: NodeKind = NodeKind.FUNCTION,
) -> CodeNode:
    return CodeNode(
        id=f"test.py::function::{name}::1",
        kind=kind,
        name=name,
        qualified_name=name,
        file_path="test.py",
        signature=signature,
        body=body,
    )


class TestClassifyNodeChange:
    def test_interface_change_signature(self):
        old = _make_node("foo", signature="def foo(x: int)", body="return x")
        new = _make_node(
            "foo", signature="def foo(x: int, y: int)", body="return x + y"
        )
        assert _classify_node_change(old, new) == ChangeKind.INTERFACE

    def test_impl_change_body_only(self):
        old = _make_node("foo", signature="def foo(x: int)", body="return x")
        new = _make_node(
            "foo", signature="def foo(x: int)", body="return x * 2"
        )
        assert _classify_node_change(old, new) == ChangeKind.IMPL

    def test_interface_change_modifiers(self):
        old = _make_node("foo", signature="def foo()")
        old.modifiers = ["staticmethod"]
        new = _make_node("foo", signature="def foo()")
        new.modifiers = ["classmethod"]
        assert _classify_node_change(old, new) == ChangeKind.INTERFACE

    def test_whitespace_in_signature_ignored(self):
        old = _make_node("foo", signature="def foo( x: int )", body="return x")
        new = _make_node(
            "foo", signature="def foo(x: int)", body="return x * 2"
        )
        assert _classify_node_change(old, new) == ChangeKind.IMPL


class TestClassifyFileChanges:
    def test_added_node(self):
        old_nodes: list[CodeNode] = []
        new_nodes = [_make_node("bar", body="return 1")]

        result = classify_file_changes(old_nodes, new_nodes, "test.py")
        assert len(result.node_changes) == 1
        assert result.node_changes[0].kind == ChangeKind.ADDED

    def test_deleted_node(self):
        old_nodes = [_make_node("bar", body="return 1")]
        new_nodes: list[CodeNode] = []

        result = classify_file_changes(old_nodes, new_nodes, "test.py")
        assert len(result.node_changes) == 1
        assert result.node_changes[0].kind == ChangeKind.DELETED

    def test_unchanged_node(self):
        node = _make_node("bar", signature="def bar()", body="return 1")
        result = classify_file_changes([node], [node], "test.py")
        assert len(result.node_changes) == 0

    def test_trivial_detection(self):
        old = _make_node("foo", signature="def foo()", body="return 1")
        new = _make_node("foo", signature="def foo()", body="return 2")

        result = classify_file_changes([old], [new], "test.py")
        assert result.is_trivial is True
        assert all(c.kind == ChangeKind.IMPL for c in result.node_changes)

    def test_non_trivial_interface_change(self):
        old = _make_node("foo", signature="def foo(x: int)", body="return x")
        new = _make_node(
            "foo", signature="def foo(x: int, y: str)", body="return x"
        )

        result = classify_file_changes([old], [new], "test.py")
        assert result.is_trivial is False

    def test_mixed_changes(self):
        old_a = _make_node("a", signature="def a()", body="return 1")
        old_b = _make_node("b", signature="def b(x)", body="return x")
        new_a = _make_node("a", signature="def a()", body="return 2")  # impl
        new_b = _make_node(
            "b", signature="def b(x, y)", body="return x + y"
        )  # interface
        new_c = _make_node("c", body="return 3")  # added

        result = classify_file_changes(
            [old_a, old_b], [new_a, new_b, new_c], "test.py"
        )
        kinds = {c.kind for c in result.node_changes}
        assert ChangeKind.IMPL in kinds
        assert ChangeKind.INTERFACE in kinds
        assert ChangeKind.ADDED in kinds


class TestClassifyDiffText:
    def test_extract_changed_files(self):
        diff = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-old
+new
diff --git a/bar.py b/bar.py
--- a/bar.py
+++ b/bar.py
@@ -1 +1 @@
-old
+new"""
        files = classify_diff_text(diff)
        assert "foo.py" in files
        assert "bar.py" in files

    def test_new_file(self):
        diff = """diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+print("hello")"""
        files = classify_diff_text(diff)
        assert "new.py" in files
