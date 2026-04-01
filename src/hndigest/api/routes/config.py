"""Configuration endpoint: current system configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])

# Config directory relative to project root.
_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"

# Config files to load and expose.
_CONFIG_FILES = ("scoring.yaml", "orchestrator.yaml", "llm.yaml", "categories.yaml")


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Return the current system configuration from YAML config files.

    Reads each known config file (scoring, orchestrator, llm, categories)
    and returns their contents as a single dict keyed by config name.
    Missing files are silently skipped.

    Returns:
        Dict mapping config name (without extension) to its parsed YAML contents.

    Raises:
        HTTPException: 500 if config files cannot be read.
    """
    try:
        result: dict[str, Any] = {}
        for filename in _CONFIG_FILES:
            filepath = _CONFIG_DIR / filename
            if filepath.exists():
                with open(filepath, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                key = filename.removesuffix(".yaml")
                result[key] = data if data is not None else {}
        return result
    except Exception as exc:
        logger.exception("Failed to read configuration files")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
