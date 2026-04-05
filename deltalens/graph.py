"""SQLite-backed graph store with BFS traversal and FTS5 search.

No business logic — pure storage and retrieval.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

import networkx as nx  # type: ignore[import-untyped]

from deltalens.models import CodeEdge, CodeNode, EdgeKind, NodeKind, ParseResult


class GraphStore:
    """SQLite-backed code graph with BFS traversal and full-text search."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._nx: nx.DiGraph | None = None

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                language TEXT DEFAULT '',
                line_start INTEGER DEFAULT 0,
                line_end INTEGER DEFAULT 0,
                signature TEXT DEFAULT '',
                docstring TEXT DEFAULT '',
                body TEXT DEFAULT '',
                sha256 TEXT DEFAULT '',
                is_test INTEGER DEFAULT 0,
                is_entry_point INTEGER DEFAULT 0,
                modifiers TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id, kind)
            );

            CREATE TABLE IF NOT EXISTS file_hashes (
                file_path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                last_parsed_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS change_history (
                node_id TEXT NOT NULL,
                commit_sha TEXT,
                changed_at INTEGER,
                change_kind TEXT,
                risk_flag INTEGER DEFAULT 0,
                reviewer TEXT,
                PRIMARY KEY (node_id, commit_sha)
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
        """
        )

        # FTS5 for full-text search (standalone, not content-linked)
        with contextlib.suppress(sqlite3.OperationalError):
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts
                USING fts5(node_id, name, qualified_name, signature, docstring)
            """
            )

        self.conn.commit()

    def _invalidate_nx(self) -> None:
        self._nx = None

    def _get_nx(self) -> nx.DiGraph:
        """Lazily build a NetworkX graph from the SQLite store."""
        if self._nx is not None:
            return self._nx

        g = nx.DiGraph()
        for row in self.conn.execute(
            "SELECT id, kind, name, file_path FROM nodes"
        ):
            g.add_node(
                row["id"],
                kind=row["kind"],
                name=row["name"],
                file_path=row["file_path"],
            )
        for row in self.conn.execute(
            "SELECT source_id, target_id, kind FROM edges"
        ):
            g.add_edge(row["source_id"], row["target_id"], kind=row["kind"])

        self._nx = g
        return g

    # -----------------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------------

    def upsert_node(self, node: CodeNode) -> None:
        """Insert or update a node."""
        self.conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, kind, name, qualified_name, file_path, language,
                line_start, line_end, signature, docstring, body, sha256,
                is_test, is_entry_point, modifiers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.id,
                node.kind.value,
                node.name,
                node.qualified_name,
                node.file_path,
                node.language,
                node.line_start,
                node.line_end,
                node.signature,
                node.docstring,
                node.body,
                node.sha256,
                int(node.is_test),
                int(node.is_entry_point),
                json.dumps(node.modifiers),
            ),
        )
        self._invalidate_nx()

    def upsert_edge(self, edge: CodeEdge) -> None:
        """Insert or update an edge."""
        self.conn.execute(
            "INSERT OR REPLACE INTO edges (source_id, target_id, kind) VALUES (?, ?, ?)",
            (edge.source_id, edge.target_id, edge.kind.value),
        )
        self._invalidate_nx()

    def ingest_parse_result(self, result: ParseResult) -> None:
        """Ingest a full parse result: remove old data for the file, insert new."""
        # Remove old nodes and edges for this file
        self.remove_file(result.file_path)

        # Insert new nodes and edges
        for node in result.nodes:
            self.upsert_node(node)
        for edge in result.edges:
            self.upsert_edge(edge)

        # Update file hash
        self.conn.execute(
            "INSERT OR REPLACE INTO file_hashes (file_path, sha256) VALUES (?, ?)",
            (result.file_path, result.sha256),
        )
        self.conn.commit()
        self._invalidate_nx()

    def remove_file(self, file_path: str) -> None:
        """Remove all nodes and edges associated with a file."""
        node_ids = [
            row[0]
            for row in self.conn.execute(
                "SELECT id FROM nodes WHERE file_path = ?", (file_path,)
            )
        ]
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            self.conn.execute(
                f"DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                node_ids + node_ids,
            )
            self.conn.execute(
                f"DELETE FROM nodes WHERE id IN ({placeholders})", node_ids
            )
        self.conn.execute(
            "DELETE FROM file_hashes WHERE file_path = ?", (file_path,)
        )
        self.conn.commit()
        self._invalidate_nx()

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    def get_node(self, node_id: str) -> CodeNode | None:
        """Retrieve a single node by ID."""
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_node(row)

    def get_nodes_for_file(self, file_path: str) -> list[CodeNode]:
        """Get all nodes belonging to a file."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_file_hash(self, file_path: str) -> str | None:
        """Get the stored SHA-256 hash for a file."""
        row = self.conn.execute(
            "SELECT sha256 FROM file_hashes WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row["sha256"] if row else None

    def get_all_file_paths(self) -> list[str]:
        """Get all indexed file paths."""
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT file_path FROM nodes WHERE kind = 'file'"
            )
        ]

    def get_direct_callers(self, node_id: str) -> list[CodeNode]:
        """Get nodes that directly call the given node."""
        g = self._get_nx()
        callers = []
        for pred in g.predecessors(node_id):
            edge_data = g.edges[pred, node_id]
            if edge_data.get("kind") in (EdgeKind.CALLS.value, "calls"):
                node = self.get_node(pred)
                if node:
                    callers.append(node)
        return callers

    def get_dependents(self, node_id: str) -> list[CodeNode]:
        """Get all nodes that depend on the given node (reverse edges)."""
        g = self._get_nx()
        deps = []
        for pred in g.predecessors(node_id):
            node = self.get_node(pred)
            if node:
                deps.append(node)
        return deps

    def bfs_impacted(
        self, seed_ids: list[str], max_depth: int = 4
    ) -> list[tuple[str, int]]:
        """BFS from seed nodes, returning (node_id, depth) pairs.

        Traverses reverse edges (who depends on the changed nodes).
        """
        g = self._get_nx()
        visited: dict[str, int] = {}
        frontier = [(nid, 0) for nid in seed_ids]

        while frontier:
            node_id, depth = frontier.pop(0)
            if node_id in visited or depth > max_depth:
                continue
            visited[node_id] = depth

            # Reverse traversal: who imports/calls this node?
            for pred in g.predecessors(node_id):
                if pred not in visited:
                    frontier.append((pred, depth + 1))

        return list(visited.items())

    def resolve_unresolved_edges(self) -> int:
        """Resolve edges with unresolved targets (prefixed with '?::').

        Matches callee names to actual nodes in the graph.
        Returns the number of edges resolved.
        """
        unresolved = self.conn.execute(
            "SELECT rowid, source_id, target_id, kind FROM edges WHERE target_id LIKE '?::%'"
        ).fetchall()

        resolved_count = 0
        for row in unresolved:
            callee_name = row["target_id"][3:]  # strip '?::'

            # Find matching node by name
            matches = self.conn.execute(
                "SELECT id FROM nodes WHERE name = ? AND kind IN ('function', 'class')",
                (callee_name,),
            ).fetchall()

            if len(matches) == 1:
                self.conn.execute(
                    "UPDATE edges SET target_id = ? WHERE rowid = ?",
                    (matches[0]["id"], row["rowid"]),
                )
                resolved_count += 1
            elif len(matches) > 1:
                # Prefer same-file matches
                source_node = self.get_node(row["source_id"])
                if source_node:
                    same_file = [
                        m
                        for m in matches
                        if self.get_node(m["id"])
                        and self.get_node(m["id"]).file_path == source_node.file_path  # type: ignore[union-attr]
                    ]
                    if same_file:
                        self.conn.execute(
                            "UPDATE edges SET target_id = ? WHERE rowid = ?",
                            (same_file[0]["id"], row["rowid"]),
                        )
                        resolved_count += 1

        self.conn.commit()
        self._invalidate_nx()
        return resolved_count

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    def rebuild_fts(self) -> None:
        """Rebuild the FTS5 index from the nodes table."""
        try:
            self.conn.execute("DELETE FROM nodes_fts")
            self.conn.execute(
                """
                INSERT INTO nodes_fts(node_id, name, qualified_name, signature, docstring)
                SELECT id, name, qualified_name, signature, docstring FROM nodes
            """
            )
            self.conn.commit()
        except sqlite3.OperationalError:
            pass

    def search_fts(self, query: str, limit: int = 20) -> list[CodeNode]:
        """Full-text search using FTS5 BM25 ranking."""
        try:
            self.rebuild_fts()
            rows = self.conn.execute(
                """SELECT node_id FROM nodes_fts
                   WHERE nodes_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
            results = []
            for row in rows:
                node = self.get_node(row[0])
                if node:
                    results.append(node)
            return results
        except sqlite3.OperationalError:
            # Fallback to LIKE search
            return self.search_like(query, limit)

    def search_like(self, query: str, limit: int = 20) -> list[CodeNode]:
        """Fallback LIKE-based search."""
        pattern = f"%{query}%"
        rows = self.conn.execute(
            """SELECT * FROM nodes
               WHERE name LIKE ? OR qualified_name LIKE ? OR signature LIKE ?
               LIMIT ?""",
            (pattern, pattern, pattern, limit),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    # -----------------------------------------------------------------------
    # Stats
    # -----------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Get graph statistics."""
        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[
            0
        ]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[
            0
        ]
        file_count = self.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'file'"
        ).fetchone()[0]
        func_count = self.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'function'"
        ).fetchone()[0]
        class_count = self.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'class'"
        ).fetchone()[0]
        return {
            "nodes": node_count,
            "edges": edge_count,
            "files": file_count,
            "functions": func_count,
            "classes": class_count,
        }

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> CodeNode:
        return CodeNode(
            id=row["id"],
            kind=NodeKind(row["kind"]),
            name=row["name"],
            qualified_name=row["qualified_name"],
            file_path=row["file_path"],
            language=row["language"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            signature=row["signature"],
            docstring=row["docstring"],
            body=row["body"],
            sha256=row["sha256"],
            is_test=bool(row["is_test"]),
            is_entry_point=bool(row["is_entry_point"]),
            modifiers=json.loads(row["modifiers"]),
        )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
