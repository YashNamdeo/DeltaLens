"""Tree-sitter based multi-language AST parser.

Extracts code nodes (functions, classes, types) and edges (calls, imports,
inheritance) from source files. Signatures are extracted separately from bodies
to enable the allocator to choose representation tiers.
"""

from __future__ import annotations

import hashlib
import logging
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import tree_sitter_javascript as tsjs
import tree_sitter_python as tspy
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from deltalens.models import CodeEdge, CodeNode, EdgeKind, NodeKind, ParseResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".php": "php",
}

_LANGUAGE_OBJECTS: dict[str, Language] = {}


def _get_language(lang_name: str) -> Language | None:
    """Get a Tree-sitter Language object, loading it lazily."""
    if lang_name in _LANGUAGE_OBJECTS:
        return _LANGUAGE_OBJECTS[lang_name]

    loader_map: dict[str, Any] = {
        "python": tspy,
        "javascript": tsjs,
        "typescript": tsts,
        "tsx": tsts,
    }

    loader = loader_map.get(lang_name)
    if loader is None:
        return None

    try:
        if lang_name == "tsx":
            lang = Language(loader.language_tsx())
        elif lang_name == "typescript":
            lang = Language(loader.language_typescript())
        else:
            lang = Language(loader.language())
        _LANGUAGE_OBJECTS[lang_name] = lang
        return lang
    except Exception:
        logger.warning("Failed to load tree-sitter language: %s", lang_name)
        return None


# ---------------------------------------------------------------------------
# Language-specific AST node types
# ---------------------------------------------------------------------------

_FUNCTION_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "decorated_definition"},
    "javascript": {
        "function_declaration",
        "arrow_function",
        "method_definition",
        "function",
    },
    "typescript": {
        "function_declaration",
        "arrow_function",
        "method_definition",
        "function",
    },
    "tsx": {
        "function_declaration",
        "arrow_function",
        "method_definition",
        "function",
    },
}

_CLASS_TYPES: dict[str, set[str]] = {
    "python": {"class_definition"},
    "javascript": {"class_declaration"},
    "typescript": {
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
    },
    "tsx": {
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
    },
}

_IMPORT_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
}

_CALL_TYPES: dict[str, set[str]] = {
    "python": {"call"},
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
    "tsx": {"call_expression"},
}


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _node_text(node: Node, source: bytes) -> str:
    """Extract text content of an AST node."""
    return source[node.start_byte : node.end_byte].decode(
        "utf-8", errors="replace"
    )


def _extract_name(node: Node, source: bytes, language: str) -> str:
    """Extract the name of a function/class/type node."""
    # Handle Python decorated definitions — drill into the actual definition
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return _extract_name(child, source, language)

    name_node = node.child_by_field_name("name")
    if name_node:
        return _node_text(name_node, source)

    # Fallback: for arrow functions assigned to variables
    # e.g. const foo = () => {}
    parent = node.parent
    if parent and parent.type in (
        "variable_declarator",
        "assignment_expression",
    ):
        name_child = parent.child_by_field_name("name")
        if name_child:
            return _node_text(name_child, source)

    return "<anonymous>"


def _extract_signature(node: Node, source: bytes, language: str) -> str:
    """Extract function/method signature (without body)."""
    if node.type == "decorated_definition":
        parts = []
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                parts.append(_extract_signature(child, source, language))
            elif child.type == "decorator":
                parts.append(_node_text(child, source))
        return "\n".join(parts)

    if language == "python":
        # Everything up to the colon before the body
        params = node.child_by_field_name("parameters")
        return_type = node.child_by_field_name("return_type")
        name = _extract_name(node, source, language)

        sig = f"def {name}"
        if params:
            sig += _node_text(params, source)
        if return_type:
            sig += f" -> {_node_text(return_type, source)}"
        return sig

    # JS/TS: everything before the body block
    body = node.child_by_field_name("body")
    if body:
        sig_bytes = source[node.start_byte : body.start_byte].rstrip()
        return sig_bytes.decode("utf-8", errors="replace").strip()

    return _node_text(node, source)[:200]


