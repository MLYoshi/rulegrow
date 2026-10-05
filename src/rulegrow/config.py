"""Configuration: .env loading, budgets, data directories."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DATA_DIR_NAME = ".rulegrow"
RUNNER_IMAGE = "rulegrow-runner:latest"


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE lines, # comments). Never overrides real env."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key:
            values.setdefault(key, value)
    return values


@dataclass
class Config:
    # OpenRouter
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = ""

    # Budgets (PRD 6.2)
    max_auto_attempts: int = 8
    max_job_seconds: int = 180

    # Sandbox
    runner_image: str = RUNNER_IMAGE
    docker_timeout_seconds: int = 30
    container_memory: str = "256m"
    container_cpus: str = "0.5"

    # Limits
    max_rule_files: int = 20
    max_rule_lines: int = 200
    max_rule_bytes: int = 16_384
    max_reply_chars: int = 4_000
    max_input_chars: int = 2_000
    max_result_bytes: int = 262_144  # sandbox JSON result cap

    # Quality
    reviewer_enabled: bool = False  # optional semantic review via model (PRD 5.1)

    # Paths
    project_root: Path = field(default_factory=Path.cwd)

    @property
    def data_dir(self) -> Path:
        return self.project_root / DATA_DIR_NAME

    @property
    def versions_dir(self) -> Path:
        return self.data_dir / "versions"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def regressions_path(self) -> Path:
        return self.data_dir / "regressions.json"

    @property
    def guards_path(self) -> Path:
        return self.data_dir / "guards.json"

    @property
    def current_pointer_path(self) -> Path:
        return self.data_dir / "current.json"


def load_config(project_root: Path | None = None) -> Config:
    root = project_root or Path.cwd()
    env = load_dotenv(root / ".env")
    cfg = Config(project_root=root)
    cfg.api_key = os.environ.get("OPENROUTER_API_KEY", env.get("OPENROUTER_API_KEY", ""))
    cfg.base_url = os.environ.get("OPENROUTER_BASE_URL", env.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL))
    cfg.model = os.environ.get("OPENROUTER_MODEL", env.get("OPENROUTER_MODEL", ""))

    settings_path = root / "rulegrow.yaml"
    if settings_path.is_file():
        _apply_yaml_settings(cfg, settings_path)
    return cfg


def _apply_yaml_settings(cfg: Config, path: Path) -> None:
    """Tiny flat YAML reader for `key: value` lines (no nested structures needed)."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if not key:
            continue
        if raw in ("true", "false"):
            value: object = raw == "true"
        elif raw.lstrip("-").isdigit():
            value = int(raw)
        else:
            value = raw
        if hasattr(cfg, key) and not key.startswith("_"):
            setattr(cfg, key, value)
