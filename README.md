# my-agent

个人用、可自我进化的**本地项目开发 Agent**。**`evolve/` 以 Git 为真源**；`data/evolve_log.jsonl` 记引用与审计，不代替版本回滚。

**稳定化**：Phase 18 已解冻（[STABILIZATION.md](docs/STABILIZATION.md))  
**进度真源**：[docs/MAP.md](docs/MAP.md) §2 + [docs/TASKS.md](docs/TASKS.md)（勿以本 README 判断代码现状）

**当前摘要（2026-08-08）**

| 项 | 说明 |
|----|------|
| **产品定位** | 单机项目工作台（[LOCAL-DELIVERY-MODEL](docs/LOCAL-DELIVERY-MODEL.md) v0.3.3） |
| **默认入口** | `start-desktop.bat` → unified 壳（`desktop/src/shells/unified/`） |
| **Terminal** | `start-terminal.bat` → Claude 式 cwd agent（[TERMINAL-MODE](docs/TERMINAL-MODE.md) v0.2.0） |
| **Builtin** | 12 个（`read_file` · `grep` · `glob_file_search` · **`codebase_search`** · `run_evolved` · 子代理等） |
| **路线图** | [ROADMAP-PACK-1245](docs/ROADMAP-PACK-1245.md) **Pack 1/2/4/5/6 M0 done** |
| **另排** | S-572 Terminal smoke；S-441/461/490/500 手工 smoke |

---

## 快速开始

```powershell
# 前置：Python 3.12+、Node.js LTS（桌面壳）
pip install -r requirements.txt

# CLI 对话 REPL（Windows 请用 start.bat 强制 UTF-8，见 docs/DESKTOP.md §3.8.1）
.\start.bat

# Terminal 狂野模式（先 cd 进仓库，Claude 式 cwd agent）
.\start-terminal.bat

# 桌面壳（默认入口；首次自动 npm install）
.\start-desktop.bat

# 无 LLM 调工具
python my-agent tool list
python my-agent tool run grep --json '{\"pattern\":\"Phase\",\"path\":\"docs/MAP.md\",\"max_results\":2}' -y

# 治理
python my-agent review
python my-agent audit --topic coding
```

环境变量：`LLM_API_KEY`（对话 / audit）；`MY_AGENT_FEEDBACK_ON_EXIT=1`（exit 时可选反馈）。详见 [docs/MAP.md](docs/MAP.md) §8。

### DOC-09 · Fresh bootstrap（新机器 / 新 clone · S-51）

> **定稿**：T-1806-doc-09 · 验收对照 [STABILIZATION.md](docs/STABILIZATION.md) §3.11.1。  
> `data/` **不进 Git**（DOC-06）；换机请另拷备份，见 [STABILIZATION.md](docs/STABILIZATION.md) §3.9.4。

| 步 | 动作 | 验收 |
|----|------|------|
| 0 | **前置** | `python --version` → **3.12+**；桌面还要 Node.js LTS（`npm -v`） |
| 1 | **clone** | `git clone <private-url> my-agent` → `cd my-agent` |
| 2 | **pip** | `pip install -r requirements.txt`（须含 **`httpx`** + **`websockets`**） |
| 3 | **密钥** | 设置本机 `LLM_API_KEY`；无 key 仍可跑无 LLM 的 `tool` CLI |
| 4a | **桌面首启**（推荐） | `.\start-desktop.bat` → 自动 `npm install` → Electron + sidecar |
| 4b | **CLI 首启** | `.\start.bat` |
| 5 | **就绪** | sidecar 日志含 `{"ready": true, ...}`；桌面 WS 就绪 |

**可选**：换机后把备份的 `data/`、`workspace/` 拷回仓库根下同名路径。

**常见失败**：

| 现象 | 处理 |
|------|------|
| `Python not found` / `npm not found` | 安装并加入 PATH |
| `ModuleNotFoundError: httpx` / `websockets` | 重跑 `pip install -r requirements.txt` |
| 桌面首启卡住在 npm | 进 `desktop/` 手动 `npm.cmd install` |
| 端口 8765 占用 | 关旧实例或托盘「接管」 |
| 控制台中文乱码 | 用 `start.bat` / 桌面 spawn（DOC-08） |

---

## Git 回滚习惯（进化层）

> 与 [docs/GOVERNANCE.md](docs/GOVERNANCE.md) §9.3、§10 一致。**内容回滚靠 Git**；log 只追加事件。

| 时机 | 建议 |
|------|------|
| **`proposals accept` 成功** | 立刻 `git commit` 本次 `evolve/` |
| **review / audit 后手改** | `git add evolve/` + commit |
| **误接受** | `git log` → `git checkout <hash> -- evolve/<path>` |

CLI 在 accept / review / audit 结束时会打印简短 Git 提示（`governance/git_hints.py`）。细节见 [GOVERNANCE.md](docs/GOVERNANCE.md)。

---

## 文档索引

| 文档 | 用途 |
|------|------|
| [**docs/MAP.md**](docs/MAP.md) | **项目地图**（新会话先读） |
| [**docs/TASKS.md**](docs/TASKS.md) | 任务清单 · DOC-04 准入 |
| [**docs/ROADMAP-PACK-1245.md**](docs/ROADMAP-PACK-1245.md) | Pack 1/2/4/5/6 路线图 |
| [docs/LOCAL-DELIVERY-MODEL.md](docs/LOCAL-DELIVERY-MODEL.md) | 本地交付模型（LDM） |
| [docs/CODEBASE-SEARCH.md](docs/CODEBASE-SEARCH.md) | `codebase_search` 语义找代码 |
| [docs/ASYNC-ORCHESTRATION.md](docs/ASYNC-ORCHESTRATION.md) | 起服链同回合编排 |
| [docs/PROJECT-MODE.md](docs/PROJECT-MODE.md) | 项目模式 · ENV |
| [docs/SHELL-CHANNEL.md](docs/SHELL-CHANNEL.md) | `run_command` 执行面 |
| [docs/TOOLS.md](docs/TOOLS.md) | Builtin + evolved 工具 |
| [docs/TERMINAL-MODE.md](docs/TERMINAL-MODE.md) | **Terminal 狂野模式**（与 Desktop 会话分离 · `start-terminal`） |
| [docs/STABILIZATION.md](docs/STABILIZATION.md) | 稳定化 · smoke · Gate |
| [docs/ARCHIVED-TOOLS.md](docs/ARCHIVED-TOOLS.md) | 已归档 evolved 工具 |

<details>
<summary>建设顺序（历史 · 勿作现状依据）</summary>

```
Phase 1～6 基线 → Phase 18 稳定化解冻
→ Phase 22～50：侧栏 · Harness · 配方 · 验证 · 编排 …
→ Pack 1245（2026-08）：收口 · 路由 · phase_key · codebase_search · 起服 G13
```

完整 Phase 表见 [MAP.md](docs/MAP.md) §2。

</details>