def _extract_docstring(node: Node, source: bytes, language: str) -> str:
    """Extract docstring from a function/class node."""
    target = node
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                target = child
                break

    body = target.child_by_field_name("body")
    if not body:
        return ""

    if language == "python":
        # First expression_statement containing a string
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string":
                        text = _node_text(sub, source)
                        return text.strip("\"'").strip()
            break  # only check first statement

    # JS/TS: look for leading comment
    prev = node.prev_sibling
    if prev and prev.type == "comment":
        return _node_text(prev, source).strip("/* \n").strip()

    return ""


def _is_test_function(name: str, file_path: str) -> bool:
    """Heuristic: is this a test function?"""
    p = Path(file_path)
    in_test_file = (
        p.name.startswith("test_")
        or p.name.endswith("_test.py")
        or "tests/" in str(p)
        or "test/" in str(p)
        or p.name.endswith(".test.js")
        or p.name.endswith(".test.ts")
        or p.name.endswith(".spec.js")
        or p.name.endswith(".spec.ts")
    )
    return in_test_file or name.startswith("test_") or name.startswith("test")


def _make_node_id(file_path: str, kind: str, name: str, line: int) -> str:
    """Create a stable unique ID for a code node."""
    return f"{file_path}::{kind}::{name}::{line}"


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


def _extract_imports(
    tree_root: Node, source: bytes, language: str, file_path: str
) -> list[CodeEdge]:
    """Extract import edges from AST."""
    edges: list[CodeEdge] = []
    import_types = _IMPORT_TYPES.get(language, set())
    file_id = f"{file_path}::file::{Path(file_path).name}::0"

    def _walk(node: Node) -> None:
        if node.type in import_types:
            text = _node_text(node, source)
            # Extract module path from import text
            module = _resolve_import_module(text, language)
            if module:
                target_id = f"{module}::module::{module}::0"
                edges.append(
                    CodeEdge(
                        source_id=file_id,
                        target_id=target_id,
                        kind=EdgeKind.IMPORTS_FROM,
                    )
                )
        for child in node.children:
            _walk(child)

    _walk(tree_root)
    return edges


def _resolve_import_module(import_text: str, language: str) -> str:
    """Resolve an import statement to a module path."""
    if language == "python":
        # from foo.bar import baz → foo.bar
        # import foo.bar → foo.bar
        parts = import_text.split()
        if "from" in parts:
            idx = parts.index("from")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        elif "import" in parts:
            idx = parts.index("import")
            if idx + 1 < len(parts):
                return parts[idx + 1].rstrip(",")
    else:
        # JS/TS: import ... from 'module'
        if "from" in import_text:
            # Extract the string after 'from'
            after_from = (
                import_text.split("from")[-1].strip().strip(";").strip()
            )
            return after_from.strip("'\"")
    return ""


# ---------------------------------------------------------------------------
# Call extraction
# ---------------------------------------------------------------------------


def _extract_calls(
    func_node: Node, source: bytes, language: str, caller_id: str
) -> list[CodeEdge]:
    """Extract function call edges from within a function body."""
    edges: list[CodeEdge] = []
    call_types = _CALL_TYPES.get(language, set())

    def _walk(node: Node) -> None:
        if node.type in call_types:
            callee_name = _extract_callee_name(node, source)
            if callee_name and callee_name not in (
                "print",
                "len",
                "str",
                "int",
                "float",
                "bool",
            ):
                target_id = f"?::{callee_name}"  # unresolved, linked later
                edges.append(
                    CodeEdge(
                        source_id=caller_id,
                        target_id=target_id,
                        kind=EdgeKind.CALLS,
                    )
                )
        for child in node.children:
            _walk(child)

    body = func_node.child_by_field_name("body")
    target = body if body else func_node
    _walk(target)
    return edges


