"""Structured diagnostics shared by all checks (PRD 15: the framework owns judgement)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Issue:
    code: str          # e.g. "CODE003"
    message: str
    file: str = ""     # file name (not a path) inside the ruleset
    line: int = 0

    def render(self) -> str:
        loc = f"{self.file}:{self.line}" if self.file else ""
        return f"[{self.code}] {loc} {self.message}".strip()


@dataclass
class Diagnostic:
    """Result of one check stage. Empty issues == pass."""
    stage: str                     # schema | lint | sandbox | regression | guard | quality | provider
    issues: list[Issue] = field(default_factory=list)
    detail: str = ""               # human-readable summary for the repair prompt

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, code: str, message: str, file: str = "", line: int = 0) -> None:
        self.issues.append(Issue(code, message, file, line))

    def render(self) -> str:
        lines = [f"检查阶段 {self.stage} 失败："]
        for issue in self.issues:
            lines.append("  " + issue.render())
        if self.detail:
            lines.append(f"  说明：{self.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"stage": self.stage, "issues": [asdict(i) for i in self.issues], "detail": self.detail}
