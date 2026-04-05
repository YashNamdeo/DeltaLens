"""File watcher — triggers incremental updates on file changes.

Uses watchdog for real-time filesystem monitoring.
"""

from __future__ import annotations

import logging
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from deltalens.parser import detect_language

if TYPE_CHECKING:
    from deltalens.config import DeltaLensConfig

logger = logging.getLogger(__name__)


class DeltaLensHandler(FileSystemEventHandler):
    """Handle file change events and trigger incremental updates."""

    def __init__(self, repo_root: Path, config: DeltaLensConfig) -> None:
        self.repo_root = repo_root
        self.config = config
        self._pending: set[str] = set()
        self._last_flush = time.time()
        self._debounce_seconds = 2.0

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return

        path = str(event.src_path)
        if detect_language(path) is None:
            return

        # Check ignore patterns
        rel = str(Path(path).relative_to(self.repo_root))
        if any(fnmatch(rel, pat) for pat in self.config.ignore_patterns):
            return

        self._pending.add(path)
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        """Debounced flush of pending changes."""
        now = time.time()
        if now - self._last_flush < self._debounce_seconds:
            return

        if self._pending:
            self._flush()

    def _flush(self) -> None:
        """Process all pending file changes."""
        from deltalens.graph import GraphStore
        from deltalens.incremental import incremental_update

        files = list(self._pending)
        self._pending.clear()
        self._last_flush = time.time()

        logger.info("Processing %d changed files", len(files))

        assert self.config.db_path is not None
        store = GraphStore(self.config.db_path)
        try:
            stats = incremental_update(
                self.repo_root, store, changed_files=files
            )
            logger.info("Update: %s", stats)
        finally:
            store.close()


def start_watcher(repo_root: Path, config: DeltaLensConfig) -> None:
    """Start watching a directory for changes. Blocks until Ctrl+C."""
    handler = DeltaLensHandler(repo_root, config)
    observer = Observer()
    observer.schedule(handler, str(repo_root), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
            handler._maybe_flush()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

    # Flush any remaining
    if handler._pending:
        handler._flush()