def _extract_callee_name(node: Node, source: bytes) -> str:
    """Extract the name of the called function."""
    func = node.child_by_field_name("function")
    if not func:
        # Some languages use first child
        if node.children:
            func = node.children[0]
        else:
            return ""

    text = _node_text(func, source)
    # Handle method calls: obj.method → method
    if "." in text:
        return text.split(".")[-1]
    return text


# ---------------------------------------------------------------------------
# Inheritance extraction
# ---------------------------------------------------------------------------


def _extract_bases(
    class_node: Node, source: bytes, language: str, class_id: str
) -> list[CodeEdge]:
    """Extract inheritance/implements edges from a class definition."""
    edges: list[CodeEdge] = []

    if language == "python":
        # class Foo(Bar, Baz): → inherits Bar, Baz
        superclasses = class_node.child_by_field_name("superclasses")
        if superclasses:
            for child in superclasses.children:
                if child.type not in ("(", ")", ","):
                    base_name = _node_text(child, source).strip()
                    if base_name:
                        target_id = f"?::{base_name}"
                        edges.append(
                            CodeEdge(
                                source_id=class_id,
                                target_id=target_id,
                                kind=EdgeKind.INHERITS,
                            )
                        )
    else:
        # JS/TS: class Foo extends Bar implements Baz
        heritage = class_node.child_by_field_name("heritage")
        if not heritage:
            # Walk children to find extends/implements clauses
            for child in class_node.children:
                if "heritage" in child.type or "extends" in child.type:
                    heritage = child
                    break

        if heritage:
            text = _node_text(heritage, source)
            # Simple extraction of class names
            for token in text.replace(",", " ").split():
                if token not in ("extends", "implements", "{"):
                    target_id = f"?::{token}"
                    kind = (
                        EdgeKind.IMPLEMENTS
                        if "implements" in text
                        else EdgeKind.INHERITS
                    )
                    edges.append(
                        CodeEdge(
                            source_id=class_id,
                            target_id=target_id,
                            kind=kind,
                        )
                    )

    return edges


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------


def detect_language(file_path: str) -> str | None:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    return EXTENSION_TO_LANGUAGE.get(ext)


def parse_file(
    file_path: str, source: bytes | None = None
) -> ParseResult | None:
    """Parse a single source file and extract nodes + edges.

    Args:
        file_path: Path to the source file.
        source: File contents. If None, reads from disk.

    Returns:
        ParseResult with nodes and edges, or None if parsing fails.
    """
    language = detect_language(file_path)
    if not language:
        return None

    lang_obj = _get_language(language)
    if not lang_obj:
        logger.debug(
            "No tree-sitter grammar for %s, skipping %s", language, file_path
        )
        return None

    if source is None:
        try:
            source = Path(file_path).read_bytes()
        except OSError:
            logger.warning("Cannot read file: %s", file_path)
            return None

    sha = hashlib.sha256(source).hexdigest()

    parser = Parser(lang_obj)
    tree = parser.parse(source)

    nodes: list[CodeNode] = []
    edges: list[CodeEdge] = []

    # File node
    file_name = Path(file_path).name
    file_id = _make_node_id(file_path, "file", file_name, 0)
    nodes.append(
        CodeNode(
            id=file_id,
            kind=NodeKind.FILE,
            name=file_name,
            qualified_name=file_path,
            file_path=file_path,
            language=language,
            sha256=sha,
        )
    )

    # Extract imports
    edges.extend(_extract_imports(tree.root_node, source, language, file_path))

    # Walk top-level and nested definitions
    _extract_definitions(
        tree.root_node, source, language, file_path, file_id, nodes, edges
    )

    return ParseResult(
        file_path=file_path,
        language=language,
        nodes=nodes,
        edges=edges,
        sha256=sha,
    )


