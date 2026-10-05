"""Dispatcher: runs the CURRENT PUBLISHED ruleset inside the Docker sandbox.

Normal hits never call the model (PRD 1.1). There is deliberately no
host-process fallback: if Docker is unavailable, we raise (PRD 8.4).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .sandbox.docker_runner import DockerSandbox
from .sandbox.protocol import make_dispatch_task
from .storage.versions import VersionStore


@dataclass
class DispatchResult:
    hit: bool
    reply: str = ""
    module: str = ""
    violation: str = ""   # non-empty when a published module broke protocol (isolated)


class Dispatcher:
    def __init__(self, cfg: Config, store: VersionStore, sandbox: DockerSandbox) -> None:
        self.cfg = cfg
        self.store = store
        self.sandbox = sandbox

    def dispatch(self, text: str) -> DispatchResult:
        version = self.store.current_version()
        if version is None:
            return DispatchResult(hit=False)  # empty ruleset: everything misses
        # latest-module policy: run ONLY the latest module, not the whole manifest
        module = self.store.latest_module(version)
        with tempfile.TemporaryDirectory(prefix="rulegrow-dispatch-") as tmp:
            snapshot = self.store.snapshot_version(version, Path(tmp))
            (snapshot / "task.json").write_text(
                json.dumps(make_dispatch_task(text, module), ensure_ascii=False), encoding="utf-8",
            )
            result = self.sandbox.run_snapshot_task(snapshot)
        if result.get("ok") and result.get("hit"):
            return DispatchResult(hit=True, reply=result["reply"], module=result.get("module", ""))
        if result.get("ok"):
            return DispatchResult(hit=False)
        # protocol violation in a PUBLISHED version: isolate it — treat as miss so
        # evolution can fix the ruleset (PRD 3.2), but surface the violation.
        return DispatchResult(
            hit=False,
            violation=f"{result.get('error')}（模块 {result.get('module')}）",
        )
