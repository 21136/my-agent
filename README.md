# my-agent

个人用、可自我进化的本地 Agent。**`evolve/` 以 Git 为真源**；`data/evolve_log.jsonl` 记引用与审计，不代替版本回滚。

**当前版本：0.2.14** · **Phase 6 M4**（`T-601`～`T-604` done）

## 快速开始

```powershell
# Python 3.12+
pip install -r requirements.txt

# 对话 REPL（仓库根目录）
.\start.bat
# 或
cd agent-core
python main.py

# 无 LLM 调工具
python my-agent tool list
python my-agent tool run grep --json '{\"pattern\":\"Phase\",\"path\":\"docs/MAP.md\",\"max_results\":2}' -y

# 治理
python my-agent review
python my-agent audit --topic coding
```

环境变量：`LLM_API_KEY`（对话 / audit）；`MY_AGENT_FEEDBACK_ON_EXIT=1`（exit 时可选反馈）。详见 [docs/MAP.md](docs/MAP.md) §8。

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
| [docs/TASKS.md](docs/TASKS.md) | **任务清单**（T-001～T-906） |
| [docs/PROJECT.md](docs/PROJECT.md) | 项目总览 |
| [docs/LAYERS.md](docs/LAYERS.md) | **先 tool 后 skill** |
| [docs/RUNTIME.md](docs/RUNTIME.md) | 对话层：续接、DeepSeek、digest |
| [docs/EVOLVE.md](docs/EVOLVE.md) | proposal、防重复、接受路由 |
| [docs/GOVERNANCE.md](docs/GOVERNANCE.md) | review、audit、suspect、`ReviewReport` |
| [docs/MEMORY.md](docs/MEMORY.md) | 三件套 + `evolve/_index.toml` |
| [docs/TOOLS.md](docs/TOOLS.md) | 6 Builtin + 主题 tools |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 文档版本历史 |

## 建设顺序（一页纸）

```
TOOLS.md 评审
  → Phase 1：builtin + evolved 执行器（无 LLM）     done
  → Phase 2：CLI + LLM 调 tool                     done
  → Phase 3：记忆三件套 + 主题路由                 done
  → Phase 4：proposal（memory/tool）               done
  → Phase 5：真实 evolved tool                     done
  → Phase 6：治理 + skill（可选）                  进行中
```

## 当前状态

| 项 | 状态 |
|----|------|
| 代码阶段 | Phase 6 M4 + **T-006**（`T-601`～`T-604`、GitHub 私有远端） |
| 下一步 | 可选 `T-601b` / `T-605` skill；日常 `git commit` 策展 `evolve/` |
| Skill | **M1 不做**；M4 可选 |

详见 [docs/MAP.md](docs/MAP.md) §2。
