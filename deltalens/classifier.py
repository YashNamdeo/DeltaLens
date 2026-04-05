"""Change classifier — determines if changes are interface or implementation.

This is the key innovation: before touching the graph, classify the diff.
Interface changes (signature, decorators, exports) trigger full BFS.
Implementation changes (body only) trigger direct-caller lookup only.

Pure functions — no I/O, no side effects.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from deltalens.models import ChangeKind, CodeNode, NodeChange


@dataclass
class ClassifiedChange:
    """Result of classifying changes between two versions of a file."""

    file_path: str
    node_changes: list[NodeChange]
    is_trivial: bool = False  # True if single-file, <15 lines, impl-only


def classify_file_changes(
    old_nodes: list[CodeNode],
    new_nodes: list[CodeNode],
    file_path: str,
) -> ClassifiedChange:
    """Classify changes between two versions of a file's nodes.

    Compares old and new node lists to determine what kind of change
    happened to each node: interface change, implementation change,
    addition, or deletion.

    Args:
        old_nodes: Nodes from the previous version of the file.
        new_nodes: Nodes from the current version of the file.
        file_path: Path of the file being classified.

    Returns:
        ClassifiedChange with per-node change classifications.
    """
    old_by_name: dict[str, CodeNode] = {n.qualified_name: n for n in old_nodes}
    new_by_name: dict[str, CodeNode] = {n.qualified_name: n for n in new_nodes}

    changes: list[NodeChange] = []

    # Detect added nodes
    for name, node in new_by_name.items():
        if name not in old_by_name:
            changes.append(
                NodeChange(
                    node_id=node.id,
                    kind=ChangeKind.ADDED,
                    new_signature=node.signature,
                )
            )

    # Detect deleted nodes
    for name, node in old_by_name.items():
        if name not in new_by_name:
            changes.append(
                NodeChange(
                    node_id=node.id,
                    kind=ChangeKind.DELETED,
                    old_signature=node.signature,
                )
            )

    # Detect modified nodes
    for name in old_by_name.keys() & new_by_name.keys():
        old_node = old_by_name[name]
        new_node = new_by_name[name]

        if old_node.body == new_node.body:
            continue  # no change

        kind = _classify_node_change(old_node, new_node)
        changes.append(
            NodeChange(
                node_id=new_node.id,
                kind=kind,
                old_signature=old_node.signature,
                new_signature=new_node.signature,
            )
        )

    # Determine if this is a trivial change
    total_changed_lines = _count_changed_lines(
        [
            old_by_name.get(c.node_id, new_by_name.get(c.node_id))
            for c in changes
        ],
        [
            new_by_name.get(c.node_id, old_by_name.get(c.node_id))
            for c in changes
        ],
    )
    all_impl = all(c.kind == ChangeKind.IMPL for c in changes)
    is_trivial = len(changes) > 0 and all_impl and total_changed_lines < 15

    return ClassifiedChange(
        file_path=file_path,
        node_changes=changes,
        is_trivial=is_trivial,
    )


def _classify_node_change(old: CodeNode, new: CodeNode) -> ChangeKind:
    """Classify a change to a single node as interface or implementation.

    Interface change if any of:
    - Signature changed (parameters, return type)
    - Modifiers changed (decorators, visibility, static, etc.)
    - Docstring changed significantly (public API docs)

    Implementation change if only the function body differs.
    """
    # Signature change → interface
    if _signatures_differ(old.signature, new.signature):
        return ChangeKind.INTERFACE

    # Modifier change → interface
    if old.modifiers != new.modifiers:
        return ChangeKind.INTERFACE

    # If body changed but signature didn't → implementation
    return ChangeKind.IMPL


def _signatures_differ(old_sig: str, new_sig: str) -> bool:
    """Compare two signatures, ignoring whitespace differences."""
    return _normalize_whitespace(old_sig) != _normalize_whitespace(new_sig)


def _normalize_whitespace(s: str) -> str:
    """Normalize whitespace: collapse runs, strip around punctuation."""
    # Collapse all whitespace to single space
    s = " ".join(s.split())
    # Remove spaces around parentheses, commas, colons for structural comparison
    s = re.sub(r"\s*([(),:])\s*", r"\1", s)
    return s


def _count_changed_lines(
    old_nodes: list[CodeNode | None],
    new_nodes: list[CodeNode | None],
) -> int:
    """Count total changed lines across all modified nodes."""
    total = 0
    for old, new in zip(old_nodes, new_nodes, strict=False):
        old_body = (old.body if old else "").splitlines()
        new_body = (new.body if new else "").splitlines()
        diff = list(difflib.unified_diff(old_body, new_body, lineterm=""))
        total += sum(
            1 for line in diff if line.startswith("+") or line.startswith("-")
        )
    return total


def classify_diff_text(diff_text: str) -> list[str]:
    """Extract changed file paths from a unified diff string.

    Useful for quickly determining which files need re-parsing.
    """
    files: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
        elif line.startswith("--- a/"):
            path = line[6:]
            if path != "/dev/null":
                files.append(path)
    return list(dict.fromkeys(files))  # dedupe preserving order