def _extract_definitions(
    root: Node,
    source: bytes,
    language: str,
    file_path: str,
    parent_id: str,
    nodes: list[CodeNode],
    edges: list[CodeEdge],
    class_name: str = "",
) -> None:
    """Recursively extract function/class definitions from an AST subtree."""
    func_types = _FUNCTION_TYPES.get(language, set())
    class_types = _CLASS_TYPES.get(language, set())

    for child in root.children:
        actual = child
        # Handle decorated definitions in Python
        if child.type == "decorated_definition":
            for sub in child.children:
                if sub.type in func_types or sub.type in class_types:
                    actual = sub
                    break
            else:
                actual = child

        if actual.type in func_types or child.type in func_types:
            target = child  # use decorated if available
            name = _extract_name(target, source, language)
            qualified = f"{class_name}.{name}" if class_name else name
            node_id = _make_node_id(
                file_path, "function", name, target.start_point[0]
            )

            sig = _extract_signature(target, source, language)
            doc = _extract_docstring(target, source, language)
            body_text = _node_text(target, source)

            fn_node = CodeNode(
                id=node_id,
                kind=NodeKind.FUNCTION,
                name=name,
                qualified_name=qualified,
                file_path=file_path,
                language=language,
                line_start=target.start_point[0] + 1,
                line_end=target.end_point[0] + 1,
                signature=sig,
                docstring=doc,
                body=body_text,
                is_test=_is_test_function(name, file_path),
            )
            nodes.append(fn_node)
            edges.append(
                CodeEdge(
                    source_id=parent_id,
                    target_id=node_id,
                    kind=EdgeKind.CONTAINS,
                )
            )

            # Extract call edges from this function
            edges.extend(_extract_calls(target, source, language, node_id))

        elif actual.type in class_types or child.type in class_types:
            target = child
            name = _extract_name(target, source, language)
            node_id = _make_node_id(
                file_path, "class", name, target.start_point[0]
            )

            cls_node = CodeNode(
                id=node_id,
                kind=NodeKind.CLASS,
                name=name,
                qualified_name=name,
                file_path=file_path,
                language=language,
                line_start=target.start_point[0] + 1,
                line_end=target.end_point[0] + 1,
                signature=_extract_signature(target, source, language),
                docstring=_extract_docstring(target, source, language),
                body=_node_text(target, source),
            )
            nodes.append(cls_node)
            edges.append(
                CodeEdge(
                    source_id=parent_id,
                    target_id=node_id,
                    kind=EdgeKind.CONTAINS,
                )
            )

            # Extract base class edges
            edges.extend(_extract_bases(actual, source, language, node_id))

            # Recurse into class body for methods
            body = actual.child_by_field_name("body")
            if body:
                _extract_definitions(
                    body,
                    source,
                    language,
                    file_path,
                    node_id,
                    nodes,
                    edges,
                    class_name=name,
                )

        # Handle variable declarations with arrow functions (JS/TS)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            for decl in child.children:
                if decl.type == "variable_declarator":
                    value = decl.child_by_field_name("value")
                    if value and value.type in func_types:
                        name = _extract_name(value, source, language)
                        node_id = _make_node_id(
                            file_path, "function", name, child.start_point[0]
                        )
                        fn_node = CodeNode(
                            id=node_id,
                            kind=NodeKind.FUNCTION,
                            name=name,
                            qualified_name=name,
                            file_path=file_path,
                            language=language,
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            signature=_extract_signature(
                                value, source, language
                            ),
                            body=_node_text(child, source),
                        )
                        nodes.append(fn_node)
                        edges.append(
                            CodeEdge(
                                source_id=parent_id,
                                target_id=node_id,
                                kind=EdgeKind.CONTAINS,
                            )
                        )
                        edges.extend(
                            _extract_calls(value, source, language, node_id)
                        )


def parse_directory(
    root: Path, ignore_patterns: list[str] | None = None
) -> list[ParseResult]:
    """Parse all supported files in a directory tree.

    Args:
        root: Root directory to scan.
        ignore_patterns: Glob patterns to skip.

    Returns:
        List of ParseResult for each successfully parsed file.
    """

    ignore = ignore_patterns or []
    results: list[ParseResult] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if any(
            fnmatch(rel, pat) or fnmatch(rel, f"**/{pat}") for pat in ignore
        ):
            continue
        if detect_language(str(path)) is None:
            continue

        result = parse_file(str(path))
        if result:
            results.append(result)

    return results
