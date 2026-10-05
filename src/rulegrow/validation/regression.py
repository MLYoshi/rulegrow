"""Regression and guard test construction/evaluation (PRD 7.2 / 7.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..lint.diagnostics import Diagnostic
from ..sandbox.protocol import TestCase
from ..storage.versions import VersionStore


@dataclass
class CandidateTestReport:
    diagnostic: Diagnostic = field(default_factory=lambda: Diagnostic(stage="sandbox"))
    current_reply: str = ""
    current_module: str = ""


def build_test_cases(store: VersionStore, current_input: str) -> list[TestCase]:
    """One combined task: current input must hit, regressions exact-match,
    guard inputs must miss (PRD 6 pipeline)."""
    cases = [TestCase(current_input, "hit")]
    for case in store.load_regressions():
        cases.append(TestCase(case["input"], "exact", case["accepted_output"]))
    for guard in store.load_guards():
        cases.append(TestCase(guard, "miss"))
    return cases


def evaluate_test_result(result: dict[str, Any], store: VersionStore,
                         current_input: str) -> CandidateTestReport:
    """Turn the sandbox test-mode result into a Diagnostic. Current-input reply
    is captured (it becomes the accepted regression output on publish)."""
    report = CandidateTestReport()
    diag = report.diagnostic
    results = result.get("results", [])

    for entry in results:
        text = entry.get("text", "")
        if not entry.get("passed", False):
            if entry.get("kind") == "exact":
                diag.add(
                    "REG001",
                    f"历史回归失败：输入「{text}」应输出「{str(entry.get('expected'))[:80]}」，"
                    f"实际输出「{str(entry.get('actual'))[:80]}」",
                )
            elif entry.get("kind") == "miss":
                diag.add(
                    "GUARD001",
                    f"守卫输入被命中：「{text}」不应被回答，实际输出「{str(entry.get('actual'))[:80]}」",
                )
            else:
                diag.add("CUR001", f"当前输入未得到合法回复：{entry.get('reason', '')}")
        elif entry.get("kind") == "hit" and text == current_input[:120]:
            report.current_reply = str(entry.get("actual", ""))
            report.current_module = str(entry.get("module", ""))

    if not report.current_reply and diag.ok:
        diag.add("CUR001", "沙箱结果中缺少当前输入的命中记录")
    return report


def render_regression_failure(entry: dict[str, Any]) -> str:
    return f"输入「{entry.get('text', '')}」：{entry.get('reason', '未通过')}"
