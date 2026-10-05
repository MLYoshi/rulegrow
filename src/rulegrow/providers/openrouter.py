"""OpenRouter provider: generation, repair, and (optional) semantic review.

Handles timeouts, rate limits and server errors; never logs the API key;
structurally validates responses instead of accepting anything JSON-ish.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


class ProviderError(RuntimeError):
    pass


@dataclass
class ModelCall:
    kind: str                    # generate | repair | review
    model: str = ""
    elapsed: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class OpenRouterProvider:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.calls: list[ModelCall] = []

    @property
    def ready(self) -> bool:
        return bool(self.cfg.api_key and self.cfg.model)

    def require_ready(self) -> None:
        if not self.cfg.api_key:
            raise ProviderError("缺少 OPENROUTER_API_KEY：请复制 .env.example 为 .env 并填写。")
        if not self.cfg.model:
            raise ProviderError("缺少 OPENROUTER_MODEL：请在 .env 中配置实际的 OpenRouter 模型 ID。")

    # ---- low-level ----

    def _chat(self, messages: list[dict[str, str]], kind: str, timeout: float = 90.0) -> str:
        self.require_ready()
        call = ModelCall(kind=kind, model=self.cfg.model)
        self.calls.append(call)
        payload = json.dumps({
            "model": self.cfg.model,
            "messages": messages,
            "temperature": 0.2,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + "/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.cfg.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if exc.code in (429, 500, 502, 503, 529):
                raise ProviderError(f"OpenRouter 暂时不可用（HTTP {exc.code}），请稍后重试：{detail}") from exc
            raise ProviderError(f"OpenRouter 请求失败（HTTP {exc.code}）：{detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"OpenRouter 连接失败：{exc}") from exc
        finally:
            call.elapsed = time.monotonic() - started

        # structural validation of the response (PRD 9.2)
        try:
            choices = body["choices"]
            message = choices[0]["message"]
            content = message["content"]
            if not isinstance(content, str):
                if isinstance(content, list):  # some models return content parts
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                else:
                    raise KeyError("content")
        except (KeyError, IndexError, TypeError) as exc:
            call.error = "invalid response structure"
            raise ProviderError(f"OpenRouter 响应结构不合法：{json.dumps(body, ensure_ascii=False)[:300]}") from exc
        call.usage = body.get("usage", {}) or {}
        return content

    # ---- JSON extraction ----

    _FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

    def _extract_json(self, content: str) -> Any:
        text = content.strip()
        match = self._FENCE_RE.search(text)
        if match:
            text = match.group(1).strip()
        # try whole-string first, then first balanced {...} block
        for candidate in (text, self._first_object(text)):
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise ProviderError("模型没有返回可解析的 JSON。请重试或更换模型。")

    @staticmethod
    def _first_object(text: str) -> str:
        start = text.find("{")
        if start < 0:
            return ""
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return ""

    # ---- high-level operations ----

    def _load_prompt(self, name: str) -> str:
        path = PROMPTS_DIR / name
        if not path.is_file():
            raise ProviderError(f"缺少提示词文件：{path}")
        return path.read_text(encoding="utf-8")

    def generate_or_repair(
        self,
        current_files: dict[str, str],
        current_manifest: list[str],
        user_input: str,
        diagnostics: list[str] | None = None,
        latest_module: str = "",
    ) -> dict[str, Any]:
        """Ask the model for a COMPLETE candidate ruleset (PRD 6.1:
        full rulesets only, never unverified patches). The previous
        version's Python code (esp. the latest module) is fed as the base
        for the next version."""
        kind = "repair" if diagnostics else "generate"
        system = self._load_prompt("generator.txt")
        ruleset = json.dumps(
            {"manifest": current_manifest, "files": [
                {"name": n, "content": c} for n, c in current_files.items()
            ]},
            ensure_ascii=False,
        )
        user_parts = [
            f"## 上一版规则集（JSON，其中包含上一版的 Python 代码逻辑）\n{ruleset}" if current_files else "## 上一版规则集\n（空，尚无任何规则）",
        ]
        if latest_module:
            user_parts.append(
                f"## 上一版的最新模块\n`{latest_module}` —— 请在该模块代码的基础上生成下一版："
                "保留其已有分支逻辑原样不动，只做必要的新增；其他旧文件必须与上一版完全相同。"
            )
        user_parts.append(f"## 需要新掌握的用户输入\n{user_input}")
        if diagnostics:
            user_parts.append("## 上一轮候选未通过检查的诊断信息\n" + "\n".join(f"- {d}" for d in diagnostics))
            user_parts.append("请基于上一版规则集输出修复后的完整候选规则集。")
        else:
            user_parts.append("请基于上一版规则集输出下一版完整候选规则集。")
        content = self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(user_parts)}],
            kind=kind,
        )
        candidate = self._extract_json(content)
        if not isinstance(candidate, dict):
            raise ProviderError("模型返回的候选不是 JSON 对象。")
        return candidate

    def review_reply(self, user_input: str, reply: str) -> tuple[bool, str]:
        """Optional semantic review (PRD 5.1). User input and candidate reply are
        passed as DATA; the reviewer prompt is fixed and not overridable."""
        system = self._load_prompt("reviewer.txt")
        user = json.dumps({"user_input": user_input, "candidate_reply": reply}, ensure_ascii=False)
        content = self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            kind="review",
        )
        verdict = self._extract_json(content)
        if isinstance(verdict, dict) and isinstance(verdict.get("pass"), bool):
            reason = verdict.get("reason", "")
            return verdict["pass"], str(reason)
        return False, "评审输出结构不合法，按不通过处理。"

    # ---- observability ----

    def usage_summary(self) -> str:
        if not self.calls:
            return "本次无模型调用。"
        total_tokens = sum(int(c.usage.get("total_tokens", 0) or 0) for c in self.calls)
        total_seconds = sum(c.elapsed for c in self.calls)
        return (
            f"模型调用 {len(self.calls)} 次（{total_seconds:.1f}s，约 {total_tokens} tokens）。"
        )
