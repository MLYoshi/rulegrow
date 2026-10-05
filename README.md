# RuleGrow

一个被问倒后会重写自己的 AI。

RuleGrow 是一个 CLI 对话系统，把 AI 生成的对话逻辑固化为**可读、可检查、可迭代的 Python 条件规则**：

- 已掌握的问题：在 Docker 沙箱中运行已有规则，**不调用模型**。
- 未掌握的问题：调用 AI（OpenRouter）生成**完整候选规则集**。
- 候选代码必须通过 Schema、路径、AST Linter、Docker 沙箱测试（当前输入 / 历史回归 / 未知输入守卫）和质量评估，才能**原子发布**。
- 用户可以查看版本、差异、失败原因并回滚。

> **AI 可以提出规则，但不能决定自己是否合格。**
> Linter 负责限制代码结构，Docker 负责隔离执行影响，测试和评估负责发现行为问题，发布权始终属于框架。

## 前置依赖

- Python ≥ 3.10
- **Docker**（必须）：所有规则执行（正式运行与候选测试）都在固定 Docker 容器中进行。Docker 不可用时 RuleGrow 会明确报错，**不会回退到主进程执行**。

```bash
# 本地开发
python3.12 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env   # 填入你的 OpenRouter 配置
```

## 配置（.env）

```dotenv
OPENROUTER_API_KEY=your_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=<OpenRouter 实际模型 ID>
```

模型 ID 必须填写 OpenRouter 上的真实 ID（如 `deepseek/deepseek-chat` 等），不能凭名称猜测。可选预算配置见 `rulegrow.yaml`（`max_auto_attempts`、`max_job_seconds` 等，默认 8 次 / 180 秒）。

## 使用

```bash
rulegrow chat             # 开始对话（输入 /quit 退出，/usage 查看模型用量）
rulegrow lint             # 检查当前已发布规则
rulegrow history          # 查看版本历史
rulegrow diff v1 v2       # 比较两个版本
rulegrow rollback v1      # 校验后回滚到 v1
rulegrow jobs             # 列出进化任务
rulegrow resume <job-id>  # 继续暂停的任务
```

首次运行会自动构建固定沙箱镜像 `rulegrow-runner:latest`（基于 `Dockerfile.runner`，只包含固定 Python 与固定执行器）。

## 规则协议

每个规则文件只有一个函数：

```python
def handle(text: str) -> str | bool:
    if text.strip() in ("你好", "hello"):
        return "你好！有什么我可以帮你的？"
    return False
```

- 返回非空字符串 = 命中；返回 `False` = 未命中；返回 `True`/`None`/空串/其他类型 = 协议违规。
- 语法白名单：`if/elif/else`、局部赋值、字符串/布尔/有限集合字面量、`and/or/not`、相等与成员判断、批准的字符串方法、`+` 拼接。循环、推导式、import、f-string、文件/网络/环境变量操作等一律禁止（详见 `src/rulegrow/lint/ast_rules.py` 的 CODE001–CODE015）。
- MVP 不支持正则表达式。

## 费用说明

- 命中已有规则时**零模型调用**。
- 模型只在首次生成、未命中后的进化、检查失败后的修复（以及可选的语义评审，默认关闭）时调用。一次进化任务最多 `max_auto_attempts` 次调用，预算耗尽任务暂停，不会无限消耗。

## 隐私

- 未命中的输入会发送给 OpenRouter；对话与测试记录默认保存在本地 `.rulegrow/` 目录。
- API Key 不会进入生成代码、Docker 容器、版本快照或普通日志。
- 删除 `.rulegrow/` 即删除全部本地历史。

## 沙箱安全边界（重要）

Docker 沙箱每次执行均：禁用网络、非 root 用户、只读根文件系统、丢弃全部 Linux capabilities、限制 CPU/内存/进程数、只挂载本次规则版本的只读快照、设置总超时、结构化 JSON 返回。AI 只能提交规则文件，不能修改执行器、镜像或启动参数。

**Docker 是本地 CLI 的务实隔离措施，不是绝对安全保证。** 本项目不承诺防御恶意宿主机用户、Docker 内核漏洞或在线多租户攻击。若转为在线服务，必须重新评估隔离架构。

## 项目结构

```text
src/rulegrow/
├── cli.py                 # CLI 入口
├── runtime.py             # 对话主循环（调度优先，未命中才进化）
├── dispatcher.py          # 在 Docker 中调度当前已发布规则
├── evolution.py           # 进化流水线与预算控制
├── config.py              # .env / rulegrow.yaml 配置
├── providers/openrouter.py
├── lint/                  # AST Linter（白名单）+ Schema/路径校验 + 诊断
├── validation/            # 回归 / 守卫 / 对话质量评估
├── sandbox/               # Docker 运行器 + 任务/结果协议
└── storage/versions.py    # 版本快照、diff、发布、回滚
runner/entrypoint.py       # 容器内固定执行器
tests/                     # 单元测试 + 进化流水线集成测试（需 Docker）
```

## 开发

```bash
.venv/bin/python -m unittest discover -s tests   # 运行测试
```

## License

MIT
