# CLI ↔ 桌面元命令 Parity（DOC-02 / IT-38）

> 版本 **0.3.3** · 2026-08-07 · **定稿**（DOC-02 / T-1806-doc-02 · **T-5707 Terminal 绕行**）  
> Phase 18 · [STABILIZATION.md](./STABILIZATION.md) §8 · 审计 **T-1808-01～05**（全集 · 桌面等价 · 绕行 · IT-38 · IT-11）  
> 父清单：[STABILIZATION-TASKS.md](./STABILIZATION-TASKS.md) M1-H · M2-G

## 0. DOC-02 定稿状态

| 项 | 状态 |
|----|------|
| **T-1808-01** 元命令全集 ≥15 族 | **done** · §1 共 **17** 族（M01～M17） |
| **T-1808-02** 桌面 WS / 侧栏 / Parity 列 | **done** · §1 主表 + §2 子命令 |
| **T-1808-03** N/A 与易混项绕行文案 | **done** · §6；无静默不支持 |
| **T-1808-04** IT-38 自动化 | **done** · `tests.test_cli_desktop_parity`（文档 ≥15 族 + M04/M05/M14） |
| **T-1808-05** / IT-11 `command` ≡ `user.message` | **done** · 见下文 IT-11 小节；会话效果一致 |
| **与 STABILIZATION §8** | 本表为审计真源；§8 能力摘要与本表一致 |

**读者入口**：先看 §1 主表 → 需要绕行文案看 §6 → 自动化见 `agent-core/tests/test_cli_desktop_parity.py`。

---

## 范围

| 项 | 说明 |
|----|------|
| **Desktop / CLI 真源** | `agent-core/main.py` · `ConversationRepl.handle_line`（约 L193–330） |
| **Terminal 真源** | `agent-core/cli_terminal.py` · `TerminalRepl.handle_line`（**无** `项目 …` · 固定 `agent`） |
| **桌面入口** | sidecar `server.py`：`user.message.text` 与 `command.name` 均送入 `handle_line`（IT-11 / T-1808-05） |
| **本表 §1** | 凡在 **Desktop/CLI** `handle_line` 内**先于** `agent.run_turn` 拦截的行，均计为元命令 |
| **§7** | Terminal harness **绕行**（非 §1 子集；见 [TERMINAL-MODE.md](./TERMINAL-MODE.md)） |
| **不含** | 纯自然语言回合（fallthrough → `agent.run_turn`）；见 §3 · §4 |
| **绕行文案** | §6（T-1808-03）；Terminal 专用 §7；N/A 与易混项须有可读说明，禁止静默不支持 |

### 桌面等价列说明（T-1808-02）

| 列 | 含义 |
|----|------|
| **桌面 WS** | 桌面触发的 WebSocket `type`；`—` 表示不经 WS 发元命令文本 |
| **侧栏 / UI** | 按钮、顶栏、设置页、聊天框等可见入口；`聊天框` = 各壳 composer 发 `user.message` |
| **Parity** | **同路径** = 与 CLI 同一 `handle_line` 分支 · **等价不同径** = 行为对齐但走专用 WS/API · **N/A** = 桌面无对等或仅 CLI |

> **惯例**：凡标 `user.message` / `command` 的项，均在聊天框键入元命令即可；`command` 目前仅 **project 侧栏「确认开工」** 程序化调用 `sendCommand("项目 确认")`（`project/index.ts`）。

### `command` vs `user.message`（IT-11 · T-1808-05）

| 项 | 约定 |
|----|------|
| **会话效果** | 同一元命令字符串 → 同一 `handle_line` → **磁盘会话状态一致**（`turn_mode` · `project_id` · `plan_status` 等） |
| **WS 事件差** | `command` 在 `_run_line` 后**总是** `emit_session_state`；`user.message` 仅在 `_repl_refreshes_session_state` 为真时刷新（`新会话` · `换主题` · `压缩`） |
| **非刷新元命令** | 如 `只聊` · `项目 新建`：`command` 多一次 `session.banner`/`history` 推送，**不改变** handle_line 结果 |
| **自动化** | `tests.test_cli_desktop_parity.CommandUserMessageEquivalenceTests` |

---

与代码分支顺序一致；先匹配者优先。

