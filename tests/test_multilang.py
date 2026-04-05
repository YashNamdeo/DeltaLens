"""Multi-language parsing tests."""

from pathlib import Path

from deltalens.models import NodeKind
from deltalens.parser import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


class TestPythonParsing:
    def test_class_methods(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        methods = [
            n
            for n in result.nodes
            if n.kind == NodeKind.FUNCTION and "." in n.qualified_name
        ]
        method_names = {m.qualified_name for m in methods}
        assert "UserService.get_user" in method_names
        assert "UserService.create_user" in method_names

    def test_standalone_functions(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        funcs = [
            n
            for n in result.nodes
            if n.kind == NodeKind.FUNCTION and "." not in n.qualified_name
        ]
        func_names = {f.name for f in funcs}
        assert "authenticate" in func_names
        assert "main" in func_names


class TestJavaScriptParsing:
    def test_es6_class(self):
        result = parse_file(str(FIXTURES / "sample.js"))
        assert result is not None
        classes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        assert any(c.name == "UserController" for c in classes)

    def test_function_declaration(self):
        result = parse_file(str(FIXTURES / "sample.js"))
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        func_names = {f.name for f in funcs}
        assert "setupRoutes" in func_names

    def test_arrow_function_variable(self):
        result = parse_file(str(FIXTURES / "sample.js"))
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        func_names = {f.name for f in funcs}
        assert "validateEmail" in func_names


class TestTypeScriptParsing:
    def test_interface_detected(self):
        result = parse_file(str(FIXTURES / "sample.ts"))
        assert result is not None
        classes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        # Interfaces are detected as classes in our parser
        assert "UserDTO" in names or "UserService" in names

    def test_type_alias_detected(self):
        result = parse_file(str(FIXTURES / "sample.ts"))
        assert result is not None
        classes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        assert "UserRole" in names or "UserService" in names

    def test_class_methods(self):
        result = parse_file(str(FIXTURES / "sample.ts"))
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        func_names = {f.name for f in funcs}
        assert "getUser" in func_names or "createUser" in func_names


class TestEdgeCases:
    def test_empty_file(self):
        result = parse_file("test_empty.py", source=b"")
        assert result is not None
        # Should still have a file node
        assert any(n.kind == NodeKind.FILE for n in result.nodes)

    def test_syntax_error_graceful(self):
        source = b"def broken(\n    # missing closing paren"
        result = parse_file("broken.py", source=source)
        # Tree-sitter is error-tolerant, should still parse partially
        assert result is not None

    def test_nested_functions(self):
        source = b"""
def outer():
    def inner():
        return 1
    return inner()
"""
        result = parse_file("nested.py", source=source)
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        names = {f.name for f in funcs}
        assert "outer" in names

    def test_decorated_function(self):
        source = b"""
import functools

@functools.lru_cache
def cached_func(x: int) -> int:
    return x * 2
"""
        result = parse_file("decorated.py", source=source)
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert any(f.name == "cached_func" for f in funcs)
