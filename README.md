# my-agent

个人用、可自我进化的**本地 Agent**。Git 为真源；`evolve/` 存 prompt / memory / tool；`data/` 存会话与审计（不进 Git）。

**进度真源**：[docs/MAP.md](docs/MAP.md) §2 + [docs/TASKS.md](docs/TASKS.md)（勿以本 README 判断代码是否已落地）

**稳定化**：Phase 18 已解冻（[STABILIZATION.md](docs/STABILIZATION.md)）

---

## 三条产品线

| 入口 | 用途 | 文档 |
|------|------|------|
| **`start-desktop.bat`**（默认） | **主战场** · 只为写 workspace 项目 · unified **project** 纪律 · 教科书流程（闸门+证据） | [DESKTOP-TEXTBOOK-FLOW.md](docs/DESKTOP-TEXTBOOK-FLOW.md) · [DESKTOP.md](docs/DESKTOP.md) |
| **`start-terminal.bat`** | **狂野挎包** · cwd agent · Ink · auto plan-execute · **功能冻结**（维护/P0） | [TERMINAL-MODE.md](docs/TERMINAL-MODE.md) **v0.3.2** |
| **`start.bat`** | 轻量 CLI REPL（备用） | [RUNTIME.md](docs/RUNTIME.md) |

Desktop 与 Terminal **会话分离**（`meta.harness` 终身不可变）；换界面只能 exit 后在另一入口续接。  
**定调**：Desktop = 产品开发焦点；Terminal = 卖点维护态，**独立入口**，不进 Desktop 流程轨。

---

## 当前摘要（2026-08-14）

| 项 | 说明 |
|----|------|
| **产品定调** | [DESKTOP-TEXTBOOK-FLOW](docs/DESKTOP-TEXTBOOK-FLOW.md) — Desktop 主战场 · Terminal 冻结维护 |
| **交付模型** | [LOCAL-DELIVERY-MODEL](docs/LOCAL-DELIVERY-MODEL.md) v0.3.3 · Pack 1245 M0 done |
| **桌面 UI** | `desktop/src/shells/unified/`（`default` / `project` / `night`）+ 独立 `pet` 窗 |
| **Terminal UI** | Ink **v0.3.2**（`terminal-ui/`）· 默认 `MY_AGENT_TERMINAL_UI=ink` · legacy Bottom TUI 可回退 |
| **Terminal 内核** | TM-24～28 自动 plan-execute · effective root 内免 confirm · 与 Desktop 无 `project_id` |
| **Builtin** | 12 个（核心 7 + 编排 5：`explore` · `plan_partner` · `deliverable_review` 等） |
| **Evolved** | `evolve/tools/**` 经 `run_evolved` 调用；写路径默认 agent root |
| **下一手工** | S-580 Desktop 北极星路径 · S-576 Terminal（frozen 留痕） |

---

## 快速开始

```powershell
# 前置：Python 3.12+；桌面 / Terminal Ink 还要 Node.js 20+（LTS 即可）
pip install -r requirements.txt

# 桌面（默认；首次自动 npm install）
.\start-desktop.bat

# Terminal：先 cd 进你的仓库（Claude 同款），再启动
cd D:\path\to\your-repo
D:\my-agent\start-terminal.bat

# CLI 备用（Windows 请用 start.bat 强制 UTF-8）
.\start.bat

# 无 LLM 调工具
python my-agent tool list
python my-agent tool run grep --json '{\"pattern\":\"Phase\",\"path\":\"docs/MAP.md\",\"max_results\":2}' -y

# 治理
python my-agent review
python my-agent audit --topic coding
```

**环境变量**：`LLM_API_KEY`（对话 / audit）；`MY_AGENT_FEEDBACK_ON_EXIT=1`（exit 时可选反馈）。更多见 [MAP.md](docs/MAP.md) §8。

**Terminal 常用开关**：

| 变量 | 说明 |
|------|------|
| `MY_AGENT_TERMINAL_UI=legacy` | 回退 prompt_toolkit Bottom TUI（Welcome 显示 v0.2.1） |
| `MY_AGENT_TERMINAL_USE_DIST=1` | 强制用 `terminal-ui/dist/` 而非 tsx 源码 |
| `TERMINAL_PLAN_CLASSIFY=0` | 关闭复杂任务的 auto-plan 分类器 |

---

## 仓库结构（简图）