```
exit / quit
  → evolve 弱确认（evolve_offer_pending）
  → evolve 强触发
  → 新会话
  → 压缩
  → 只聊 / 动手
  → proposals
  → 探索 / 调研 / explore
  → 验收 / check
  → 注册主题
  → 托管目录
  → 短确认（项目计划门）
  → 项目
  → 主题 / 加主题 / 换主题
  → （否则）agent.run_turn
```

---

## 1. 元命令主表（17 族）

| ID | 族 | 触发（代表式） | 桌面 WS | 侧栏 / UI | Parity | 绕行 §6 |
|----|-----|----------------|---------|-----------|--------|---------|
| M01 | **退出** | `exit` · `quit` · `exit --record` · `exit --full` | — | 托盘 **退出** · 关窗（`electron/main.ts` `requestAppQuit`） | **N/A** | §6.1 |
| M02 | **evolve 弱确认** | `好` · `是的` · `写进去` …（须 `evolve_offer_pending`） | `user.message` | 聊天框（grow / daily / project / pet） | **同路径** | — |
| M03 | **evolve 强触发** | `记住` · `沉淀` · `写进evolve` … | `user.message` | 聊天框；grow 另收 `evolve.proposals` 顶栏「去处理」 | **同路径** | §6.2 |
| M04 | **新会话** | `新会话` · `new` | `user.message` · `command` | 聊天框（无顶栏/托盘专用按钮） | **同路径** | §6.3 |
| M05 | **压缩上下文** | `压缩` · `summarize` · `compact` | `user.message` · `command` | 聊天框 | **同路径** | §6.3 |
| M06 | **只聊** | `只聊` · `ask` | `user.message` · `command` | 聊天框（无模式 pill，见 `DESKTOP.md` §3.2） | **同路径** | §6.3 |
| M07 | **动手** | `动手` · `agent` | `user.message` · `command` | 聊天框 | **同路径** | §6.3 |
| M08 | **proposals** | `proposals` · `list` · `accept` · `reject` | 列表：`evolve.proposals`（连接推送）+ `user.message`；审阅：`proposal.accept` / `proposal.reject` + `user.message` | grow 顶栏「去处理」+ 展开区接受/拒绝；或聊天框 `proposals …` | **等价不同径** | §6.2 |
| M09 | **探索** | `探索 <任务>` · `调研` · `explore` | `user.message` | 聊天框 | **同路径** | §6.3 |
| M10 | **验收 / checker** | `验收 <tool>` · `check <tool>` | `user.message` | 聊天框；结果以 `notice` / 顶栏 checker 文案（grow·project） | **同路径** | §6.3 |
| M11 | **注册主题** | `注册主题 <id> …` · `register topic` | `user.message` | 聊天框（多轮 `input_fn` 经 WS 输入队列） | **同路径** | §6.3 |
| M12 | **托管目录** | `托管目录` · `host root(s)` + 子命令 | 见 §2.2 | 顶栏 **托管区** 设置（`host-settings.ts`） | **等价不同径** | §6.2 |
| M13 | **短计划确认** | `确认` · `确认开工` · `开工` … | `user.message` · `command`（`项目 确认`）· `plan.response` | project 侧栏计划卡 **确认开工** / **继续修改** | **等价不同径** | §6.2 |
| M14 | **项目** | `项目` · `project` + 子命令 | 见 §2.1 | project 侧栏项目列表 / 计划卡 / 验收卡 | **混合** | §6.2 |
| M15 | **主题（替换）** | `主题 <id> …` · `topic` | `user.message` · `command` | 聊天框 | **同路径** | §6.3 |
| M16 | **加主题** | `加主题 <id> …` | `user.message` | 聊天框 | **同路径** | §6.3 |
| M17 | **换主题** | `换主题` · `change topic(s)` | `user.message` · `command` | 聊天框（S2 确认轮次经 WS 输入队列） | **同路径** | §6.3 |

**计数**：17 族 ≥ 15（T-1808-01）。**桌面列**：T-1808-02。**绕行**：T-1808-03 §6。

CLI 启动横幅（`main.py` L168）与上表一致。

---

## 2. 子命令与别名展开

### 2.1 项目（M14）

前缀：`项目` · `project`（大小写不敏感）。

