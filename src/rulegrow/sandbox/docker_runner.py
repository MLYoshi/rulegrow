"""Host-side Docker sandbox runner (PRD section 8).

Fixed image, read-only root fs, no network, non-root user, dropped capabilities,
resource limits, per-run timeout, temp snapshot mounts, JSON-only results.
No Docker socket, no API key, no host env inside the container.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

from ..config import Config
from .protocol import SandboxError, parse_result


class DockerSandbox:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # ---- docker availability / image ----

    def _run_docker(self, args: list[str], timeout: float | None = None,
                    check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(
                ["docker", *args], capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxError(
                "未找到 docker 命令。RuleGrow 需要 Docker 执行规则，"
                "请先安装并启动 Docker：https://docs.docker.com/get-docker/"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError("docker 命令超时") from exc
        if check and proc.returncode != 0:
            raise SandboxError(f"docker 命令失败：{proc.stderr.strip()[:400]}")
        return proc

    def check_available(self) -> None:
        self._run_docker(["info", "--format", "{{.ServerVersion}}"], timeout=15)

    def ensure_image(self) -> None:
        """The image is fixed and version-controlled with this repo (PRD 8.4);
        built locally if missing. Never pull third-party images at runtime."""
        proc = self._run_docker(["image", "inspect", self.cfg.runner_image], check=False)
        if proc.returncode == 0:
            return
        root = self.cfg.project_root
        if not (root / "Dockerfile.runner").is_file():
            raise SandboxError(
                f"沙箱镜像 {self.cfg.runner_image} 不存在，且当前目录缺少 Dockerfile.runner。"
                "请在项目根目录运行。"
            )
        print("系统：首次运行，正在构建固定沙箱镜像……")
        self._run_docker(
            ["build", "-f", "Dockerfile.runner", "-t", self.cfg.runner_image, "."],
            timeout=300,
        )

    # ---- execution ----

    def _docker_run_args(self, snapshot_dir: Path, name: str) -> list[str]:
        return [
            "run", "--rm",
            "--name", name,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "10001:10001",
            "--memory", self.cfg.container_memory,
            "--memory-swap", self.cfg.container_memory,
            "--cpus", self.cfg.container_cpus,
            "--pids-limit", "64",
            "--stop-timeout", "3",
            "--tmpfs", "/tmp:rw,size=8m,noexec",
            "-v", f"{snapshot_dir}:/snapshot:ro",
            self.cfg.runner_image,
            "/snapshot/task.json",
        ]

    def run_snapshot_task(self, snapshot_dir: Path, timeout: float | None = None) -> dict[str, Any]:
        """Run the executor against snapshot_dir (must contain task.json and rules/)."""
        self.check_available()
        self.ensure_image()
        name = f"rulegrow-{uuid.uuid4().hex[:12]}"
        args = ["docker", *self._docker_run_args(snapshot_dir, name)]
        effective_timeout = float(timeout or self.cfg.docker_timeout_seconds)
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=effective_timeout, check=False)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=10, check=False)
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False)
            raise SandboxError(f"沙箱执行超时（>{effective_timeout:.0f}s），容器已终止，旧版本继续可用") from None
        if proc.returncode != 0:
            raise SandboxError(f"容器执行失败（exit={proc.returncode}）：{proc.stderr.strip()[:400]}")
        return parse_result(proc.stdout.strip(), self.cfg)
