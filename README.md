# my-agent

面向软件项目的本地 AI Agent 工作台。以对话为入口，协作修改项目文档和代码，并记录可核实的验证结果。

## 选对分支

- **`main`** 是默认分支，保留较早的源码基线。下面的启动命令适用于本分支已有的桌面、Terminal 和 CLI 入口。
- **[`codex/self-hosting-e2e`](https://github.com/21136/my-agent/tree/codex/self-hosting-e2e)** 包含本轮开发更新和对应文档。Windows 一键自动改造、选定同步、聊天回放和实验窗口退出修复，请切换到该分支试用。**不是 `main` 已包含这些更新，也不代表已发布正式版本。**

查看开发分支的[文档导航](https://github.com/21136/my-agent/blob/codex/self-hosting-e2e/docs/README.md)、[Windows 一键自动改造](https://github.com/21136/my-agent/blob/codex/self-hosting-e2e/docs/SELF-EVOLUTION-AUTO-EXFAT.md)和[稳定／实验区同步指南](https://github.com/21136/my-agent/blob/codex/self-hosting-e2e/docs/SELECTED-SYNC-WORKER.md)。旧阶段表和任务清单不再作为这条开发线的使用入口。

## 本地启动（Windows）

准备 Python 3.12+；使用桌面或 Terminal 时另需 Node.js 和 npm。在仓库根目录运行：

```powershell
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start-desktop.bat
```

| 入口 | 用途 |
|---|---|
| `start-desktop.bat` | Electron 桌面工作台 |
| `start-terminal.bat` | Terminal Ink 界面，独立会话入口 |
| `start.bat` | 备用 CLI REPL |

模型与凭据配置见本分支的[模型路由](docs/LLM-ROUTING.md)和[运行时说明](docs/RUNTIME.md)。双车道概念见[稳定／实验区](docs/SANDBOX-EVOLUTION.md)。请勿向 Git 提交密钥、聊天记录或本地运行数据。
