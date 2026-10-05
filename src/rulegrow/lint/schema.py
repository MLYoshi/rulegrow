"""Schema and path validation for AI-produced candidate rulesets (PRD 3, 4 CODE015, 7.1)."""

from __future__ import annotations

import re

from ..config import Config
from .diagnostics import Diagnostic

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\.py$")


def validate_candidate(candidate: object, cfg: Config) -> tuple[Diagnostic, dict[str, str]]:
    """Validate the model's candidate structure.

    Expected shape:
        {"manifest": ["greetings.py", ...], "files": [{"name": "...", "content": "..."}]}

    Returns (diagnostic, files) where files maps name -> content when structurally valid.
    """
    diag = Diagnostic(stage="schema")
    if not isinstance(candidate, dict):
        diag.add("SCHEMA001", f"候选必须是 JSON 对象，实际是 {type(candidate).__name__}")
        return diag, {}

    manifest = candidate.get("manifest")
    files_raw = candidate.get("files")

    if not isinstance(manifest, list) or not all(isinstance(x, str) for x in manifest):
        diag.add("SCHEMA002", "manifest 必须是文件名字符串数组")
        return diag, {}
    if not isinstance(files_raw, list):
        diag.add("SCHEMA003", "files 必须是数组")
        return diag, {}

    files: dict[str, str] = {}
    for item in files_raw:
        if not isinstance(item, dict) or "name" not in item or "content" not in item:
            diag.add("SCHEMA004", "每个文件必须是 {\"name\": ..., \"content\": ...}")
            continue
        name, content = item["name"], item["content"]
        if not isinstance(name, str) or not isinstance(content, str):
            diag.add("SCHEMA004", "文件 name 和 content 必须是字符串")
            continue
        # CODE015: file name whitelist (regex makes path escape impossible; we never
        # treat names as paths and always resolve within the rules dir when writing).
        if not NAME_RE.match(name):
            diag.add("CODE015", f"非法文件名 `{name}`：只允许小写字母/数字/下划线加 .py")
            continue
        if name in files:
            diag.add("CODE015", f"重复文件 `{name}`")
            continue
        if len(content.encode("utf-8")) > cfg.max_rule_bytes:
            diag.add("CODE015", f"文件 `{name}` 超过 {cfg.max_rule_bytes} 字节")
            continue
        if not content.strip():
            diag.add("CODE015", f"文件 `{name}` 内容为空")
            continue
        files[name] = content

    if len(files) > cfg.max_rule_files:
        diag.add("CODE015", f"规则文件数量超过上限 {cfg.max_rule_files}")

    if len(set(manifest)) != len(manifest):
        diag.add("SCHEMA005", "manifest 存在重复文件")
    for name in manifest:
        if name not in files:
            diag.add("SCHEMA006", f"manifest 引用了不存在的文件 `{name}`")
    unregistered = sorted(set(files) - set(manifest))
    if unregistered:
        diag.add("SCHEMA007", f"存在未登记的规则文件：{unregistered}")
    if not manifest and files:
        diag.add("SCHEMA008", "manifest 为空但存在规则文件")

    return diag, files