| 子命令 | 别名 | CLI | 桌面 WS | 侧栏 / UI | Parity |
|--------|------|-----|---------|-----------|--------|
| 列表 | `list` · 空前缀 | `handle_line` | `project.list` | 侧栏刷新钮 · 连接后自动 `listProjects` | **等价不同径** |
| 新建 | `new` · `create` | `handle_line` | `user.message` | 聊天框「项目 新建 \<id\>」（空列表文案指引） | **同路径** |
| 打开 | `open` | `handle_line` | `user.message`（`project.open` API 存在但桌面未接按钮） | 聊天框 | **同路径** |
| 切换 | `switch` | `handle_line` | `project.switch` | 侧栏 **我的项目** 列表项 | **等价不同径** |
| 确认 | `confirm` | `handle_line` | `command` · `plan.response` · `user.message` | 侧栏计划卡 **确认开工** | **等价不同径** |
| 状态 | `status` | `handle_line` | `project.state`（推送/刷新） | 侧栏顶栏文案 · TASKS/MAP 面板 | **等价不同径**（UI 自动展示，不必打字） |
| 验收 | `verify` | `handle_line` | `project.verify` · `user.message` | 侧栏 **运行验收**（`confirmed` 且已定义 acceptance） | **等价不同径** |

### 2.2 托管目录（M12）

前缀：`托管目录` · `host root` · `host roots`。

| 子命令 | 别名 | CLI | 桌面 WS | 侧栏 / UI | Parity |
|--------|------|-----|---------|-----------|--------|
| 列表 | `list` · 空前缀 | `handle_line` | `host_scope.list` | 托管区设置页列表 | **等价不同径** |
| 添加 | `add` | `handle_line` | `host_scope.add` · `host_scope.wizard` | 设置页添加 / 首次向导 | **等价不同径** |
| 删除 | `remove` · `del` | `handle_line` | `host_scope.remove` | 设置页删除 | **等价不同径** |
| 写开关 | `write` | `handle_line` | `host_scope.write` · `host_scope.repath` | 设置页开写 / 换文件夹 | **等价不同径** |

数据文件均为 `data/host_scope.json`（与 CLI 共用）。

### 2.3 短计划确认（M13）

在 **`active_shell=project`** 且 **`project_plan_status` 为 `draft` 或 `plan_dirty`** 时，下列短语映射为 `项目 确认`：

`确认` · `确认开工` · `确认计划` · `开工` · `开始` · `开始吧` · `可以开工` · `好的` · `好的确认`

| 入口 | 桌面 WS | 侧栏 / UI |
|------|---------|-----------|
| 聊天短语 | `user.message` | 聊天框 |
| 侧栏按钮 | `plan.response`（有 `plan.request` 叠加时）或 `command` `项目 确认` | 计划卡 **确认开工** |

### 2.4 evolve 弱确认全集（M02）

`写进去` · `好的` · `好` · `要` · `对` · `是的` · `是` · `行` · `yes` · `y`（及 `好的，` / `好，` 前缀变体）— 桌面均为 **聊天框** `user.message`。

### 2.5 evolve 强触发全集（M03）

`记住这个` · `写进 evolve` · `写进evolve` · `以后都这样` · `沉淀` · `记住` — 桌面均为 **聊天框** `user.message`。

### 2.6 proposals 展开（M08）

| 动作 | CLI | 桌面 WS | 侧栏 / UI |
|------|-----|---------|-----------|
| 列出 pending | `proposals` / `proposals list` | `evolve.proposals`（连接时推送） | grow 顶栏待办 +「去处理」 |
| 接受 | `proposals accept <id>` | `proposal.accept` | grow 展开区 **接受** |
| 拒绝 | `proposals reject <id>` | `proposal.reject` | grow 展开区 **拒绝** |

---

## 3. 非元命令（fallthrough）

未命中 §1 的行进入 `agent.run_turn`：工具调用、confirm 卡、流式 `turn.*` 等由 Agent 回合管线处理。

| 机制 | CLI (`start.bat`) | Terminal (`start-terminal.bat`) | 桌面 WS | Parity |
|------|-------------------|----------------------------------|---------|--------|
| 工具 confirm | stdin `y/n` | stdin `y/n`（effective root 内常免 confirm） | `confirm.response` | **等价不同径** |
| 回合取消 | `Ctrl+C` 仅取消**输入行**；无可靠 Stop | **`Ctrl+C` → `turn.cancel`**（协作取消长回合 · T-5706） | `turn.cancel` · Stop | **混合** | §6.1 · §7 |
| 主题注册 / S2 交互 | `input_fn` 多轮 | 同左（`压缩` 走父类） | `user.message`（`try_route_input` 入队） | **同路径** | §6.3 |
| 退出归档 | `exit --record` / `--full` | `exit` only（无 `--record` 档） | — | **N/A** | §6.1 |

