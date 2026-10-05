"""Chat runtime: dispatch first, evolve only on miss. System/progress output is
kept visually separate from assistant replies (PRD 2.2)."""

from __future__ import annotations

from .config import Config
from .dispatcher import Dispatcher
from .evolution import EvolutionEngine, EvolutionOutcome
from .providers.openrouter import OpenRouterProvider
from .sandbox.docker_runner import DockerSandbox
from .sandbox.protocol import SandboxError
from .storage.versions import VersionStore


class ChatRuntime:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = VersionStore(cfg)
        self.sandbox = DockerSandbox(cfg)
        self.provider = OpenRouterProvider(cfg)
        self.dispatcher = Dispatcher(cfg, self.store, self.sandbox)
        self.engine = EvolutionEngine(cfg, self.store, self.sandbox, self.provider)

    def handle_input(self, text: str, progress=print) -> tuple[str, str]:
        """Returns (role, message): role is 'assistant' or 'system'."""
        text = text.strip()
        if not text:
            return "system", "（空输入）"
        if len(text) > self.cfg.max_input_chars:
            return "system", f"输入过长（上限 {self.cfg.max_input_chars} 字符）。"

        try:
            result = self.dispatcher.dispatch(text)
        except SandboxError as exc:
            return "system", f"Docker 沙箱不可用，已中止本次执行（不会回退到主进程执行）：\n{exc}"

        if result.violation:
            progress(f"系统：警告——已发布规则存在协议违规，已隔离该结果并触发修复：{result.violation}")

        if result.hit:
            return "assistant", result.reply

        # miss -> evolution (requires provider configured)
        if not self.provider.ready:
            return "system", (
                "现有规则无法处理该输入，且未配置 OpenRouter（OPENROUTER_API_KEY / OPENROUTER_MODEL），"
                "无法生成新规则。请参考 .env.example 配置 .env。"
            )

        progress("系统：现有规则无法处理，正在生成规则……")
        try:
            outcome = self.engine.evolve(text, progress=progress)
        except SandboxError as exc:
            return "system", f"沙箱错误，任务已暂停，当前版本未受影响：{exc}"

        if outcome.status == "published":
            progress(f"系统：检查通过，已发布版本 v{outcome.version}。")
            return "assistant", outcome.reply
        if outcome.status == "paused":
            return "system", outcome.message
        return "system", outcome.message
