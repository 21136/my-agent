# 项目工作台界面重设计（WORKBENCH-UI）

> 版本 **0.3.0** · 2026-08-04 · **状态：M1 Q4 已落地（T-3410～3413）**  
> 触发：huiyi 联调体验差 + 反窄化结论——产品初衷是**写项目工具**，聊天顺带；多壳/绑工具骨架与初衷拧着。  
> 关联：[DESKTOP.md](./DESKTOP.md) · [SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md) · [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) · **总路线** [CURSOR-ALIGN.md](./CURSOR-ALIGN.md) **Track G / Phase 34**

---

## 0. 已决

| # | 结论 |
|---|------|
| 1 | 打开应用 → **项目工作台**；**无** grow / pet 主入口 |
| 2 | **项目能力永远在**，不靠切壳解锁工具 |
| 3 | 侧栏本意是**更好写项目**（加速器），不是另一扇权限门 |
| 4 | 改 `workspace/<id>/…` 时，痕迹**静默挂到该项目**，事后侧栏可见 |
| 5 | 普通闲聊可发生在同一会话，但不占主入口叙事 |
| 6 | **Q4（2026-08-04）**：无项目空态保留 **「先聊聊」** 次入口 → 不绑项目的 **grow 普通对话**（可 `write_evolve`）；主叙事仍是选/建项目 |

### Q1～Q3（2026-08-02 · 采纳建议默认）

| # | 已决 |
|---|------|
| Q1 | **A** — pet 从默认入口拿掉；顶栏「伴侣窗」/ 托盘可开 |
| Q2 | **A** — 亮/暗在设置（外观）；**不**用 dark 覆盖 `data-perspective` |
| Q3 | **A** — 无项目时空态主路径为选/建项目；composer **默认仍 gated**（见 Q4 修订） |

### Q4 — 空态「先聊聊」（2026-08-04 · **B 方案 · 已签**）

**背景**：M0 将无项目时的 composer 完全 gated，导致：

- 冷启动只能点按钮，无法口语立项；
- 「+ 对话」语义是「挂起项目 → grow」，但无项目时顶栏入口形同虚设；
- `write_evolve` 须在 grow，却要先有项目才能「+ 对话」——造工具绕路。

**已决（方案 B）**：

| 项 | 结论 |
|----|------|
| 主叙事 | **不变** — 打开仍是项目工作台；空态文案仍以「选择或新建项目」为主 |
| 次入口 | 空态第三按钮 **「先聊聊」**（或等价短文案）；**非**主按钮样式 |
| 行为 | 点击 → `新会话` / `session.new` 等价：**不绑** `project_id`；后端 `active_shell=grow` |
| composer | 进入该会话后 **解锁**；空态本身 composer 仍 gated（须先点「先聊聊」或绑项目） |
| 侧栏 | 无项目绑定时：任务流/服务区 **弱化或空提示**；不假装有项目进度 |
| 拖文件 | 无项目绑定时 **仍禁止**（与 [FILES-DROP.md](./FILES-DROP.md) 一致） |
| 与顶栏「+ 对话」 | **无项目**：空态「先聊聊」= 唯一入口；**已绑项目**：顶栏「+ 对话」= 挂起项目开 grow（UX-POLISH D7） |
| 与 `write_evolve` | grow 普通对话内 **允许** `write_evolve`；项目绑定会话内 **仍禁止**（PROJECT-MODE P6） |
| 回项目 | 用户从「会话」列表或侧栏「我的项目」回到已绑项目会话；不要求从「先聊聊」里再 propose 换线 |

**非目标**：

- 不把空态改成「默认就能在底栏打字」（那是方案 C/D）；
- 不恢复多壳选择器；
- 不让无项目会话写 `workspace/<id>/`（无 `project_root` 即无项目写权限）。

---

## 1. 目标体验（对标 Cursor，缩到自己）

| Cursor 感 | 本工作台 |
|-----------|----------|
| 打开就是干活 | 打开就是**某个项目**（或「选项目」空态；**次选**「先聊聊」） |
| Agent 在侧/底，不挡主线 | **聊天默认占主列**；计划 **审阅 / 完整 TASKS** 临时占主列（见 [PLAN-REVIEW-UI.md](./PLAN-REVIEW-UI.md)）；侧栏仅短卡入口 |
| 不切「模式」才能用工具 | **无壳选择器**；无「先进 project 才有工具」 |
| 改文件有着落 | 路径 → 项目账本；侧栏能回看 |
| 偶尔先问一句 | 空态 **先聊聊** → grow 问答 / 造工具，不占主按钮叙事 |

**非目标（本版）**：做成第二个 IDE；保留 pet 浮动球主入口；继续维护 default/night 多套「像另一个 App」的入口。

---

## 2. 信息架构

### 2.1 有项目绑定

顶栏 + 侧栏加速器 + 主区对话（与 M0 相同）。