---

## 4. WS 专用（非 `handle_line` · 桌面真源）

下列能力**无** CLI 单行元命令；桌面以 WS 为真源，CLI 用聊天或 REPL 子集绕行。

| WS `type` | 用途 | 侧栏 / UI | CLI 绕行 |
|-----------|------|-----------|----------|
| `shell.switch` | grow / daily / project / govern 壳切换 | 顶栏外壳 `<select>`（`app-chrome.ts`） | 无；CLI 仅 `active_shell` 随会话元数据 |
| `turn.cancel` | 中止当前回合 | Stop | — |
| `confirm.response` | 响应工具确认卡 | 确认卡按钮 | stdin |
| `project.open` | 打开项目绑定当前会话 | （API 有；UI 未接，用 `project.switch`） | `项目 打开 <id>` |
| `project.switch` | 切换项目会话线 | 侧栏项目列表 | `项目 切换 <id>` |
| `project.list` / `project.state` | 列表与侧栏刷新 | 侧栏 | `项目 列表` · `项目 状态` |
| `project.verify` | 一键验收 | 侧栏验收卡 | `项目 验收` |
| `plan.response` | 计划确认门 | 侧栏计划卡 | `项目 确认` · M13 短语 |
| `host_scope.*` | 托管目录 CRUD | 托管区设置 | `托管目录 …` |
| `proposal.accept` / `reject` | 审阅 evolve 提案 | grow 展开区 | `proposals accept/reject` |
| `session.history` / `session.refresh` | 续接历史 | 连接 / 切换后自动 | — |

---

## 6. 用户绕行文案（T-1808-03）

> **用途**：产品文案、帮助页、`notice` 提示或支持文档可直接引用。  
> **原则**：用户尝试不可用路径时，须说明**用什么代替**，不说「不支持」了事。

### 6.1 Parity = N/A

| 项 | 你在哪 | 不能怎样 | 请改用 |
|----|--------|----------|--------|
| **M01 退出** | 桌面聊天 | 发 `exit` / `quit` 不会退出应用（走普通回合或无效） | 托盘 **退出** 或关闭窗口；助手忙时会先确认（见 `DESKTOP.md` §4.4.2） |
| **M01 退出归档** | 桌面 | 无 `exit --record` / `exit --full` 等价 | 会话已落在 `data/sessions/`；若需导出 `data/conversations/*.json`，在终端运行 `start.bat`，输入 `exit --record` 或 `exit --full` |
| **回合取消** | CLI (`start.bat`) | 无 `turn.cancel` / Stop | 等待回合结束；`Ctrl+C` 取消**当前输入行**（不保证中止 LLM）。要可靠停止请用**桌面 Stop** |
| **回合取消** | **Terminal** | 无 Stop 按钮 | **`Ctrl+C`** 中止当前回合（等同 `turn.cancel`）；空闲时 `Ctrl+C` 仅取消输入行 |
| **外壳切换** | CLI | 无 `shell.switch` 元命令 | 开桌面，顶栏 **外壳** 下拉；或 `项目 切换 <id>` |
| **Terminal 项目命令** | Terminal | 发 `项目 …` | 打印「Terminal 不支持项目命令」；用 cwd 狂野模式直接改代码（见 §7） |
| **Desktop 开 Terminal 会话** | 桌面 | `session.list` 不显示 terminal 会话 | 在目标目录 `cd` 后运行 `start-terminal.bat`；**不能**在桌面里 resume terminal id |
| **跨 harness 续接** | 任意 | 不能 desktop 入口打开 terminal 会话 id（反之亦然） | **exit** 当前 UI → 在正确入口 **新开或续接同 harness** |
| **续接历史 UI** | CLI | 无 `session.history` 事件 | 启动 REPL 自动 `resume_or_create`；查 `data/sessions/<id>/messages.jsonl` |

**桌面 · 聊天里误发 `exit`（建议 notice 文案）**：

```text
「exit」只在终端 CLI 里结束会话。要退出桌面：托盘 → 退出，或直接关窗。
```

**CLI · 想中止长回合（`start.bat` · 建议终端提示）**：

