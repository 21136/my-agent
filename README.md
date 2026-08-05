# my-agent

个人用、可自我进化的本地 Agent。**`evolve/` 以 Git 为真源**；`data/evolve_log.jsonl` 记引用与审计，不代替版本回滚。

**稳定化**：Phase 18 已解冻（[STABILIZATION.md](docs/STABILIZATION.md)）· **当前进度以 [docs/MAP.md](docs/MAP.md) §2 为准**（2026-08：Phase 42～46、unified 壳、配方/验证/工具工坊等）

> 下文「建设顺序 / 当前状态」中 Phase 1～6 为**历史快照**；新会话请先读 **MAP + TASKS**，勿以本节版本号判断代码现状。

## 快速开始

```powershell
# 前置：Python 3.12+、Node.js LTS（桌面壳）
pip install -r requirements.txt

# CLI 对话 REPL（仓库根目录；Windows 请用 start.bat 强制 UTF-8，见 docs/DESKTOP.md §3.8.1）
.\start.bat
# 裸跑可能在 CP936 控制台乱码：
#   cd agent-core
#   python main.py

# 桌面壳（默认入口；首次自动 npm install）
.\start-desktop.bat
# 或手动：
#   cd desktop
#   npm install
#   npm run dev

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
| 0 | **前置** | `python --version` → **3.12+**；桌面还要 Node.js LTS（`npm -v`）。过旧 Python：先升级，勿指望深处报错清晰 |
| 1 | **clone** | `git clone <private-url> my-agent` → `cd my-agent` |
| 2 | **pip** | `pip install -r requirements.txt`（须含 **`httpx`** + **`websockets`**） |
| 3 | **密钥** | 设置本机 `LLM_API_KEY`（对话 / `web_search` / audit）；无 key 仍可跑无 LLM 的 `tool` CLI |
| 4a | **桌面首启**（推荐） | 双击 / 运行 `.\start-desktop.bat` → 缺 `desktop/node_modules` 时自动 `npm install` → Electron + sidecar |
| 4b | **CLI 首启** | `.\start.bat`（强制 UTF-8；勿裸跑 `python …\main.py` 除非已设 DOC-08 环境） |
| 5 | **就绪** | sidecar stdout / 日志含 `{"ready": true, ...}`；桌面顶栏 WS 就绪；CLI 出现 REPL 提示 |

**可选**：换机后把备份的 `data/`（及 `workspace/`）拷回仓库根下同名路径，再启动。

**常见失败**：

| 现象 | 处理 |
|------|------|
| `Python not found` / `npm not found` | 安装并加入 PATH；重开终端 |
| `ModuleNotFoundError: httpx` / `websockets` | 在仓库根重跑 `pip install -r requirements.txt` |
| 桌面首启卡住在 npm | 进 `desktop/` 手动 `npm.cmd install`（Windows） |
| 端口 8765 占用 / 双开 | 见 S-52：关旧实例或托盘「接管」；勿多开抢端口 |
| 控制台中文乱码 | 用 `start.bat` / 桌面 spawn（DOC-08 · DESKTOP §3.8.1） |

---

## Git 回滚习惯（进化层）

> 与 [docs/GOVERNANCE.md](docs/GOVERNANCE.md) §9.3、§10 一致。**内容回滚靠 Git**；log 只追加事件，不删历史。

### 何时 commit

| 时机 | 建议 |
|------|------|
| **`proposals accept` 成功** | 立刻提交本次写入的 `evolve/` |
| **`my-agent review` / `audit` 后** | 若你根据清单 **手改**了 memory / prompt / tool（archive、suspect 恢复等） |
| **手改 `evolve/`** | 任何直接编辑 `_index.toml`、`prompts/`、`memories/`、`tools/` 后 |

`review` / `audit` **不会**自动改文件；只有 accept 与你的手改才需要 commit。

### 推荐命令

**accept 后（REPL 或 `proposals accept <id>`）：**

```powershell
git add evolve/
git commit -m "evolve: accept <proposal_id>"
```

**治理审查后批量整理：**

```powershell
git add evolve/
git commit -m "evolve: archive never-used memories"
# 或
git commit -m "evolve: fix coding prompt per audit"
```

**只看改了什么：**

```powershell
git status
git diff evolve/
```

### 误接受 / 改错：用 Git 恢复

1. 查历史（把 `<path>` 换成具体文件，如 `evolve/memories/coding/foo.md`）：

   ```powershell
   git log --oneline -- evolve/<path>
   ```

2. 恢复到某一版（**只恢复该路径**，不动其它文件）：

   ```powershell
   git checkout <hash> -- evolve/<path>
   ```

3. （可选）在 `data/evolve_log.jsonl` 手工追加一行 `rollback_noted`，便于日后对照：

   ```json
   {"event":"rollback_noted","git_ref":"<hash>","paths":["evolve/..."],"note":"误 accept 回滚"}
   ```

**不做（M4）**：自动把 log 与 commit 绑定、一键 `my-agent rollback`。裁决权在人。

### 个人节奏（参考）

| 频率 | 动作 |
|------|------|
| 每次 accept | `git commit`（约 30 秒） |
| 每 2 周或 evolve 条目 +5 | `my-agent review` |
| 感觉 prompt / memory 规则打架 | `my-agent audit` |
| 出问题时 | `git checkout` + 可选 `rollback_noted` |

CLI 在 **accept**、**review**、**audit** 结束时会打印简短 Git 提示（`governance/git_hints.py`）。

---

## 文档索引

| 文档 | 用途 |
|------|------|
| [**docs/MAP.md**](docs/MAP.md) | **项目地图**（新会话先读：目录、模块、进度、验收命令） |
| [docs/TASKS.md](docs/TASKS.md) | **任务清单**（全 Phase；DOC-04 准入） |
| [docs/ARCHIVED-TOOLS.md](docs/ARCHIVED-TOOLS.md) | **已归档 evolved 工具**（替代路径 · T-4310） |
| [docs/PROJECT-MODE.md](docs/PROJECT-MODE.md) | 项目模式 · ENV · 构建纪律 |
| [docs/SHELL-CHANNEL.md](docs/SHELL-CHANNEL.md) | `run_command` 执行面 · IT-103 |
| [docs/PROJECT.md](docs/PROJECT.md) | 项目总览 |
| [docs/LAYERS.md](docs/LAYERS.md) | **先 tool 后 skill** |
| [docs/RUNTIME.md](docs/RUNTIME.md) | 对话层：续接、DeepSeek、digest |
| [docs/EVOLVE.md](docs/EVOLVE.md) | proposal、防重复、接受路由 |
| [docs/GOVERNANCE.md](docs/GOVERNANCE.md) | review、audit、suspect、`ReviewReport` |
| [docs/MEMORY.md](docs/MEMORY.md) | 三件套 + `evolve/_index.toml` |
| [docs/TOOLS.md](docs/TOOLS.md) | 6 Builtin + 主题 tools |
| [docs/STABILIZATION.md](docs/STABILIZATION.md) | Phase 18 稳定化（DOC-01～09 · smoke · Gate） |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 文档版本历史 |

## 建设顺序（历史 · Phase 1～6）

> **现行排期**：见 [docs/MAP.md](docs/MAP.md) §2 · [docs/TASKS.md](docs/TASKS.md)「下一焦点」。

```
TOOLS.md 评审
  → Phase 1～6：builtin / LLM / 记忆 / proposal / evolved / 治理   done（2026-Q2 基线）
  → Phase 18：稳定化 · 解冻                                      done
  → Phase 22～46：项目侧栏 · 工具目录 · Harness · 配方 · 验证 · 工坊 …  见 MAP
```

## 当前状态（摘要 · 2026-08-05）

| 项 | 状态 |
|----|------|
| **真源** | [MAP.md](docs/MAP.md) §2 + [TASKS.md](docs/TASKS.md) |
| **默认入口** | `start-desktop.bat`（unified 壳 · `desktop/src/shells/unified/`） |
| **执行面** | `run_command` / `run_service`；`npm_exec`/`mvn_exec`/`repl` 等 **archived** |
| **近期 done** | Phase 43 配方（T-4307 run_command only）· 46 工具工坊 M1 · T-4310/4311 archived 清理 |
| **下一焦点** | Phase 24 收尾 · Phase 46 S-461 · WORKBENCH M1 等（见 MAP §2.2） |

<details>
<summary>历史快照（Phase 6 · 勿作现状依据）</summary>

| 项 | 状态 |
|----|------|
| 代码阶段 | Phase 6 M4 + T-006（T-601～T-604） |
| 下一步 | 可选 T-601b / T-605 skill |

</details>
