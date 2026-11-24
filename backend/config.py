"""Configuration and workspace path resolution for NovelGenerator v5."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = Path(os.environ["NOVELGEN_FRONTEND_DIR"]).resolve() if os.environ.get("NOVELGEN_FRONTEND_DIR") else (PROJECT_ROOT / "frontend")


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    db_path: Path
    llm_config_path: Path
    host: str = "127.0.0.1"
    port: int = 8787

    @classmethod
    def from_env(cls, data_dir: str | Path | None = None) -> "AppConfig":
        raw_dir = data_dir or os.environ.get("NOVELGEN_DATA_DIR") or (PROJECT_ROOT / "data")
        resolved = Path(raw_dir).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=resolved,
            db_path=resolved / "novelgen_v5.db",
            llm_config_path=resolved / "llm_config.json",
            host=os.environ.get("NOVELGEN_HOST", "127.0.0.1"),
            port=int(os.environ.get("NOVELGEN_PORT", "8787")),
        )