```text
CLI 没有 Stop。请等待回合结束，或改用桌面点 Stop。Ctrl+C 仅取消当前输入行。
```

**Terminal · 中止长回合（`start-terminal.bat`）**：

```text
长回合中按 Ctrl+C 即可停止（等同 turn.cancel）。若仅在 you> 等待输入，Ctrl+C 只取消当前行。
```

**Desktop · 锁被 Terminal 占用**：

```text
Terminal 狂野模式正在占用会话锁。请在该 Terminal 窗口输入 exit 结束，再启动桌面。
（不支持接管会话。）
```

### 6.2 Parity = 等价不同径（易混）

| 项 | 桌面用户 | CLI 用户 |
|----|----------|----------|
| **M08 proposals** | 顶栏有「去处理」时优先点展开区 **接受/拒绝**；也可聊天发 `proposals` / `proposals accept <id>` | 打字即可；grow 壳外无顶栏时仍用 `proposals list` |
| **M12 托管目录** | 用顶栏 **托管区** 添加/删除/开写；聊天里 `托管目录 …` 在桌面**同样有效**但非主路径 | REPL 发 `托管目录 列表` 等；与 `data/host_scope.json` 和桌面设置页**同一份数据** |
| **M13/M14 项目确认** | 优先点侧栏计划卡 **确认开工**；或聊天 `确认开工` / `项目 确认` | 同左短语；无侧栏时 `项目 确认` |
| **M14 项目切换** | 点侧栏 **我的项目**；或聊天 `项目 切换 <id>` | `项目 切换 <id>`；无侧栏刷新时 `项目 列表` |
| **M14 项目打开** | 聊天 `项目 打开 <id>`（侧栏暂无「打开」钮，只有切换） | 同左 |
| **M14 项目状态** | 看侧栏 TASKS/MAP，不必打字；需要时 `项目 状态` | `项目 状态` |
| **M14 项目验收** | 计划 **已确认** 后点侧栏 **运行验收**；或 `项目 验收` | `项目 验收`（须 `confirmed` + `PROJECT.md` 里定义验收命令） |
| **M10 checker** | 聊天发 `验收 <工具名>` 或 `check <工具名>`；无单独菜单项 | 同左 |
| **工具 confirm** | 点确认卡 **同意/拒绝**；不要只打字 `y`（除非助手在等你输入） | 终端按提示输入 `y` / `n` / `a` |

**桌面 · 在日用/项目壳找 proposals 按钮**：

```text
待审 evolve 提案只在「生长」壳顶栏显示。请顶栏外壳切到「生长」，或聊天发 proposals 查看列表。
```

**桌面 · 想管托管目录却发了聊天命令**：

```text
托管文件夹请点顶栏「托管区」设置；聊天里发「托管目录 列表」也可以，与设置页是同一份配置。
```

**CLI · 想切 grow/日用壳**：

```text
终端没有外壳菜单。请打开桌面，在顶栏外壳下拉切换；或继续用当前会话，项目相关发「项目 …」即可。
```

### 6.3 同路径但无专用按钮（避免「没这功能」误解）

| 能力 | 桌面操作 | 说明 |
|------|----------|------|
| 新会话 | 聊天框输入 `新会话` | 无托盘/顶栏按钮；秒开新线程（S-03） |
| 压缩 | 聊天框 `压缩` | 无菜单项 |
| 只聊 / 动手 | 聊天框 `只聊` / `动手` | 无顶栏模式切换；状态写入 `meta.json` 续接有效 |
| 探索 | `探索 <任务>` / `explore <task>` | 无探索向导按钮 |
| 注册主题 | `注册主题 <id> …` | 无设置页；按聊天多轮提示完成 |
| 主题 / 加主题 / 换主题 | 聊天框 | 换主题会走 S2 确认，在聊天里逐轮回复 |

**通用（聊天框元命令）**：

```text
本壳没有单独按钮时，在输入框发送元命令即可（与终端 REPL 相同），例如：新会话、压缩、只聊、项目 新建 myapp。
```

### 6.4 验收自检（T-1808-03）

- [x] 凡 §1 表 **N/A** 行（M01）有 §6.1 替代说明  
- [x] §3 **N/A** 行（回合取消、退出归档）有文案  
- [x] §4 **仅桌面 WS** 行均有 **CLI 绕行** 列或 §6.1/6.2 说明  
- [x] **等价不同径** 主表行（M08、M12～M14）在 §6.2 有双向提示  
- [x] 无「不支持」且无替代动作的条目

