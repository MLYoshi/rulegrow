"""Sandbox protocol: task JSON (host -> container) and result JSON (container -> host).

Container never receives secrets; it only sees the task file inside the
read-only snapshot. Results are plain JSON with hard size caps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import Config


class SandboxError(RuntimeError):
    """Docker unavailable, container failure, timeout, or invalid result."""


# ---- task builders (host side) ----

def make_dispatch_task(text: str, module: str = "") -> dict[str, Any]:
    """Dispatch runs ONLY the latest module (not the whole manifest)."""
    task: dict[str, Any] = {"mode": "dispatch", "rules_dir": "rules", "text": text}
    if module:
        task["module"] = module
    return task


@dataclass
class TestCase:
    text: str
    kind: str          # "exact" | "hit" | "miss"
    output: str = ""   # for kind == "exact"

    def to_dict(self) -> dict[str, Any]:
        case: dict[str, Any] = {"text": self.text, "expect": {"kind": self.kind}}
        if self.kind == "exact":
            case["expect"]["output"] = self.output
        return case


def make_test_task(cases: list[TestCase], module: str = "") -> dict[str, Any]:
    """Candidate tests run ONLY the candidate's latest module."""
    task: dict[str, Any] = {"mode": "test", "rules_dir": "rules", "cases": [c.to_dict() for c in cases]}
    if module:
        task["module"] = module
    return task


# ---- result parsing (host side) ----

def parse_result(raw: str, cfg: Config) -> dict[str, Any]:
    """Parse and structurally validate the container's JSON result (PRD 9.2:
    never accept a response just because it 'looks like' JSON)."""
    if len(raw.encode("utf-8")) > cfg.max_result_bytes:
        raise SandboxError("沙箱返回结果超过大小上限")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SandboxError(f"沙箱返回了无法解析的 JSON：{exc}") from exc

    if not isinstance(result, dict) or "ok" not in result:
        raise SandboxError("沙箱返回结构不合法：缺少 ok 字段")
    if result["ok"] is True:
        mode = result.get("mode")
        if mode == "dispatch":
            if not isinstance(result.get("hit"), bool):
                raise SandboxError("沙箱返回结构不合法：hit 必须是布尔值")
            if result["hit"] and not isinstance(result.get("reply"), str):
                raise SandboxError("沙箱返回结构不合法：reply 必须是字符串")
        elif mode == "test":
            results = result.get("results")
            if not isinstance(results, list):
                raise SandboxError("沙箱返回结构不合法：results 必须是数组")
        else:
            raise SandboxError("沙箱返回结构不合法：未知 mode")
    else:
        if not isinstance(result.get("error"), str):
            raise SandboxError("沙箱返回结构不合法：error 必须是字符串")
    return result
