"""Shared data models for DeltaLens."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NodeKind(StrEnum):
    """Types of nodes in the code graph."""

    FILE = "file"
    FUNCTION = "function"
    CLASS = "class"
    TYPE = "type"
    MODULE = "module"


class EdgeKind(StrEnum):
    """Types of edges in the code graph."""

    CALLS = "calls"
    IMPORTS_FROM = "imports_from"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CONTAINS = "contains"
    TESTED_BY = "tested_by"
    OVERRIDES = "overrides"


class ChangeKind(StrEnum):
    """Classification of how a node changed."""

    INTERFACE = "interface"  # signature, decorators, exports changed
    IMPL = "impl"  # body only changed
    ADDED = "added"
    DELETED = "deleted"


class RepresentationTier(StrEnum):
    """How a node should be represented in the context payload."""

    FULL = "full"  # full source with diff markers
    SIGNATURE = "signature"  # signatures + docstrings + type hints
    SUMMARY = "summary"  # one-line structural summary
    NAME_ONLY = "name_only"  # excluded from prompt


@dataclass
class CodeNode:
    """A node in the code graph (function, class, file, etc.)."""

    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    file_path: str
    language: str = ""
    line_start: int = 0
    line_end: int = 0
    signature: str = ""
    docstring: str = ""
    body: str = ""
    sha256: str = ""
    is_test: bool = False
    is_entry_point: bool = False
    modifiers: list[str] = field(default_factory=list)


@dataclass
class CodeEdge:
    """An edge in the code graph."""

    source_id: str
    target_id: str
    kind: EdgeKind


@dataclass
class NodeChange:
    """A change to a specific node, classified by type."""

    node_id: str
    kind: ChangeKind
    old_signature: str | None = None
    new_signature: str | None = None


@dataclass
class ScoredNode:
    """A node with its computed impact score."""

    node: CodeNode
    score: float
    reasoning: str = ""
    tier: RepresentationTier = RepresentationTier.NAME_ONLY


@dataclass
class ContextPayload:
    """The final context payload sent to the model."""

    changed_files: list[str]
    total_tokens: int
    budget: int
    nodes: list[ScoredNode]
    summary: str = ""


@dataclass
class ParseResult:
    """Result of parsing a single file."""

    file_path: str
    language: str
    nodes: list[CodeNode]
    edges: list[CodeEdge]
    sha256: str = ""
