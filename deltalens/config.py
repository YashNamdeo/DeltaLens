"""Configuration management for DeltaLens."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TOKEN_BUDGET = 8000
DEFAULT_IMPACT_THRESHOLD = 0.3
DEFAULT_DISTANCE_DECAY = 0.6
DEFAULT_IGNORE_PATTERNS = [
    "node_modules/",
    ".git/",
    "__pycache__/",
    "*.pyc",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
    ".deltalens/",
]


@dataclass
class DeltaLensConfig:
    """Runtime configuration for DeltaLens."""

    repo_root: Path = field(default_factory=Path.cwd)
    token_budget: int = DEFAULT_TOKEN_BUDGET
    impact_threshold: float = DEFAULT_IMPACT_THRESHOLD
    distance_decay: float = DEFAULT_DISTANCE_DECAY
    ignore_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS)
    )
    languages: list[str] = field(default_factory=list)  # empty = auto-detect
    db_path: Path | None = None
    enable_embeddings: bool = False

    def __post_init__(self) -> None:
        if self.db_path is None:
            self.db_path = self.repo_root / ".deltalens" / "graph.db"

    @classmethod
    def load(cls, repo_root: Path | None = None) -> DeltaLensConfig:
        """Load config from .deltalens.toml if present, else defaults."""
        root = repo_root or Path.cwd()
        config_path = root / ".deltalens.toml"
        overrides: dict[str, Any] = {}

        if config_path.exists():
            with open(config_path, "rb") as f:
                raw = tomllib.load(f)
            overrides = raw.get("deltalens", raw)

        return cls(
            repo_root=root,
            token_budget=overrides.get("token_budget", DEFAULT_TOKEN_BUDGET),
            impact_threshold=overrides.get(
                "impact_threshold", DEFAULT_IMPACT_THRESHOLD
            ),
            distance_decay=overrides.get(
                "distance_decay", DEFAULT_DISTANCE_DECAY
            ),
            ignore_patterns=overrides.get(
                "ignore_patterns", list(DEFAULT_IGNORE_PATTERNS)
            ),
            languages=overrides.get("languages", []),
            enable_embeddings=overrides.get("enable_embeddings", False),
        )
