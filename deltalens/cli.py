"""CLI entry point for DeltaLens.

No business logic — delegates to modules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import click

from deltalens import __version__
from deltalens.config import DeltaLensConfig


@click.group()
@click.version_option(version=__version__)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(verbose: bool) -> None:
    """DeltaLens — semantic-diff-aware code intelligence for AI assistants."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(name)s %(levelname)s: %(message)s",
    )


@main.command()
@click.argument("repo_root", default=".", type=click.Path(exists=True))
@click.option(
    "--ignore", "-i", multiple=True, help="Additional ignore patterns"
)
def init(repo_root: str, ignore: tuple[str, ...]) -> None:
    """Initialize DeltaLens for a repository. Creates .deltalens/ and builds initial graph."""
    root = Path(repo_root).resolve()
    dl_dir = root / ".deltalens"
    dl_dir.mkdir(exist_ok=True)

    click.echo(f"Initializing DeltaLens in {root}")

    cfg = DeltaLensConfig.load(root)
    if ignore:
        cfg.ignore_patterns.extend(ignore)

    from deltalens.graph import GraphStore
    from deltalens.parser import parse_directory

    assert cfg.db_path is not None
    store = GraphStore(cfg.db_path)

    results = parse_directory(root, cfg.ignore_patterns)
    for result in results:
        store.ingest_parse_result(result)

    resolved = store.resolve_unresolved_edges()
    stats = store.stats()

    click.echo(
        f"Graph built: {stats['files']} files, {stats['functions']} functions, "
        f"{stats['classes']} classes, {stats['edges']} edges ({resolved} resolved)"
    )
    store.close()


@main.command()
@click.argument("repo_root", default=".", type=click.Path(exists=True))
@click.option(
    "--ignore", "-i", multiple=True, help="Additional ignore patterns"
)
def build(repo_root: str, ignore: tuple[str, ...]) -> None:
    """Full graph rebuild."""
    root = Path(repo_root).resolve()
    cfg = DeltaLensConfig.load(root)
    if ignore:
        cfg.ignore_patterns.extend(ignore)

    from deltalens.graph import GraphStore
    from deltalens.parser import parse_directory

    assert cfg.db_path is not None
    store = GraphStore(cfg.db_path)

    click.echo("Building graph...")
    results = parse_directory(root, cfg.ignore_patterns)
    for result in results:
        store.ingest_parse_result(result)

    resolved = store.resolve_unresolved_edges()
    stats = store.stats()

    click.echo(
        f"Done: {stats['files']} files, {stats['functions']} functions, "
        f"{stats['classes']} classes, {stats['edges']} edges ({resolved} resolved)"
    )
    store.close()


@main.command()
@click.argument("repo_root", default=".", type=click.Path(exists=True))
def update(repo_root: str) -> None:
    """Incremental update — only re-parse changed files."""
    root = Path(repo_root).resolve()
    cfg = DeltaLensConfig.load(root)

    from deltalens.graph import GraphStore
    from deltalens.incremental import incremental_update as do_update

    assert cfg.db_path is not None
    store = GraphStore(cfg.db_path)
    stats = do_update(root, store, ignore_patterns=cfg.ignore_patterns)

    click.echo(
        f"Updated: {stats['updated']} files, {stats.get('edges_resolved', 0)} edges resolved"
    )
    store.close()


@main.command()
@click.argument("repo_root", default=".", type=click.Path(exists=True))
def status(repo_root: str) -> None:
    """Show graph statistics."""
    root = Path(repo_root).resolve()
    cfg = DeltaLensConfig.load(root)

    from deltalens.graph import GraphStore

    db_path = cfg.db_path
    assert db_path is not None
    if not db_path.exists():
        click.echo("No graph found. Run 'deltalens init' first.")
        return

    store = GraphStore(db_path)
    stats = store.stats()
    click.echo(json.dumps(stats, indent=2))
    store.close()


@main.command()
@click.argument("changed_files", nargs=-1, required=True)
@click.option("--budget", "-b", default=8000, help="Token budget")
@click.option("--repo-root", default=".", type=click.Path(exists=True))
def context(
    changed_files: tuple[str, ...], budget: int, repo_root: str
) -> None:
    """Show what context would be sent for given changed files."""
    root = Path(repo_root).resolve()
    cfg = DeltaLensConfig.load(root)

    from deltalens.allocator import allocate_budget, render_context
    from deltalens.classifier import classify_file_changes
    from deltalens.graph import GraphStore
    from deltalens.models import EdgeKind
    from deltalens.parser import parse_file
    from deltalens.scorer import (
        ScoringContext,
        filter_by_threshold,
        score_impacted_nodes,
    )

    assert cfg.db_path is not None
    store = GraphStore(cfg.db_path)

    all_changed_nodes = []
    change_kinds: dict[str, str] = {}

    for fpath in changed_files:
        old_nodes = store.get_nodes_for_file(str(Path(fpath).resolve()))
        new_result = parse_file(str(Path(fpath).resolve()))
        if not new_result:
            click.echo(f"Cannot parse: {fpath}", err=True)
            continue

        classified = classify_file_changes(old_nodes, new_result.nodes, fpath)
        for nc in classified.node_changes:
            change_kinds[nc.node_id] = nc.kind.value
        all_changed_nodes.extend(
            [n for n in new_result.nodes if n.id in change_kinds]
        )

    if not all_changed_nodes:
        click.echo("No changes detected.")
        store.close()
        return

    seed_ids = [n.id for n in all_changed_nodes]
    all_impl = all(k == "impl" for k in change_kinds.values())
    bfs_results = store.bfs_impacted(seed_ids, max_depth=1 if all_impl else 4)

    edge_lookup = {nid: EdgeKind.CALLS.value for nid, _ in bfs_results}
    node_lookup = {n.id: n for n in all_changed_nodes}
    for nid, _ in bfs_results:
        if nid not in node_lookup:
            node = store.get_node(nid)
            if node:
                node_lookup[nid] = node

    ctx = ScoringContext(
        distance_decay=cfg.distance_decay, impact_threshold=cfg.impact_threshold
    )
    scored = score_impacted_nodes(
        all_changed_nodes,
        change_kinds,
        bfs_results,
        edge_lookup,
        node_lookup,
        ctx,
    )
    scored = filter_by_threshold(scored, cfg.impact_threshold)
    payload = allocate_budget(scored, budget)

    click.echo(render_context(payload))
    store.close()


