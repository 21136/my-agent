# my-agent

面向真实软件项目的本地 Agent 工作台。聊天是入口，项目文件、代码与可核实的验证结果是工作产物。

- **开发前先读**：[AGENTS.md](AGENTS.md) → [当前源码地图](docs/MAP.md)。
- **文档入口**：[文档导航](docs/README.md)；按需阅读，不以旧计划或历史任务表判断当前能力。
- **产品范围**：[PRODUCTION-PROJECT-MVP](docs/PRODUCTION-PROJECT-MVP.md)。

## 当前入口

| 入口 | 用途 |
|---|---|
| `start-desktop.bat` | 主要工作台：Electron + unified 聊天/项目界面，独立伴侣窗 pet |
| `start-terminal.bat` | Terminal Ink UI，独立会话入口 |
| `start.bat` | 备用 CLI REPL |

Desktop 与 Terminal 的会话边界不同，不把切换界面当成同一个运行过程。

## 当前实现边界

- 项目核心制品：`PROJECT.md`、`DESIGN.md`、`VERIFY.md`。旧任务队列、计划代理、进度门和狂奔调度已下线，不再要求按历史 Phase 或 `docs/TASKS.md` 推进。
- **Goal Runtime P0**：已接入动手回合，持久化目标状态、累计执行预算并记录验收结果；不是旧狂奔功能的恢复，也不代表无人值守全流程已验收。
- **双车道**：稳定根目录 + `.worktrees/sandbox/` 实验树，支持状态检查、合入与同步；见 [SANDBOX-EVOLUTION](docs/SANDBOX-EVOLUTION.md)。
- **managed 原生入口**与普通 Agent 工作台不同：记录页的创建/保存/重开验证不等于完整聊天、模型和工具能力验收。
- 当前分支、提交、未提交改动和验证结果以现场证据为准，不在 README 维护另一份进度表。

## 快速开始

前置：Python 3.12+；Desktop / Terminal Ink 还需要 Node.js 20+。

```powershell
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 桌面主入口
.\start-desktop.bat

# 或使用 Terminal / CLI
.\start-terminal.bat
.\start.bat
```

模型配置见 [LLM-ROUTING](docs/LLM-ROUTING.md) 与 [RUNTIME](docs/RUNTIME.md)。凭据保存在本机运行配置中，不要提交或粘贴密钥。

## 仓库结构

```text
agent-core/          Python Agent、API、权限、工具、目标与发布运行时
desktop/            Electron 主进程与 unified / pet 界面
terminal-ui/        Terminal Ink UI
evolve/             提示词、Skill、工具与记忆等进化层
docs/               当前专题说明与源码导航
.worktrees/sandbox/  实验车道，合入前不写稳定树
data/               本机运行数据（默认不进 Git）
workspace/          用户项目与产物
```

## 定向验证

先确认解释器和依赖：

```powershell
.venv\Scripts\python.exe -c "import sys, pytest; print(sys.executable)"

# 示例：目标状态模块的定向回归，不是全量测试
.venv\Scripts\python.exe -m pytest agent-core/tests/test_goal_runtime.py -q

# Desktop 定向测试示例
cd desktop
node --test tests/shell-dev-adoption.test.ts
```

根据修改对象选择测试，不为了“完成”无理由重跑全部套件。真实模型调用、正式环境启动和发布另行确认范围。

## 工作区纪律

- 保留无关既有修改，不自行 `reset`、`clean`、暂存、提交或推送。
- 实验工作按 [AGENTS.md](AGENTS.md) 写入 sandbox；源码合入与切换预览地址不是一回事。
- 测试通过、构建完成、隔离运行与客户认可分别报告，不互相替代。
- 过期设计、任务历史与现场交接不再作为新会话必读材料；需要追溯时查 Git 历史。
