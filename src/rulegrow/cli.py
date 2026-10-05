"""RuleGrow CLI (PRD section 12).

    rulegrow chat              interactive dialogue
    rulegrow lint              lint the currently published ruleset
    rulegrow history           list published versions
    rulegrow diff v1 v2        unified diff between two versions
    rulegrow rollback vN       validate + rollback to a version
    rulegrow resume <job-id>   continue a paused evolution job
    rulegrow jobs              list evolution jobs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .lint.ast_rules import lint_rule_source
from .lint.diagnostics import Diagnostic
from .providers.openrouter import OpenRouterProvider
from .runtime import ChatRuntime
from .sandbox.docker_runner import DockerSandbox
from .sandbox.protocol import SandboxError
from .storage.versions import VersionStore

PRIVACY_NOTICE = (
    "提示：未命中的输入会发送给 OpenRouter 以生成规则；对话与测试记录保存在本地 .rulegrow/ 目录。"
)


def _error(message: str) -> int:
    print(f"错误：{message}", file=sys.stderr)
    return 1


# ---- commands ----

def cmd_chat(args: argparse.Namespace) -> int:
    cfg = load_config()
    runtime = ChatRuntime(cfg)

    print(f"RuleGrow v{__version__} —— 输入内容开始对话，Ctrl-D 或 /quit 退出。")
    print(PRIVACY_NOTICE)
    while True:
        try:
            text = input("用户：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit"):
            print("再见。")
            return 0
        if text == "/usage":
            print(f"系统：{runtime.provider.usage_summary()}")
            continue
        if text == "/version":
            version = runtime.store.current_version()
            print(f"系统：当前版本 v{version}" if version else "系统：尚无已发布版本。")
            continue

        role, message = runtime.handle_input(text)
        prefix = "助手：" if role == "assistant" else "系统："
        print(f"{prefix}{message}")


def cmd_lint(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = VersionStore(cfg)
    version = store.current_version()
    if version is None:
        print("尚无已发布版本，无可检查规则。")
        return 0
    all_ok = True
    for name in store.load_manifest(version):
        source = store.load_rules(version).get(name, "")
        diag = lint_rule_source(source, name, cfg.max_rule_lines)
        status = "通过" if diag.ok else "失败"
        print(f"v{version}/{name}: {status}")
        if not diag.ok:
            all_ok = False
            print(diag.render())
    print("Lint 全部通过。" if all_ok else "Lint 存在问题。")
    return 0 if all_ok else 1


def cmd_history(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = VersionStore(cfg)
    versions = store.list_versions()
    if not versions:
        print("尚无已发布版本。")
        return 0
    current = store.current_version()
    for v in versions:
        meta = store.load_meta(v)
        marker = "（当前）" if v == current else ""
        model = meta.get("model", "?")
        trigger = meta.get("trigger_input", "")
        line = f"v{v}{marker}  {meta.get('created_at', '?')}  模型 {model}"
        if trigger:
            line += f"  触发输入「{trigger[:40]}」"
        print(line)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = VersionStore(cfg)
    try:
        a, b = _parse_version(args.a), _parse_version(args.b)
    except ValueError as exc:
        return _error(str(exc))
    if a not in store.list_versions() or b not in store.list_versions():
        return _error("版本不存在。可用版本见 `rulegrow history`。")
    lines = store.diff_versions(a, b)
    print("\n".join(lines) if lines else f"v{a} 与 v{b} 无差异。")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = VersionStore(cfg)
    sandbox = DockerSandbox(cfg)
    try:
        target = _parse_version(args.version)
    except ValueError as exc:
        return _error(str(exc))
    if target not in store.list_versions():
        return _error(f"版本 v{target} 不存在。")
    # PRD 10.1: rollback also goes through full validation — lint + sandbox run
    # against the target version's own recorded cases.
    try:
        sandbox.check_available()
    except SandboxError as exc:
        return _error(f"回滚需要 Docker 校验目标版本，当前不可用：{exc}")
    try:
        import json
        import tempfile

        from .sandbox.protocol import TestCase, make_test_task

        files = store.load_rules(target)
        manifest = store.load_manifest(target)
        issues: list[str] = []
        for name in manifest:
            diag = lint_rule_source(files.get(name, ""), name, cfg.max_rule_lines)
            if not diag.ok:
                issues.append(diag.render())
        if issues:
            for line in issues:
                print(line)
            return _error("目标版本未通过 Linter 校验，已取消回滚。")

        cases_data = store.load_cases(target)
        cases = [TestCase(c["input"], "exact", c["accepted_output"]) for c in cases_data.get("regressions", [])]
        cases += [TestCase(g, "miss") for g in cases_data.get("guards", [])]
        if cases:
            with tempfile.TemporaryDirectory(prefix="rulegrow-rollback-") as tmp:
                snapshot = store.snapshot_version(target, Path(tmp))
                (snapshot / "task.json").write_text(
                    json.dumps(make_test_task(cases, store.latest_module(target)), ensure_ascii=False), encoding="utf-8")
                result = sandbox.run_snapshot_task(snapshot)
            if not result.get("ok"):
                return _error(f"目标版本沙箱校验失败：{result.get('error')}")
            failed = [r for r in result.get("results", []) if not r.get("passed")]
            if failed:
                for entry in failed:
                    print(f"校验失败：{entry.get('text')}：{entry.get('reason')}")
                return _error("目标版本未通过其历史用例校验，已取消回滚。")
    except SandboxError as exc:
        return _error(f"目标版本校验失败：{exc}")
    store.rollback(target)
    print(f"已回滚到 v{target}（已通过 Linter 与沙箱校验）。")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    cfg = load_config()
    runtime = ChatRuntime(cfg)
    try:
        job = runtime.store.load_job(args.job_id)
    except ValueError as exc:
        return _error(str(exc))
    if job["status"] != "paused":
        return _error(f"任务 {job['id']} 状态为 {job['status']}，只有 paused 的任务可以继续。")
    if not runtime.provider.ready:
        return _error("未配置 OpenRouter（.env），无法继续修复任务。")
    print(f"继续任务 {job['id']}（触发输入「{job['input'][:40]}」）……")
    outcome = runtime.engine.evolve(job["input"], job=job)
    if outcome.status == "published":
        print(f"系统：检查通过，已发布版本 v{outcome.version}。")
        print(f"助手：{outcome.reply}")
    else:
        print(f"系统：{outcome.message}")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = VersionStore(cfg)
    jobs = store.list_jobs()
    if not jobs:
        print("暂无进化任务。")
        return 0
    for job in jobs:
        print(f"{job['id']}  {job['status']:<9} 尝试 {job.get('attempts', 0)} 次  输入「{job.get('input', '')[:30]}」")
    return 0


def _parse_version(text: str) -> int:
    text = text.lower().lstrip("v")
    if not text.isdigit():
        raise ValueError(f"非法版本号：{text}")
    return int(text)


# ---- parser ----

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rulegrow", description="一个被问倒后会重写自己的 AI。")
    parser.add_argument("--version", action="version", version=f"rulegrow {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("chat", help="开始对话").set_defaults(func=cmd_chat)
    sub.add_parser("lint", help="检查当前已发布规则").set_defaults(func=cmd_lint)
    sub.add_parser("history", help="查看版本历史").set_defaults(func=cmd_history)
    p_diff = sub.add_parser("diff", help="比较两个版本")
    p_diff.add_argument("a")
    p_diff.add_argument("b")
    p_diff.set_defaults(func=cmd_diff)
    p_rb = sub.add_parser("rollback", help="回滚到指定版本")
    p_rb.add_argument("version")
    p_rb.set_defaults(func=cmd_rollback)
    p_resume = sub.add_parser("resume", help="继续暂停的进化任务")
    p_resume.add_argument("job_id")
    p_resume.set_defaults(func=cmd_resume)
    sub.add_parser("jobs", help="列出进化任务").set_defaults(func=cmd_jobs)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