### 2.2 无项目空态（Q4）

```text
┌─────────────────────────────────────────┐
│  选择或新建项目                          │
│  打开应用即工作台。先选一个项目，再…      │
│                                         │
│  [ 新建项目 ]  [ 我的项目 ]              │  ← 主路径（accent + 默认）
│         [ 先聊聊 ]                       │  ← 次入口（ghost / 文字按钮）
└─────────────────────────────────────────┘
```

| 控件 | DOM id（建议） | 动作 |
|------|----------------|------|
| 新建项目 | `#empty-new-project` | 已有：`项目 新建 <id>` |
| 我的项目 | `#empty-pick-project` | 已有：侧栏项目列表 overlay |
| **先聊聊** | `#empty-free-chat` | **新增**：`client.sendCommand("新会话")`；期望 `session.banner` 无 `project_id` |

空态消失条件：`projectState.projectId` **或** 当前会话为 **grow 且无绑定**（`session.banner` 无 `project_id` — 前端以 `projectId === ""` 且已进入 free-chat 会话为准）。

### 2.3 顶栏「+ 对话」语义（澄清）

| 用户状态 | 「+ 对话」 | 空态「先聊聊」 |
|----------|-----------|----------------|
| 无项目 | 隐藏或 disabled（与 gated composer 一致） | **唯一**开 grow 入口 |
| 已绑项目 | 挂起项目 → 新 grow 会话 | 隐藏（空态不显示） |

---

## 3. M0 落地对照

| 项 | 实现 |
|----|------|
| 启动进工作台 | `electron/main.ts` `whenReady` → `openWorkbenchWindow` |
| 关工作台不回 pet | close 仅 hide；托盘双击/快捷键打开工作台 |
| 默认 project 布局 | unified `data-perspective=project`；侧栏常显；`perspectiveLocked` |
| 空态 | `#workbench-empty`：新建 / 我的项目；composer+chat gated |
| pet | `app-chrome`「伴侣窗」→ `openPet` |
| 主题 | `applyTheme` 只改 `dataset.theme`，不踩 perspective |

> **Q4 未落地**：空态「先聊聊」、grow 无绑定时 composer 解锁 — 见 §4 M1。

---

## 4. 里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **D0** | 本文拍板 | **done** |
| **M0** | 启动进工作台；空态；侧栏默认展开 | **done** |
| **M1** | Q4：空态「先聊聊」+ grow 无绑 composer 解锁；顶栏「+ 对话」无项目时隐藏；路径写入挂账（原 M1 余量） | **Q4 done** · 挂账 todo |
| **M2** | 文档与 DESKTOP §0 同步；可深藏 pet | todo |
| **M3** | 计划审阅主列（[PLAN-REVIEW-UI.md](./PLAN-REVIEW-UI.md) PRU-M0） | **done** |

### M1 任务拆分（→ [TASKS.md](./TASKS.md) T-3410～T-3413）

| ID | 交付 | 验收 |
|----|------|------|
| T-3410 | 空态 `#empty-free-chat` + 样式（次按钮） | 无项目可见；点击发 `新会话` |
| T-3411 | `updateWorkbenchEmpty` / `allowSend`：无 `projectId` 但当前为 grow 无绑会话 → 解锁 composer | IT-341：空态点「先聊聊」后可发送 |
| T-3412 | 顶栏「+ 对话」：`!projectId` 时 hidden/disabled | 与空态语义不冲突 |
| T-3413 | 冒烟 S-341 更新（见下） | 四条全过 |

### 手工冒烟（S-340 · M0 基线）

1. 启动桌面 → 工作台（非 pet 球）  
2. 无项目 → 空态「新建 / 我的项目」；**底栏仍不能**直接发消息（M0）  
3. 新建或选择项目 → 空态消失，可聊  
4. 顶栏「伴侣窗」仍可开 pet；关工作台进托盘不自动弹 pet  

### 手工冒烟（S-341 · M1 / Q4）

1. 无项目 → 空态见 **「先聊聊」**（次按钮）  
2. 点「先聊聊」→ 空态消失；底栏可输入；`session.banner` **无** `project_id`  
3. 在该会话说「造一个 echo 工具」→ `write_evolve` **不被** project 门拦截  
4. 顶栏无项目时 **无**可用「+ 对话」（或 disabled + tooltip）  
5. 绑项目后 → 空态逻辑恢复 M0；「+ 对话」可挂起项目开 grow  

---

## 5. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-02 | 初稿 |
| 0.2.0 | 2026-08-02 | Q1～Q3 已决；M0 实现 |
| 0.2.1 | 2026-08-03 | 主输入 **自动路由** Plan（§15.11.1）；Alt+发送强制主 Agent；见 [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) |
| 0.3.0 | 2026-08-04 | **Q4 方案 B**：空态「先聊聊」→ grow 无绑对话；修订 Q3 表述；M1 任务 T-3410～3413 · S-341 |
