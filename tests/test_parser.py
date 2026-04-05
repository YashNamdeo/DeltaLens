"""Tests for the Tree-sitter parser module."""

from pathlib import Path

from deltalens.models import EdgeKind, NodeKind
from deltalens.parser import detect_language, parse_file

FIXTURES = Path(__file__).parent / "fixtures"


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("foo.py") == "python"

    def test_javascript(self):
        assert detect_language("foo.js") == "javascript"
        assert detect_language("foo.jsx") == "javascript"

    def test_typescript(self):
        assert detect_language("foo.ts") == "typescript"
        assert detect_language("foo.tsx") == "tsx"

    def test_unknown(self):
        assert detect_language("foo.txt") is None
        assert detect_language("foo.md") is None


class TestParsePython:
    def test_parse_sample(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        assert result.language == "python"
        assert len(result.nodes) > 0
        assert len(result.edges) > 0

    def test_file_node_created(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        file_nodes = [n for n in result.nodes if n.kind == NodeKind.FILE]
        assert len(file_nodes) == 1
        assert file_nodes[0].name == "sample.py"

    def test_class_detected(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        classes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        class_names = {c.name for c in classes}
        assert "UserService" in class_names
        assert "AdminService" in class_names

    def test_functions_detected(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        func_names = {f.name for f in funcs}
        assert "get_user" in func_names
        assert "create_user" in func_names
        assert "authenticate" in func_names
        assert "main" in func_names

    def test_signatures_extracted(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        get_user = next((n for n in result.nodes if n.name == "get_user"), None)
        assert get_user is not None
        assert "user_id" in get_user.signature
        assert "def get_user" in get_user.signature

    def test_docstrings_extracted(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        get_user = next((n for n in result.nodes if n.name == "get_user"), None)
        assert get_user is not None
        assert "Fetch a user" in get_user.docstring

    def test_contains_edges(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        contains = [e for e in result.edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) > 0

    def test_call_edges(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        calls = [e for e in result.edges if e.kind == EdgeKind.CALLS]
        assert len(calls) > 0

    def test_import_edges(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        imports = [e for e in result.edges if e.kind == EdgeKind.IMPORTS_FROM]
        assert len(imports) > 0

    def test_inheritance_edges(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        inherits = [e for e in result.edges if e.kind == EdgeKind.INHERITS]
        assert len(inherits) > 0  # AdminService → UserService

    def test_parse_from_bytes(self):
        source = b'def hello():\n    return "world"\n'
        result = parse_file("test.py", source=source)
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        assert any(f.name == "hello" for f in funcs)

    def test_sha256_computed(self):
        result = parse_file(str(FIXTURES / "sample.py"))
        assert result is not None
        assert len(result.sha256) == 64  # SHA-256 hex length


class TestParseJavaScript:
    def test_parse_sample(self):
        result = parse_file(str(FIXTURES / "sample.js"))
        assert result is not None
        assert result.language == "javascript"

    def test_class_detected(self):
        result = parse_file(str(FIXTURES / "sample.js"))
        assert result is not None
        classes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        assert any(c.name == "UserController" for c in classes)

    def test_functions_detected(self):
        result = parse_file(str(FIXTURES / "sample.js"))
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        func_names = {f.name for f in funcs}
        assert "setupRoutes" in func_names

    def test_arrow_functions_detected(self):
        result = parse_file(str(FIXTURES / "sample.js"))
        assert result is not None
        funcs = [n for n in result.nodes if n.kind == NodeKind.FUNCTION]
        func_names = {f.name for f in funcs}
        assert "validateEmail" in func_names


class TestParseTypeScript:
    def test_parse_sample(self):
        result = parse_file(str(FIXTURES / "sample.ts"))
        assert result is not None
        assert result.language == "typescript"

    def test_class_and_interface_detected(self):
        result = parse_file(str(FIXTURES / "sample.ts"))
        assert result is not None
        classes = [n for n in result.nodes if n.kind == NodeKind.CLASS]
        names = {c.name for c in classes}
        # TypeScript interfaces and types are detected as classes
        assert "UserService" in names or "UserDTO" in names