### 6.5 DOC-02 定稿自检（T-1806-doc-02）

- [x] 文首版本与变更记录均为 **0.3.3**（Terminal 绕行 · T-5707）  
- [x] §0 勾选 T-1808-01～05 全部 **done**  
- [x] §1 主表 17 族与 `handle_line` 分支顺序一致（见文首流程图）  
- [x] IT-38 最小集（M04 `新会话` · M05 `压缩` · M14 `项目 新建`）仍在 §1  
- [x] IT-11 约定（`command` 总刷新 / `user.message` 条件刷新）仍在「范围」节  
- [x] 与 STABILIZATION §8 能力行无冲突（新会话/项目/只聊动手/托管/验收/confirm/Stop）

---

## 7. Terminal harness 绕行（Phase 57 · T-5707）

> **真源**：[TERMINAL-MODE.md](./TERMINAL-MODE.md) v0.2.0 · `cli_terminal.py`  
> **入口**：`start-terminal.bat` 或 `my-agent terminal [path?]`（**保留当前 shell cwd**）

### 7.1 与 Desktop/CLI 的差异（用户可见）

| 能力 | Desktop / `start.bat` | Terminal |
|------|----------------------|----------|
| harness | `desktop` | `terminal`（终身不可变） |
| `项目 …` / 计划门 / 侧栏采纳 | 有 | **无**（发则提示不支持） |
| `只聊` / `动手` | 有 | **无**（固定 `agent`；发则提示「Terminal 仅 agent 模式」） |
| 工作区 | `project_id` · 侧栏 | **effective root** = 启动 cwd 树（R1～R4） |
| 写 confirm | 桌面确认卡 / CLI `y/n` | effective root **内免 confirm**（狂野） |
| 续接指针 | `last_conversation_id` / `project_sessions` | `terminal_last_session` |
| 会话列表（桌面） | 可见 | **隐藏**（TM-18） |
| Stop | Stop 按钮 | **Ctrl+C** |
| interface.lock `ui` | `electron` / `cli` | `terminal`（与另两档 **互斥、无 takeover**） |

### 7.2 Terminal 仍支持的元命令

| 命令 | 行为 |
|------|------|
| `exit` | 保存会话 · 释放锁 · 结束进程 |
| `新会话` / `new` | 新建 `harness=terminal` id；**cwd 不变** |
| `压缩` / `compact` | 同 Desktop CLI（父类 `handle_line`） |
| `托管目录 …` | 同 CLI（共用 `host_scope.json`） |
| 自然语言 | `agent.run_turn`（无 plan gate · 无 segment cap） |

### 7.3 建议绕行文案

**在 Terminal 里误发 `项目 新建 foo`**：

```text
Terminal 不支持项目命令。你已在当前目录狂野模式；直接描述要改的文件或让我跑测试即可。
```

**在桌面里找 Terminal 会话**：

```text
Terminal 会话不会出现在桌面会话列表。请在目标仓库目录打开终端，运行 start-terminal.bat。
```

**想从 Desktop 项目模式切到 Terminal 狂野**：

```text
请 exit 桌面或 CLI，cd 到仓库目录，再运行 start-terminal.bat。不能保留当前聊天线程切换 harness。
```

---

## 5. 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-17 | T-1808-01：自 `handle_line` 审计 17 族元命令 |
| 0.2.0 | 2026-07-17 | T-1808-02：主表 + §2 子命令逐条标注桌面 WS / 侧栏 / Parity |
| 0.3.0 | 2026-07-17 | T-1808-03：§6 用户绕行文案（N/A · 等价不同径 · 无按钮同路径） |
| 0.3.1 | 2026-07-17 | T-1808-04：IT-38 → `tests.test_cli_desktop_parity`（文档漂移 + handle_line / WS） |
| 0.3.2 | 2026-07-17 | T-1808-05 / IT-11：`command` vs `user.message` 约定 + 测试 |
| **0.3.2** | **2026-07-18** | **DOC-02 / T-1806-doc-02 定稿**：文首版本对齐；§0 / §6.5 定稿自检；与 STABILIZATION §8 一致 |
| **0.3.3** | **2026-08-07** | **T-5707**：§7 Terminal harness 绕行；§3 回合取消三分；§6.1 废止接管 · Terminal `Ctrl+C` |
