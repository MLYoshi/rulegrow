"""RuleGrow fixed sandbox executor (runs INSIDE the container).

Reads a task JSON from argv[1] (inside the read-only snapshot mount),
executes rule modules in manifest order, writes ONE JSON result to stdout.

This file is part of the versioned runner image. AI-generated rule files are
mounted read-only; they can never modify this executor.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

MAX_REPLY_CHARS = 4000


def check_return(value: Any) -> tuple[str, Any]:
    """Protocol check (PRD 3.2). Returns (status, reply):
    status in {"hit", "miss", "violation"}."""
    if value is False:                      # must be exactly False (is False, PRD 3.2)
        return "miss", False
    if isinstance(value, str):
        if value.strip() == "":
            return "violation", ""          # empty / blank reply
        if len(value) > MAX_REPLY_CHARS:
            return "violation", value[:64]
        return "hit", value
    if value is True or value is None:
        return "violation", value
    return "violation", value               # any other type


def load_modules(rules_dir: Path, only: str | None = None) -> list[tuple[str, Callable[[str], Any]]]:
    """Load rule modules from the manifest. When `only` is given (the
    latest-module policy), load just that module and nothing else."""
    manifest_path = rules_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    modules: list[tuple[str, Callable[[str], Any]]] = []
    for name in manifest["modules"]:
        if only is not None and name != only:
            continue
        path = rules_dir / name
        spec = importlib.util.spec_from_file_location(f"rule_{len(modules)}_{name[:-3]}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载规则文件 {name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)     # noqa: S302 - fixed executor loads linted files
        handle = getattr(module, "handle", None)
        if not callable(handle):
            raise RuntimeError(f"规则文件 {name} 缺少 handle 函数")
        modules.append((name, handle))
    if only is not None and not modules:
        raise RuntimeError(f"指定执行的模块 {only} 不在 manifest 中")
    return modules


def dispatch(modules: list[tuple[str, Callable[[str], Any]]], text: str) -> dict[str, Any]:
    for name, handle in modules:
        try:
            value = handle(text)
        except Exception as exc:  # a crashing module must not take down dispatch
            return {"status": "error", "module": name, "error": f"{type(exc).__name__}: {exc}"}
        status, reply = check_return(value)
        if status == "hit":
            return {"status": "hit", "module": name, "reply": reply}
        if status == "violation":
            return {
                "status": "violation", "module": name,
                "error": f"协议违规：返回值必须是字符串或 False，实际为 {value!r}"[:200],
            }
    return {"status": "miss", "module": None, "reply": None}


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    base = Path(task.get("rules_dir", "rules"))
    # Path containment: names come from manifest.json inside our own snapshot,
    # but never trust them anyway.
    rules_dir = (Path("/snapshot") / base).resolve()
    snapshot = Path("/snapshot").resolve()
    if not str(rules_dir).startswith(str(snapshot)):
        return {"ok": False, "error": "路径越界"}

    modules = load_modules(rules_dir, task.get("module"))

    mode = task.get("mode")
    if mode == "dispatch":
        outcome = dispatch(modules, task["text"])
        if outcome["status"] == "hit":
            return {"ok": True, "mode": "dispatch", "hit": True, "reply": outcome["reply"], "module": outcome["module"]}
        if outcome["status"] == "miss":
            return {"ok": True, "mode": "dispatch", "hit": False}
        return {"ok": False, "mode": "dispatch", "error": outcome["error"], "module": outcome["module"]}

    if mode == "test":
        results = []
        for case in task["cases"]:
            expect = case["expect"]
            outcome = dispatch(modules, case["text"])
            entry: dict[str, Any] = {"text": case["text"][:120], "kind": expect["kind"]}
            if expect["kind"] == "exact":
                entry["expected"] = str(expect.get("output", ""))[:120]
            if outcome["status"] == "error":
                entry.update(passed=False, reason=f"执行错误：{outcome['error']}", module=outcome["module"])
            elif outcome["status"] == "violation":
                entry.update(passed=False, reason=outcome["error"], module=outcome["module"])
            else:
                hit = outcome["status"] == "hit"
                entry["module"] = outcome["module"]
                if expect["kind"] == "miss":
                    entry.update(passed=not hit, actual=outcome["reply"],
                                 reason="" if not hit else "守卫输入被命中")
                elif expect["kind"] == "hit":
                    entry.update(passed=hit, actual=outcome["reply"],
                                 reason="" if hit else "应当命中但未命中")
                else:  # exact
                    entry.update(passed=hit and outcome["reply"] == expect.get("output", ""),
                                 actual=outcome["reply"],
                                 reason="" if (hit and outcome["reply"] == expect.get("output", ""))
                                 else "回归输出与已验收回复不一致")
            results.append(entry)
        return {"ok": True, "mode": "test", "results": results}

    return {"ok": False, "error": f"未知任务模式 {mode!r}"}


def main() -> int:
    try:
        task_path = Path(sys.argv[1])
        task = json.loads(task_path.read_text(encoding="utf-8"))
        result = run_task(task)
    except Exception:
        result = {
            "ok": False,
            "error": "执行器异常：" + traceback.format_exc(limit=3)[-300:],
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