@main.command()
@click.argument("file_path", type=click.Path(exists=True))
def classify(file_path: str) -> None:
    """Classify changes in a file (interface vs implementation)."""
    root = Path(".").resolve()
    cfg = DeltaLensConfig.load(root)

    from deltalens.classifier import classify_file_changes
    from deltalens.graph import GraphStore
    from deltalens.parser import parse_file

    assert cfg.db_path is not None
    store = GraphStore(cfg.db_path)
    abs_path = str(Path(file_path).resolve())
    old_nodes = store.get_nodes_for_file(abs_path)
    new_result = parse_file(abs_path)

    if not new_result:
        click.echo(f"Cannot parse: {file_path}", err=True)
        return

    classified = classify_file_changes(old_nodes, new_result.nodes, abs_path)

    click.echo(f"File: {file_path}")
    click.echo(f"Trivial: {classified.is_trivial}")
    for nc in classified.node_changes:
        click.echo(f"  {nc.kind.value:10s}  {nc.node_id}")
        if (
            nc.old_signature
            and nc.new_signature
            and nc.old_signature != nc.new_signature
        ):
            click.echo(f"    old: {nc.old_signature}")
            click.echo(f"    new: {nc.new_signature}")

    store.close()


@main.command()
@click.argument("query")
@click.option("--limit", "-k", default=10, help="Max results")
@click.option("--repo-root", default=".", type=click.Path(exists=True))
def search(query: str, limit: int, repo_root: str) -> None:
    """Search for code nodes."""
    root = Path(repo_root).resolve()
    cfg = DeltaLensConfig.load(root)

    from deltalens.graph import GraphStore

    assert cfg.db_path is not None
    store = GraphStore(cfg.db_path)
    results = store.search_fts(query, limit=limit)

    for node in results:
        click.echo(
            f"  {node.kind.value:8s}  {node.name:30s}  {node.file_path}:{node.line_start}"
        )

    store.close()


@main.command()
def serve() -> None:
    """Start the MCP server on stdio."""
    from deltalens.server import run_server

    asyncio.run(run_server())


@main.command()
def install() -> None:
    """Auto-detect AI tools and configure MCP integration."""
    home = Path.home()
    configured: list[str] = []

    # Claude Code
    claude_config = home / ".claude" / "claude_desktop_config.json"
    _install_mcp_config(claude_config, "claude_code", configured)

    # Claude Desktop
    claude_desktop = (
        home
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json"
    )
    _install_mcp_config(claude_desktop, "claude_desktop", configured)

    # Cursor
    cursor_config = home / ".cursor" / "mcp.json"
    _install_mcp_config(cursor_config, "cursor", configured)

    if configured:
        click.echo(
            f"Configured DeltaLens MCP server for: {', '.join(configured)}"
        )
    else:
        click.echo(
            "No supported AI tools detected. You can manually configure the MCP server."
        )
        click.echo("Run: deltalens serve")


def _install_mcp_config(
    config_path: Path, tool_name: str, configured: list[str]
) -> None:
    """Add DeltaLens to an MCP configuration file."""
    deltalens_entry = {
        "command": sys.executable,
        "args": ["-m", "deltalens.cli", "serve"],
    }

    try:
        if config_path.exists():
            data = json.loads(config_path.read_text())
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}

        if "mcpServers" not in data:
            data["mcpServers"] = {}

        data["mcpServers"]["deltalens"] = deltalens_entry
        config_path.write_text(json.dumps(data, indent=2))
        configured.append(tool_name)
    except Exception as e:
        logging.warning("Failed to configure %s: %s", tool_name, e)


@main.command()
@click.argument("repo_root", default=".", type=click.Path(exists=True))
def watch(repo_root: str) -> None:
    """Watch for file changes and update graph in real-time."""
    root = Path(repo_root).resolve()
    cfg = DeltaLensConfig.load(root)

    from deltalens.hooks.file_watcher import start_watcher

    click.echo(f"Watching {root} for changes... (Ctrl+C to stop)")
    start_watcher(root, cfg)


if __name__ == "__main__":
    main()
