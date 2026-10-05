"""Version storage: snapshots, atomic publish, diff, rollback, regressions,
guards and evolution jobs. Everything lives under .rulegrow/ (PRD section 10)."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import Config
from ..lint.schema import NAME_RE

SEED_GUARDS = [
    "asdfqwer",
    "zzzzzzzzzz",
    "aaaaaaa",
    "！！！！！！！",
    "1234567890",
    "qqqqwwweee",
]


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _safe_rule_write(rules_dir: Path, name: str, content: str) -> None:
    """Write a rule file with path containment (CODE015 second line of defence)."""
    if not NAME_RE.match(name):
        raise ValueError(f"非法规则文件名：{name}")
    target = (rules_dir / name).resolve()
    if target.parent != rules_dir.resolve():
        raise ValueError(f"规则文件路径越界：{name}")
    target.write_text(content, encoding="utf-8")


class VersionStore:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        cfg.versions_dir.mkdir(parents=True, exist_ok=True)
        cfg.jobs_dir.mkdir(parents=True, exist_ok=True)

    # ---- current / listing ----

    def current_version(self) -> int | None:
        path = self.cfg.current_pointer_path
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("version")
        return int(version) if version is not None else None

    def list_versions(self) -> list[int]:
        if not self.cfg.versions_dir.is_dir():
            return []
        return sorted(
            int(p.name[1:]) for p in self.cfg.versions_dir.iterdir()
            if p.is_dir() and re.fullmatch(r"v\d+", p.name)
        )

    def version_dir(self, version: int) -> Path:
        return self.cfg.versions_dir / f"v{version}"

    def rules_dir(self, version: int) -> Path:
        return self.version_dir(version) / "rules"

    def load_rules(self, version: int) -> dict[str, str]:
        rules: dict[str, str] = {}
        for path in sorted(self.rules_dir(version).glob("*.py")):
            rules[path.name] = path.read_text(encoding="utf-8")
        return rules

    def load_manifest(self, version: int) -> list[str]:
        data = json.loads((self.rules_dir(version) / "manifest.json").read_text(encoding="utf-8"))
        return list(data["modules"])

    def latest_module(self, version: int) -> str:
        """The single module that dispatch/tests execute (latest-module policy).
        Recorded in meta at publish time; falls back to the last manifest entry
        for versions published before the policy existed."""
        meta = self.load_meta(version)
        name = meta.get("latest_module")
        if isinstance(name, str) and name:
            return name
        manifest = self.load_manifest(version)
        return manifest[-1] if manifest else ""

    def load_meta(self, version: int) -> dict[str, Any]:
        path = self.version_dir(version) / "meta.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    # ---- publish / rollback ----

    def publish(self, files: dict[str, str], manifest: list[str], meta: dict[str, Any]) -> int:
        """Atomically publish a new version: build the full version dir first,
        then flip the pointer with an atomic rename (PRD 6.1)."""
        versions = self.list_versions()
        next_version = (versions[-1] + 1) if versions else 1
        vdir = self.version_dir(next_version)
        if vdir.exists():
            raise RuntimeError(f"版本目录已存在：{vdir}")
        rules_dir = vdir / "rules"
        rules_dir.mkdir(parents=True)
        for name, content in files.items():
            _safe_rule_write(rules_dir, name, content)
        _write_json_atomic(rules_dir / "manifest.json", {"modules": manifest})
        meta = {"version": next_version, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), **meta}
        _write_json_atomic(vdir / "meta.json", meta)
        # snapshot the test cases this version was validated against, so
        # rollback can re-validate the target against its own era (PRD 10.1)
        _write_json_atomic(vdir / "cases.json", {
            "regressions": self.load_regressions(),
            "guards": self.load_guards(),
        })
        # pointer flip is the commit point
        _write_json_atomic(self.cfg.current_pointer_path, {"version": next_version})
        return next_version

    def load_cases(self, version: int) -> dict[str, Any]:
        path = self.version_dir(version) / "cases.json"
        if not path.is_file():
            return {"regressions": [], "guards": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def rollback(self, version: int) -> None:
        if version not in self.list_versions():
            raise ValueError(f"版本 v{version} 不存在")
        _write_json_atomic(self.cfg.current_pointer_path, {"version": version})

    # ---- diff ----

    def diff_versions(self, a: int, b: int) -> list[str]:
        import difflib

        files_a = self.load_rules(a)
        files_b = self.load_rules(b)
        out: list[str] = []
        for name in sorted(set(files_a) | set(files_b)):
            if name in files_a and name in files_b:
                d = list(difflib.unified_diff(
                    files_a[name].splitlines(), files_b[name].splitlines(),
                    fromfile=f"v{a}/{name}", tofile=f"v{b}/{name}", lineterm="",
                ))
                if d:
                    out.extend(d)
                    out.append("")
            elif name in files_a:
                out.append(f"v{a}/{name} 在 v{b} 中被删除")
                out.append("")
            else:
                out.append(f"v{b}/{name} 为新增文件")
                out.append("")
        return out

    # ---- snapshot staging (used for sandbox tests; immutable copy) ----

    def stage_snapshot(self, dest: Path, files: dict[str, str], manifest: list[str]) -> Path:
        rules_dir = dest / "rules"
        rules_dir.mkdir(parents=True)
        for name, content in files.items():
            _safe_rule_write(rules_dir, name, content)
        _write_json_atomic(rules_dir / "manifest.json", {"modules": manifest})
        return dest

    def snapshot_version(self, version: int, dest: Path) -> Path:
        src = self.rules_dir(version)
        rules_dir = dest / "rules"
        rules_dir.mkdir(parents=True)
        for path in src.iterdir():
            shutil.copy2(path, rules_dir / path.name)
        return dest

    # ---- regressions / guards ----

    def load_regressions(self) -> list[dict[str, Any]]:
        path = self.cfg.regressions_path
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))["cases"]

    def save_regressions(self, cases: list[dict[str, Any]]) -> None:
        _write_json_atomic(self.cfg.regressions_path, {"cases": cases})

    def add_regression(self, text: str, accepted_output: str) -> None:
        cases = self.load_regressions()
        if any(c["input"] == text for c in cases):
            return
        cases.append({"input": text, "accepted_output": accepted_output, "assertion_mode": "exact"})
        self.save_regressions(cases)

    def load_guards(self) -> list[str]:
        path = self.cfg.guards_path
        if not path.is_file():
            self.save_guards(SEED_GUARDS)
            return list(SEED_GUARDS)
        return json.loads(path.read_text(encoding="utf-8"))["inputs"]

    def save_guards(self, inputs: list[str]) -> None:
        _write_json_atomic(self.cfg.guards_path, {"inputs": inputs})

    # ---- evolution jobs ----

    def job_path(self, job_id: str) -> Path:
        return self.cfg.jobs_dir / f"{job_id}.json"

    def create_job(self, text: str) -> dict[str, Any]:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        job = {
            "id": job_id,
            "input": text,
            "status": "running",          # running | paused | completed | failed
            "attempts": 0,
            "base_version": self.current_version(),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "attempts_log": [],
        }
        self.save_job(job)
        return job

    def save_job(self, job: dict[str, Any]) -> None:
        job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_json_atomic(self.job_path(job["id"]), job)

    def load_job(self, job_id: str) -> dict[str, Any]:
        path = self.job_path(job_id)
        if not path.is_file():
            raise ValueError(f"任务 {job_id} 不存在")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = []
        for path in sorted(self.cfg.jobs_dir.glob("*.json")):
            try:
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return jobs
