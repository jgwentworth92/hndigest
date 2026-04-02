"""Centralized path resolution for hndigest.

All config, migration, and data paths resolve from a single base
directory. The base is determined by (in priority order):

1. ``HNDIGEST_BASE_DIR`` environment variable (set in Docker)
2. The project root detected from this file's location (local dev)

Modules should import paths from here instead of computing them
from ``__file__`` — that breaks when the package is installed
into site-packages (e.g., in Docker).
"""

from __future__ import annotations

import os
from pathlib import Path

# In local dev, this file is at src/hndigest/paths.py → project root is parents[2]
# In Docker/installed, HNDIGEST_BASE_DIR env var overrides.
_FALLBACK_ROOT = Path(__file__).resolve().parents[2]

BASE_DIR: Path = Path(os.environ.get("HNDIGEST_BASE_DIR", str(_FALLBACK_ROOT)))

CONFIG_DIR: Path = BASE_DIR / "config"
MIGRATIONS_DIR: Path = BASE_DIR / "db" / "migrations"
OUTPUT_DIR: Path = BASE_DIR / "output"
ENV_FILE: Path = BASE_DIR / ".env"

SCORING_CONFIG: Path = CONFIG_DIR / "scoring.yaml"
ORCHESTRATOR_CONFIG: Path = CONFIG_DIR / "orchestrator.yaml"
LLM_CONFIG: Path = CONFIG_DIR / "llm.yaml"
PROMPTS_CONFIG: Path = CONFIG_DIR / "prompts.yaml"
CATEGORIES_CONFIG: Path = CONFIG_DIR / "categories.yaml"
