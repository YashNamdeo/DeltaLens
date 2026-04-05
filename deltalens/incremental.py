"""Incremental update engine — only re-parse changed files.

Uses SHA-256 hashing to detect changes and git diff integration
to trigger partial re-parsing.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from deltalens.parser import detect_language, parse_file

if TYPE_CHECKING:
    from deltalens.graph import GraphStore

logger = logging.getLogger(__name__)


def file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_changed_files_git(repo_root: Path, ref: str = "HEAD") -> list[str]:
    """Get list of changed files using git diff.

    Args:
        repo_root: Root of the git repository.
        ref: Git ref to diff against (default HEAD for uncommitted changes).

    Returns:
        List of file paths relative to repo_root.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", ref],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=30,
        )
        if result.returncode != 0:
            # Try staged changes
            result = subprocess.run(
                ["git", "diff", "--name-only", "--cached"],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=30,
            )

        files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
        return files
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("git not available or timed out")
        return []


def get_git_diff_text(repo_root: Path, ref: str = "HEAD") -> str:
    """Get the full unified diff text from git."""
    try:
        result = subprocess.run(
            ["git", "diff", ref],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def detect_changed_files(
    repo_root: Path, store: GraphStore, ignore_patterns: list[str] | None = None
) -> list[str]:
    """Detect files that have changed since last index by comparing SHA-256 hashes.

    Args:
        repo_root: Root directory to scan.
        store: Graph store with cached file hashes.
        ignore_patterns: Glob patterns to skip.

    Returns:
        List of file paths that need re-parsing.
    """

    ignore = ignore_patterns or []
    changed: list[str] = []

    indexed_files = set(store.get_all_file_paths())
    current_files: set[str] = set()

    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo_root))
        if any(
            fnmatch(rel, pat) or fnmatch(rel, f"**/{pat}") for pat in ignore
        ):
            continue
        if detect_language(str(path)) is None:
            continue

        file_path = str(path)
        current_files.add(file_path)

        current_hash = file_sha256(path)
        stored_hash = store.get_file_hash(file_path)

        if stored_hash != current_hash:
            changed.append(file_path)

    # Detect deleted files
    deleted = indexed_files - current_files
    for f in deleted:
        store.remove_file(f)
        logger.info("Removed deleted file from index: %s", f)

    return changed


def incremental_update(
    repo_root: Path,
    store: GraphStore,
    changed_files: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
) -> dict[str, int]:
    """Incrementally update the graph with only changed files.

    Args:
        repo_root: Root directory.
        store: Graph store to update.
        changed_files: Explicit list of changed files. If None, auto-detect.
        ignore_patterns: Glob patterns to skip.

    Returns:
        Stats dict with counts of updated, added, removed files.
    """
    if changed_files is None:
        changed_files = detect_changed_files(repo_root, store, ignore_patterns)

    stats = {"updated": 0, "failed": 0}

    for file_path in changed_files:
        path = Path(file_path)
        if not path.exists():
            store.remove_file(file_path)
            continue

        result = parse_file(file_path)
        if result:
            store.ingest_parse_result(result)
            stats["updated"] += 1
            logger.info("Updated: %s", file_path)
        else:
            stats["failed"] += 1
            logger.warning("Failed to parse: %s", file_path)

    # Resolve unresolved call edges after re-parsing
    resolved = store.resolve_unresolved_edges()
    stats["edges_resolved"] = resolved

    return stats