```text
my-agent/
├── agent-core/          # Python 内核（agent 循环 · 工具 · 子代理 · Terminal 桥接）
├── desktop/             # Electron 桌面壳
├── terminal-ui/         # Terminal Ink TUI（React + Ink）
├── evolve/              # 进化层：prompt / memory / tool / scaffold
├── docs/                # 设计真源（MAP · TASKS · TERMINAL-MODE …）
├── tools/file-sentinel/ # 源码截断监控（start-*.bat 自动拉起）
├── start-desktop.bat    # 默认入口
├── start-terminal.bat   # Terminal 入口
└── start.bat            # CLI 入口
```

---

## Fresh bootstrap（新机器 / 新 clone）

> 验收对照 [STABILIZATION.md](docs/STABILIZATION.md) §3.11.1 · `data/` **不进 Git**

| 步 | 动作 | 验收 |
|----|------|------|
| 0 | **前置** | `python --version` → 3.12+；`node -v` / `npm -v`（桌面与 Terminal） |
| 1 | **clone** | `git clone <private-url> my-agent` → `cd my-agent` |
| 2 | **pip** | `pip install -r requirements.txt`（`httpx` + `websockets`） |
| 3 | **密钥** | 设置 `LLM_API_KEY`；无 key 仍可跑 `tool` CLI |
| 4a | **桌面** | `.\start-desktop.bat` → 自动 `desktop/npm install` |
| 4b | **Terminal** | `.\start-terminal.bat` → 自动 `terminal-ui/npm install` |
| 4c | **CLI** | `.\start.bat` |
| 5 | **就绪** | 桌面：sidecar `{"ready": true}`；Terminal：Welcome **v0.3.2** |

换机可把备份的 `data/`、`workspace/` 拷回仓库根下同名路径。

**常见失败**：

| 现象 | 处理 |
|------|------|
| `Python not found` / `npm not found` | 安装并加入 PATH |
| `ModuleNotFoundError: httpx` | 重跑 `pip install -r requirements.txt` |
| 桌面首启卡在 npm | `cd desktop` → `npm install` |
| Terminal 黑屏或显示 v0.2.1 | 确认 `terminal-ui` 已 `npm install`；或设 `MY_AGENT_TERMINAL_UI=legacy` 排查 |
| 端口 8765 占用 | 关旧实例或托盘「接管」 |
| 控制台中文乱码 | 用 `start.bat` / 桌面 spawn（DOC-08） |

---

## Git 回滚习惯（进化层）

> 与 [GOVERNANCE.md](docs/GOVERNANCE.md) §9.3 一致。**内容回滚靠 Git**；`evolve_log.jsonl` 只追加审计。

| 时机 | 建议 |
|------|------|
| **`proposals accept` 成功** | 立刻 `git commit` 本次 `evolve/` |
| **review / audit 后手改** | `git add evolve/` + commit |
| **误接受** | `git log` → `git checkout <hash> -- evolve/<path>` |

---

## 文档索引

| 文档 | 用途 |
|------|------|
| [**docs/MAP.md**](docs/MAP.md) | **项目地图**（新会话先读） |
| [**docs/TASKS.md**](docs/TASKS.md) | 任务清单 · DOC-04 准入 |
| [**docs/TERMINAL-MODE.md**](docs/TERMINAL-MODE.md) | Terminal 狂野模式 · Ink UI · auto plan-execute |
| [**docs/CHANGELOG.md**](docs/CHANGELOG.md) | 版本与变更记录 |
| [docs/ROADMAP-PACK-1245.md](docs/ROADMAP-PACK-1245.md) | Pack 1/2/4/5/6 路线图 |
| [docs/LOCAL-DELIVERY-MODEL.md](docs/LOCAL-DELIVERY-MODEL.md) | 本地交付模型（LDM） |
| [docs/PROJECT-MODE.md](docs/PROJECT-MODE.md) | 项目模式 · ENV |
| [docs/TOOLS.md](docs/TOOLS.md) | Builtin + evolved 工具 |
| [docs/DESKTOP.md](docs/DESKTOP.md) | 桌面壳 · unified 工作台 |
| [docs/STABILIZATION.md](docs/STABILIZATION.md) | 稳定化 · smoke · Gate |

<details>
<summary>建设顺序（历史 · 勿作现状依据）</summary>

```
Phase 1～6 基线 → Phase 18 稳定化解冻
→ Phase 22～50：侧栏 · Harness · 配方 · 验证 · 编排 …
→ Pack 1245（2026-08）：收口 · 路由 · phase_key · codebase_search
→ Phase 57（2026-08）：Terminal legacy TUI + Ink UI + auto plan-execute
```

完整 Phase 表见 [MAP.md](docs/MAP.md) §2。

</details>
