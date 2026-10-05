"""Evolution engine: the AI proposes, the framework disposes (PRD 6, 15).

Pipeline per attempt:
    model -> schema -> AST lint -> Docker sandbox tests -> quality -> atomic publish
Any failure feeds structured diagnostics back to the model for repair,
within the attempt/time budget; otherwise the job is paused and the old
version keeps serving.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .lint.ast_rules import lint_rule_source
from .lint.diagnostics import Diagnostic
from .lint.schema import validate_candidate
from .providers.openrouter import OpenRouterProvider, ProviderError
from .sandbox.docker_runner import DockerSandbox
from .sandbox.protocol import SandboxError, make_test_task
from .storage.versions import VersionStore
from .validation import dialogue_quality
from .validation.regression import build_test_cases, evaluate_test_result

ProgressFn = Callable[[str], None]


@dataclass
class EvolutionOutcome:
    status: str                 # "published" | "paused" | "failed"
    reply: str = ""
    version: int | None = None
    job_id: str = ""
    message: str = ""
    attempts: int = 0
    diagnostics: list[str] = field(default_factory=list)


class EvolutionEngine:
    def __init__(self, cfg: Config, store: VersionStore, sandbox: DockerSandbox,
                 provider: OpenRouterProvider) -> None:
        self.cfg = cfg
        self.store = store
        self.sandbox = sandbox
        self.provider = provider

    # ---- main entry ----

    def evolve(self, text: str, progress: ProgressFn = print,
               job: dict[str, Any] | None = None) -> EvolutionOutcome:
        if job is None:
            job = self.store.create_job(text)
        job["status"] = "running"
        self.store.save_job(job)

        started = time.monotonic()
        diagnostics: list[str] = list(job.get("pending_diagnostics", []))
        outcome = EvolutionOutcome(status="paused", job_id=job["id"], message="")
        base_version = self.store.current_version()

        attempt = 0
        while attempt < self.cfg.max_auto_attempts:
            attempt += 1
            job["attempts"] = attempt
            elapsed = time.monotonic() - started
            if elapsed > self.cfg.max_job_seconds:
                outcome.status = "paused"
                outcome.message = f"任务超过时间预算（{self.cfg.max_job_seconds}s），已暂停。"
                break

            progress(f"系统：第 {attempt} 次尝试{'（修复）' if diagnostics else ''}……")

            # 1) model proposes a complete candidate ruleset
            try:
                current_files = self.store.load_rules(base_version) if base_version else {}
                current_manifest = self.store.load_manifest(base_version) if base_version else []
                latest_module = self.store.latest_module(base_version) if base_version else ""
                candidate = self.provider.generate_or_repair(
                    current_files, current_manifest, text, diagnostics or None,
                    latest_module=latest_module,
                )
            except ProviderError as exc:
                diagnostics = [f"模型调用失败：{exc}"]
                self._log_attempt(job, attempt, "provider", [str(exc)], None)
                continue

            # 2) schema / path validation
            schema_diag, files = validate_candidate(candidate, self.cfg)
            if not schema_diag.ok:
                diagnostics = [schema_diag.render()]
                self._log_attempt(job, attempt, "schema", diagnostics, candidate)
                progress(f"系统：第 {attempt} 次检查失败（Schema），正在让 AI 修复……")
                continue
            manifest = list(candidate["manifest"])

            # 3) AST lint every file
            lint_issues: list[str] = []
            for name in manifest:
                diag = lint_rule_source(files[name], name, self.cfg.max_rule_lines)
                if not diag.ok:
                    lint_issues.append(diag.render())
            if lint_issues:
                diagnostics = lint_issues
                self._log_attempt(job, attempt, "lint", lint_issues, candidate)
                progress(f"系统：第 {attempt} 次检查失败（Linter），正在让 AI 修复……")
                continue

            # 4) Docker sandbox tests: current input / regressions / guards
            #    (latest-module policy: only the candidate's latest module runs)
            candidate_latest = self._candidate_latest_module(files, manifest, base_version)
            progress("系统：正在检查候选规则……")
            try:
                report = self._run_sandbox_tests(files, manifest, text, candidate_latest)
            except SandboxError as exc:
                # Docker unavailable / container failure: fail safely, no host fallback
                self._log_attempt(job, attempt, "sandbox", [str(exc)], candidate)
                job["status"] = "paused"
                job["pending_diagnostics"] = diagnostics
                self.store.save_job(job)
                return EvolutionOutcome(
                    status="failed", job_id=job["id"], attempts=attempt,
                    message=f"沙箱不可用，任务暂停，已发布版本未受影响：{exc}",
                )
            if not report.diagnostic.ok:
                diagnostics = [report.diagnostic.render()]
                self._log_attempt(job, attempt, "sandbox", diagnostics, candidate)
                progress(f"系统：第 {attempt} 次检查失败（沙箱测试），正在让 AI 修复……")
                continue

            # 5) dialogue quality (heuristic + optional model review)
            reviewer = None
            if self.cfg.reviewer_enabled:
                reviewer = self.provider.review_reply
            quality_diag = dialogue_quality.evaluate_reply(text, report.current_reply, reviewer)
            if not quality_diag.ok:
                diagnostics = [quality_diag.render()]
                self._log_attempt(job, attempt, "quality", diagnostics, candidate)
                progress(f"系统：第 {attempt} 次检查失败（质量评估），正在让 AI 修复……")
                continue

            # 6) all checks passed -> atomic publish (PRD 6.1)
            if self.store.current_version() != base_version:
                diagnostics = ["发布冲突：规则集在任务期间被更新，请基于最新版本重新生成。"]
                base_version = self.store.current_version()
                self._log_attempt(job, attempt, "publish", diagnostics, candidate)
                continue
            meta = {
                "model": self.cfg.model,
                "trigger_input": text,
                "job_id": job["id"],
                "attempts": attempt,
                "base_version": base_version,
                "latest_module": candidate_latest,
            }
            new_version = self.store.publish(files, manifest, meta)
            self.store.add_regression(text, report.current_reply)
            job["status"] = "completed"
            job["published_version"] = new_version
            job["pending_diagnostics"] = []
            self._log_attempt(job, attempt, "published", [], candidate)
            self.store.save_job(job)

            # PRD 6: after publishing, re-run the current input through Docker
            from .dispatcher import Dispatcher
            final = Dispatcher(self.cfg, self.store, self.sandbox).dispatch(text)
            reply = final.reply if final.hit else report.current_reply
            return EvolutionOutcome(
                status="published", reply=reply, version=new_version,
                job_id=job["id"], attempts=attempt,
            )

        # budget exhausted -> paused, old version keeps serving (PRD 6.2)
        job["status"] = "paused"
        job["pending_diagnostics"] = diagnostics
        self.store.save_job(job)
        outcome.attempts = attempt
        outcome.diagnostics = diagnostics
        if not outcome.message:
            outcome.message = (
                f"达到自动修复次数上限（{self.cfg.max_auto_attempts} 次），任务暂停。"
                "使用 `rulegrow resume " + job["id"] + "` 可继续修复。"
            )
        return outcome

    # ---- helpers ----

    def _candidate_latest_module(self, files: dict[str, str], manifest: list[str],
                                 base_version: int | None) -> str:
        """The candidate's latest module: the module it adds or modifies
        (last one in manifest order if several); fallback to the last entry."""
        base_files = self.store.load_rules(base_version) if base_version else {}
        changed = [n for n in manifest if base_files.get(n) != files.get(n)]
        if changed:
            return changed[-1]
        return manifest[-1] if manifest else ""

    def _run_sandbox_tests(self, files: dict[str, str], manifest: list[str], text: str,
                           latest_module: str = ""):
        cases = build_test_cases(self.store, text)
        with tempfile.TemporaryDirectory(prefix="rulegrow-candidate-") as tmp:
            snapshot = self.store.stage_snapshot(Path(tmp), files, manifest)
            (snapshot / "task.json").write_text(
                json.dumps(make_test_task(cases, latest_module), ensure_ascii=False), encoding="utf-8",
            )
            result = self.sandbox.run_snapshot_task(snapshot)
        if not result.get("ok"):
            diag = Diagnostic(stage="sandbox")
            diag.add("SBX001", f"沙箱执行失败：{result.get('error')}")
            from .validation.regression import CandidateTestReport
            return CandidateTestReport(diagnostic=diag)
        return evaluate_test_result(result, self.store, text)

    def _log_attempt(self, job: dict[str, Any], attempt: int, stage: str,
                     issues: list[str], candidate: dict[str, Any] | None) -> None:
        job.setdefault("attempts_log", []).append({
            "attempt": attempt,
            "stage": stage,
            "issues": issues,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        if candidate is not None:
            # keep the last candidate for inspection/resume (PRD 6.2)
            job["last_candidate"] = {
                "manifest": candidate.get("manifest", []),
                "files": candidate.get("files", []),
            }
        self.store.save_job(job)
