# 壳合并设计（SHELL-CONSOLIDATION）

> 版本 **0.2.0** · 2026-07-30  
> 状态：`implemented` — **前端已落地**（unified + pet；旧壳目录已删）；后端 `active_shell` / `shell_sessions` 仍作会话线标签，`shell.switch` UI 路由已退役  
> 关联：[DESKTOP.md](./DESKTOP.md) · [UX-POLISH.md](./UX-POLISH.md) · [DAILY-SHELL.md](./DAILY-SHELL.md)（superseded） · [PROJECT-MODE.md](./PROJECT-MODE.md) · [PET-SHELL.md](./PET-SHELL.md) · `TASKS.md`

---

## 0. 落地摘要（2026-07-30）

| 项 | 状态 |
|----|------|
| `shells/unified/` + perspective（default/project/night） | **done** |
| 删除 grow/daily/project/govern 前端目录 | **done** |
| starfield → `skins/starfield/` | **done** |
| `shell-router` 只挂载 unified | **done** |
| 顶栏壳选择器 / 自动切壳 UI | **已移除** |
| pet 独立窗 | **保留** |
| 后端去掉 `active_shell` 语义 | **未做**（仍作会话线；见设计 §3 Phase 3 尾项） |
| 文档同步 | **本轮** |

以下正文保留合并前的动机与分阶段方案，作为设计真源；**实现以 §0 与代码为准**。

## 1. 动机

### 1.1 现状痛点

当前桌面壳分 5 个独立壳（`grow` / `daily` / `project` / `govern` / `pet`），前端代码总计约 4600 行：

| 壳 | 代码量 | 实际使用频率 | 问题 |
|-----|--------|-------------|------|
| **grow** | ~1000 行 | 最高 | 主壳，功能最完整 |
| **daily** | ~2000 行 | 中 | 大量 starfield 动画代码；聊天逻辑与 grow 重复 |
| **project** | ~1400 行 | 中 | 聊天逻辑与 grow 重复；差异化只是顶栏 project 状态 |
| **govern** | ~400 行 | 几乎不用 | 占位壳，无实际功能 |
| **pet** | ~1000 行 | 低 | 独立窗口，与聊天壳本质不同 |

**核心问题**：

1. **同一套聊天 UI 抄了 3 遍。** grow、daily、project 的核心交互完全一致——消息输入、流式回复渲染、工具确认卡、提案列表。`chat-state.ts`（534 行）已经试图统一共享层，但每个壳的 `index.ts` 还是各自实现了一遍事件处理、DOM 操作、状态管理。改一个交互细节（如 confirm 卡片样式）需要在 3 个壳里各改一遍。

2. **壳的语义区分是伪需求。** "coding 用 grow、整理文件用 daily、项目管理用 project"——本质上是同一个行为（和 agent 聊天），只是 prompt 主题不同。用户真正需要的是一套聊天界面 + 不同对话上下文，而不是三套聊天界面。

3. **`activity_router.py` 的自动切壳让问题更糟。** 用户说 "用 sort_by_extension 整理" → 切 daily；说 "造工具" → 切 grow；说 "做项目" → 切 project。壳频繁切换打断心流，顶栏 "壳已切换" 提示和 8 秒撤销机制本身就是对糟糕体验的补偿。

4. **govern 壳纯占位。** 代码只有 12 行，review/audit 结果完全可以渲染在聊天流里。

### 1.2 关键数据

| 指标 | 数值 |
|------|------|
| 有交互逻辑的壳 | 3 个（grow / daily / project） |
| 占位壳 | 1 个（govern，12 行） |
| 独立窗口壳 | 1 个（pet，不改） |
| 重复实现的聊天 UI | 3 套 |
| `createChatSession` 工厂调用 | 3 次（选项略有不同） |
| 壳独有状态字段 | grow 7 个 / daily 3 个 / project 16 个 |
| `activity_router.py` 规则 | 12 条 if-else |
| `shell_switch.py` 函数 | 10 个（合并后仅保留 project 相关的 3 个） |
| WS 消息类型涉及壳切换 | 6 个（`shell.switch`、`shell.switch.done`、`ui.route`、`context.switch.request`/`.done` 的 shell 分支、`project.state` 条件发射） |

### 1.3 设计目标

| 目标 | 说明 |
|------|------|
| **一个聊天壳** | grow 作为唯一全功能聊天壳，daily/project/govern 的差异化能力作为壳内状态 |
| **壳内模式切换** | 主题、项目、视角等通过顶栏下拉或对话上下文切换，不切 DOM |
| **维护成本降 2/3** | 从 ~4600 行前端到 ~2000 行，任何 UI 改动只在一处生效 |
| **pet 保留独立** | 桌宠本质是独立窗口 + 独立交互模式，与聊天壳解耦 |
| **activity_router 简化** | 只负责推荐主题，不负责切壳 |
| **向后兼容** | 旧会话的 `meta.active_shell` 平滑迁移；CLI 不受影响 |

### 1.4 不做什么

| 非目标 | 理由 |
|--------|------|
| 重写聊天渲染引擎 | `chat-state.ts` 保持，只统一消费方式 |
| 改 WS 协议 | 移除 `shell.switch` / `ui.route` 是简化，不是重写 |
| 动 pet 壳 | pet 是独立产品形态，与本次合并无关 |
| 做代码编辑器 / IDE | 不在定位内 |

---

## 2. 现状详细分析

### 2.1 五壳差异矩阵（详细）

#### 2.1.1 功能互斥矩阵

| 功能 | grow | daily | project | govern |
|------|------|-------|---------|--------|
| **进程区块**（推理 + tool 行） | ✅ 显示 | ❌ 隐藏 | ✅ 显示（复用 grow） | ❌ |
| **确认呈现方式** | 聊天流内嵌卡片 | 全屏毛玻璃 overlay | 聊天流内嵌卡片 | ❌ |
| **Topbar** | 意图 + checker + 提案 | ❌ 无 | project 状态 + 意图 | ❌ |
| **Proposal 展开面板** | ✅ | ❌ | ❌ | ❌ |
| **侧边栏** | ❌ | ❌ | ✅（240-300px 网格） | ❌ |
| **聊天轮次调暗**（旧轮次 55% 透明度） | ❌ | ✅（最后 2 轮除外） | ❌ | ❌ |
| **召回高亮**（大字号 + 彩色背景） | ❌ | ✅（最近 3 轮） | ❌ | ❌ |
| **编辑器胶囊样式**（毛玻璃圆角药丸） | ❌ | ✅ | ❌（使用 grow 全宽栏） | ❌ |
| **流式光标**（"▍" 闪烁） | ❌ | ✅ | ❌ | ❌ |
| **入场弹跳动画**（用户消息） | ❌ | ✅ | ❌ | ❌ |
| **Z-index 分层布局** | ❌（线性 flex） | ✅（3 层绝对定位） | ❌（CSS Grid） | ❌ |
| **忙碌动画** | 5s 4 色渐变 | 3s 4 色渐变 + 内阴影 | 4.5s 蓝/青绿渐变 | ❌ |

#### 2.1.2 `createChatSession` 选项差异

| 选项 | grow | daily | project |
|------|------|-------|---------|
| `showProcess` | `true` | `false` | `true` |
| `confirmInBlocks` | `true` | `false` | `true` |

daily 的 `confirmInBlocks: false` 意味着确认走 `confirmOverlay`（全屏毛玻璃），其余两壳走聊天流内嵌卡片。

#### 2.1.3 壳独有状态变量

**grow**：`proposals[]`、`proposalIndex`、`expandOpen`、`intentLabel`、`checkerLabel`、`memoryLabel`、`projectLabel`

**daily**：`recallHighlightTurns`、`focusObserver`、`motionQuery` 监听器

**project**：`projectId`、`planStatus`、`tasksMarkdown`、`mapMarkdown`、`tasksDone`、`tasksTotal`、`tasksAllDone`、`acceptanceCommand`、`canVerify`、`planOverlay`、`sidebarTab`、`verifyResult`、`verifyRunning`、`projects[]`、`switchOverlay`、`switchInProgress`、`pendingPickerId`

#### 2.1.4 CSS 变量 / 主题差异

| 壳 | 主色 | 工作渐变 | 特殊效果 |
|-----|------|---------|----------|
| **grow** | `var(--ma-grow-flow-a/b/c)`（基于 theme） | 5s 周期 | topbar 脉动 |
| **daily** | `#ff2d92`（硬编码粉红） | 3s 周期 4 色（红/粉/黄/青绿） | 空闲微光、毛玻璃（`backdrop-filter: blur()`） |
| **project** | `#2563eb`（蓝图蓝） | 4.5s 周期 | 侧边栏样式、响应式 900px 断点 |

#### 2.1.5 壳独有事件处理

| 事件 | grow | daily | project |
|------|------|-------|---------|
| `evolve.proposals` | 更新 proposals 列表 + 重渲染 | **显式忽略** | — |
| `reasoning.delta` | 进 process 块 | 状态更新 | 进 process 块 |
| `project.state` | — | — | 完整项目状态同步 |
| `project.list` | — | — | 填充项目选择器 |
| `project.switch.request` | — | — | 显示切换确认卡 |
| `project.switch.done` | — | — | 刷新会话/项目 |
| `plan.request` | — | — | 显示计划确认卡 |
| `plan.done` | — | — | 清除计划 overlay |
| `project.verify.done` | — | — | 显示验收结果 |
| `assistant.done` 后刷新 | — | — | 调 `client.refreshProject()` |
| `notice` 文本匹配 | — | — | 乐观更新计划状态 |
| 召回意图处理 | — | ✅（高亮 + 滚动） | — |
| IntersectionObserver | — | ✅（轮次可见性） | — |
| `prefers-reduced-motion` | CSS 查询 | CSS + `data-reduced-motion` 属性 | CSS 查询 |

#### 2.1.6 编辑器占位符差异

| 壳 | 占位符 |
|-----|--------|
| grow | `输入消息，或拖入文件…` |
| daily | `输入消息，或拖入文件…` |
| project | `输入消息，或拖入代码文件…` |

### 2.2 共享逻辑（chat-state.ts）

`chat-state.ts` 已经抽象了以下共享逻辑：

- `ChatBlock` 类型定义（user / assistant / confirm / tool / notice / error / intent / checker / context-switch 等）
- `createChatSession()` 工厂函数，统一消费 WS 事件并产出 blocks
- 选项：`showProcess`（是否显示 tool.start/tool.end）、`confirmInBlocks`（确认卡是否在对话流内）
- 回调：`onChange`、`onTurnStart`、`onCheckerVerdict`、`onConfirmRequest`、`onConfirmDone`、`onToolStart`、`onToolEnd`、`onAssistantDone`、`onTurnEnd`、`onCancelTimeout`、`onError`

三个壳对 `createChatSession` 的使用方式几乎一致——差异仅在回调中更新各自的 topbar HTML 和 status 文本。

### 2.3 各壳独有逻辑

**grow 独有**：
- 提案展开区（`grow-expand`）：显示 pending proposals，接受/拒绝/查看全文/下一条
- 顶栏提案计数器
- 顶栏意图标签 + checker 标签 + memory 标签 + project 标签

**daily 独有**：
- `starfield.ts`（759 行）：Canvas 星空动画背景
- `constellation.ts`（122 行）：星座连线动画
- `daily.css`（822 行）：暖夜暗色主题
- 角色前缀（"你 ·"/"助手 ·"）——已在 1.2 咖啡馆对谈迭代中被否决但代码可能残留

**project 独有**：
- 项目计划确认面板（plan gate confirm）
- 项目 TASKS 进度侧栏
- 项目顶栏信息（project_id、plan_status、task progress）
- `project.css`（886 行）：项目壳专用样式
- 项目模式特有的 WS 消息处理（`project.state`、`plan.response` 等）

**govern**：12 行占位 HTML，无实际逻辑。

**pet 独有**：
- 独立 Electron BrowserWindow（`alwaysOnTop`、无边框、小尺寸）
- 光球 mood 动画（idle / listening / busy / nudge）
- 气泡聊天 UI（不同于全窗聊天的卡片流）
- 角色动画精灵（Xenia）
- 重活接引（自动开工作台窗口）

### 2.4 activity_router.py 路由表

当前路由决策矩阵（基于用户输入文本 + 意图 + session 状态）：

| 条件 | → 壳 | → 加主题 |
|------|------|----------|
| 含 review/audit/治理 等 | govern | — |
| 项目计划 gate 未确认 | project | coding |
| 有 pending proposals | grow | — |
| 当前在 project + 有 project_root，且非 grow 独占词 | project | coding |
| 含 "造"/"实现"/"write_evolve" 等 grow 独占词 | grow | coding/data |
| 含 "做项目"/"项目 新建" 等 | project | coding |
| 含 workflow 工具名 | daily | workflow |
| intent=execute + coding/data scope + workspace/ | project | coding |
| intent=execute + coding/data scope | grow | coding/data |
| intent=research + evolve/tools 路径 | grow | scope |
| intent ∈ {qa, recall, plan} | daily | — |
| session topics 含 coding | grow | — |
| 默认 | daily | — |

**问题**：这张表是 60 行 if-else，每次加一个场景就要加一条规则。合并后，路由表退化为**只推荐主题**，不再输出壳名。

---

## 3. 目标架构

### 3.1 统一壳（unified shell）

```text
desktop/src/shells/
  unified/           # 合并 grow + daily + project（新）
    index.ts         # 壳入口，mountUnifiedShell()
    unified.css      # 统一样式，含亮色/暗色 CSS 变量
    topbar.ts        # 顶栏组件（意图、checker、提案、project 状态）
    proposals.ts     # 提案展开区（从 grow 迁入）
    project-panel.ts # 项目状态面板（从 project 迁入）
  pet/               # 保留，不改
    ...

# 删除：
  grow/     → 迁移到 unified/
  daily/    → 迁移到 unified/；starfield 作为可选皮肤保留
  project/  → 迁移到 unified/
  govern/   → 删除（review/audit 结果渲染在聊天流）
```

### 3.2 壳内视角（perspective）

不再有壳切换。统一壳通过 **视角（perspective）** 控制顶栏内容和视觉风格：

| 视角 | 触发条件 | 视觉 | 顶栏内容 |
|------|---------|------|----------|
| **default** | 无特殊上下文 | 纸白赭石（P 色系） | 意图标签 |
| **coding** | topics 含 coding | 同 default | 意图 + checker |
| **workflow** | topics 含 workflow | 同 default | 意图 + 工具状态 |
| **project** | session 有 project_root | 同 default，顶栏多一条 project 行 | project 状态 + plan 确认 + TASKS 进度 |
| **night** | 用户手动切换 | 暖夜暗色 + 可选星空背景 | 同对应视角 |

视角切换方式：
- **自动**：根据 session 状态（topics、project_root）自动调整顶栏内容和视觉
- **手动**：顶栏下拉可选暗色主题 / 星空背景（纯 CSS 变量切换，不重建 DOM）
- **不复用 activity_router**：主题推荐走现有的 `router.py` S2 确认流程；视角由 session 状态直接驱动，无额外决策层

### 3.3 文件变更清单

#### 新增

| 文件 | 说明 |
|------|------|
| `desktop/src/shells/unified/index.ts` | 统一壳入口，合并 grow/daily/project 的聊天渲染 |
| `desktop/src/shells/unified/unified.css` | 统一样式，CSS 变量驱动亮/暗双主题 |
| `desktop/src/shells/unified/topbar.ts` | 顶栏组件（意图、checker、提案计数、project 状态） |
| `desktop/src/shells/unified/proposals.ts` | 提案展开面板（从 grow 迁入） |
| `desktop/src/shells/unified/project-panel.ts` | 项目状态面板（从 project 迁入） |
| `docs/SHELL-CONSOLIDATION.md` | 本设计文档 |

#### 修改

| 文件 | 改动 |
|------|------|
| `desktop/src/main.ts` | 壳路由从 4 壳 → 1 壳；移除壳切换逻辑 |
| `desktop/src/shell-router.ts` | 简化为直接 mount unified shell |
| `desktop/src/app-chrome.ts` | 移除壳切换菜单项，保留主题切换 |
| `desktop/electron/main.ts` | 移除多壳窗口管理；保留 pet 独立窗口 |
| `agent-core/activity_router.py` | 退化为只推荐主题，不输出壳名；或直接删除 |
| `agent-core/server.py` | 移除 `shell.switch` 消息处理；移除 `ui.route` 发射 |
| `agent-core/shell_switch.py` | 废弃（保留文件但移除调用） |
| `agent-core/session.py` | `meta.active_shell` 保留字段但不再用于壳切换 |
| `docs/DESKTOP.md` | 更新 §3 壳架构描述，标注旧壳为 deprecated |
| `docs/DAILY-SHELL.md` | 标注为 superseded |
| `docs/PROJECT-MODE.md` | 更新 §8.4 壳相关描述 |

#### 删除

| 路径 | 说明 |
|------|------|
| `desktop/src/shells/grow/` | 迁移到 unified |
| `desktop/src/shells/daily/` | 迁移到 unified；starfield 相关暂保留在 `desktop/src/skins/starfield/` |
| `desktop/src/shells/project/` | 迁移到 unified |
| `desktop/src/shells/govern/` | 删除 |

#### 保留不动

| 路径 | 说明 |
|------|------|
| `desktop/src/shells/pet/` | 完全不动 |
| `desktop/src/shells/chat-state.ts` | 不动（unified 壳直接使用） |
| `desktop/src/file-drop.ts` | 不动 |
| `desktop/src/composer-attachments.ts` | 不动 |
| `desktop/src/context-switch-overlay.ts` | 不动 |
| `desktop/src/markdown.ts` | 不动 |

### 3.4 WS 协议简化

移除以下消息类型：

| 移除 | 理由 |
|------|------|
| `ui.route` | 不再自动切壳；主题推荐走现有 S2 流程 |
| `shell.switch` | 用户不手动切壳 |
| `shell.switch.done` | 同上 |

保留所有其他消息类型不变。`session.banner` 中 `active_shell` 字段保留（兼容旧会话）但前端忽略。

### 3.5 `state.json` 迁移

当前 `state.json` 有两个壳相关键：

| 键 | 用途 | 迁移后 |
|-----|------|--------|
| `shell_sessions` | 映射 `{"grow": session_id, "daily": session_id}` | 废弃。新键 `session_id`（单值）替代 |
| `last_project_id` | 上次打开的项目 ID | 保留（project 视角仍需要） |
| `project_sessions` | 映射 `{project_id: session_id}` | 保留（project 视角仍需要） |

迁移策略：启动时若 `shell_sessions` 存在，取其中最近使用的 session_id 作为当前会话；否则用 `last_conversation_id`。此后只写新键。

### 3.6 `context_switch.py` 简化

当前 `context_switch.py` 的 `_apply_shell_switch()` 和 `shell.switch` 分支将被移除。`propose_context_switch` builtin tool 的 `action: "shell.switch"` 选项移除。保留 `action: "project.create"` / `"project.switch"` / `"session.new"`。

### 3.7 `shell_switch.py` 废弃

以下函数全部成为死代码：
- `switch_shell()` — 跨壳切换
- `park_session()` — 切壳前保存会话
- `read_shell_sessions()` / `record_shell_session()` / `lookup_shell_session()` / `lookup_shell_owner()` — `shell_sessions` 映射

项目相关函数（`record_project_session()` / `lookup_project_session()` / `last_project_id`）保留。

### 3.8 旧会话兼容

`meta.active_shell` 字段在旧会话中可能为 `"grow"` / `"daily"` / `"project"`。统一壳启动时：

- 读取 `active_shell` → 映射为初始视角
- `"grow"` → 默认视角
- `"daily"` → 默认视角（若 topics 为空）/ workflow 视角（若 topics 含 workflow）
- `"project"` + 有 `project_root` → project 视角
- `"govern"` → 默认视角
- 此后不再写入 `active_shell`

---

## 4. 实施计划

### Phase 1：创建 unified 壳（不影响现有壳）

**目标**：新建 `unified/` 目录，把 grow 的完整实现拷过去作为基底，然后抽象出可变部分。此阶段 unified 壳尚未被 `shell-router.ts` 挂载，现有桌面完全不受影响。

| # | 步骤 | 详细 | 验收 |
|----|------|------|------|
| 1.1 | 创建 `unified/index.ts` | 从 `grow/index.ts` 完整拷贝，改函数名为 `mountUnifiedShell` | `npm run build` 通过 |
| 1.2 | 创建 `unified/unified.css` | 合并 `grow.css` + `daily.css` + `project.css`，将颜色/动画提取为 CSS 自定义属性（`--ma-perspective-accent`、`--ma-perspective-bg`、`--ma-flow-a/b/c`、`--ma-flow-duration`）；亮/暗两套值 | 两套变量切换不丢样式 |
| 1.3 | 抽象 `topbar.ts` | 从 grow 的 `renderTopbar()` 提取。接收 `{ intentLabel, checkerLabel, memoryLabel, projectLabel, proposals, expandOpen, onToggleExpand }` 参数 | 顶栏可独立渲染 |
| 1.4 | 抽象 `proposals.ts` | 从 grow 的 `renderExpand()` + 提案事件处理提取。接收 `{ proposals[], proposalIndex, onAccept, onReject, onNext, onCollapse }` | 提案面板可独立操作 |
| 1.5 | 抽象 `project-panel.ts` | 从 `project/index.ts` 提取侧边栏渲染逻辑、plan 确认卡、项目选择器、验收卡。接收 project 状态对象 | project 面板可独立渲染 |
| 1.6 | 引入 `perspective` 状态 | 定义 `type Perspective = "default" | "project" | "night"`；根据 session banner + topics + project_root 计算当前视角 | 视角切换不报错 |

### Phase 2：集成视角（daily/project 能力并入 unified）

**目标**：让 unified 壳根据视角动态显示/隐藏 topbar 内容、侧边栏、确认方式、动画风格。

| # | 步骤 | 详细 | 验收 |
|----|------|------|------|
| 2.1 | `showProcess` + `confirmInBlocks` 动态化 | 不再硬编码 `{ showProcess: true, confirmInBlocks: true }`。perspective 为 `default` 或 `project` 时同 grow；`night` 视角可切换为 daily 同款（进程关闭、确认走 overlay）。用 `createChatSession` 的重建机制 | 切到 night 视角 → 确认变为全屏毛玻璃 |
| 2.2 | project 视角 → 显示侧边栏 | perspective 为 `project` 时，布局从 flex 列切为 CSS Grid（`240px sidebar + 1fr chat`）。侧边栏由 `project-panel.ts` 渲染 | 有 project_root 的会话自动显示侧边栏 |
| 2.3 | project 视角 → 事件处理 | 注册 `project.state` / `project.list` / `plan.*` / `project.verify.done` 等事件处理器（仅 project 视角） | 项目模式功能完整 |
| 2.4 | night 视角 → 暗色主题 + 可选星空 | CSS 变量切为 daily 暖夜色系。starfield canvas 作为可选背景（`<canvas>` 插入，不创建时无开销） | 手动切 night → 全壳暗色 |
| 2.5 | night 视角 → 聊天轮次调暗 + 召回高亮 | 仅在 night 视角启用 IntersectionObserver + 轮次透明度逻辑 | 旧轮次 55% 透明；召回轮次高亮 |
| 2.6 | night 视角 → 编辑器胶囊 | 仅在 night 视角使用 `.daily-composer-capsule` 样式（圆角药丸 + 毛玻璃） | 编辑器外观与 old daily 一致 |
| 2.7 | 提案展开区条件显示 | 有 pending proposals 时 topbar 显示入口；无时完全隐藏。从 `evolve.proposals` WS 事件驱动 | 无 proposal → 无展开区 |
| 2.8 | 编辑器占位符动态化 | project 视角 → `输入消息，或拖入代码文件…`；其他 → `输入消息，或拖入文件…` | 占位符随视角变 |

### Phase 3：切换到 unified 壳

**目标**：让 `shell-router.ts` 挂载 unified 壳，移除壳切换代码。

| # | 步骤 | 详细 | 验收 |
|----|------|------|------|
| 3.1 | `shell-router.ts` → 只挂载 unified | switch 语句退化为 `return mountUnifiedShell(root, client)` | 桌面启动 → 一个聊天壳 |
| 3.2 | `app-chrome.ts` 移除壳选择器 | 删除 `<select>` 壳切换下拉和锁定复选框；保留主题切换下拉 | 顶栏无壳选择器 |
| 3.3 | `app-chrome.css` 移除壳相关忙碌动画 | 删除 `[data-busy-shell="daily"]` / `[data-busy-shell="project"]` 选择器；统一为一种忙碌渐变 | busy 动画不依赖壳名 |
| 3.4 | `server.py` 移除 `shell.switch` handler | 删除第 733-775 行；移除 `on_executor_event` 中的 `shell.switch.done`、`ui.route` 转发 | `python agent-core/server.py --demo` 通过 |
| 3.5 | `server.py` 移除 `emit_session_state` 中 project 条件 | 不再检查 `active_shell == "project"`；改为 `project_id` 存在即发射 `project.state` | project 连接正常收到状态 |
| 3.6 | `activity_router.py` 退化为主题路由器 | 移除 `compute_activity_route` 中的壳决策；只保留 `infer_topic_scope` + `apply_route_topics`。`ui_route_payload` 移除 `shell` 字段。文件名改 `topic_router.py` 或保留（内部重构） | `python agent-core/activity_router.py` 通过 |
| 3.7 | `shell_switch.py` 废弃调用 | 移除 `server.py` / `context_switch.py` 中对 `switch_shell` / `park_session` 的调用。保留 `record_project_session` / `lookup_project_session` / `last_project_id` | import 不报错；project 切换正常 |
| 3.8 | `session.py` `active_shell` 字段标记 deprecated | `DEFAULT_ACTIVE_SHELL` 改为 `"unified"`；`session_banner_event` 中 `active_shell` 改为固定值；旧值兼容读取 | 旧会话正常加载 |
| 3.9 | `context_switch.py` 移除 shell 分支 | 移除 `_apply_shell_switch()`；`propose_context_switch` 的 `action: "shell.switch"` 选项移除 | context switch 仅支持 project / session.new |
| 3.10 | `desktop/src/api/ws.ts` 清理 | 移除 `ShellId` 类型、`activeShell` 字段、`shellSwitch()` 方法、`setActiveShell`/`getActiveShell`/`isActiveShell` | `npm run build` 通过 |
| 3.11 | `electron/main.ts` 清理 | 移除 `shell_sessions` 相关 IPC；workbenchWindow 不再需要多壳管理 | pet 独立窗口正常 |

### Phase 4：清理旧代码

**目标**：删除旧壳目录，更新相关文档。

| # | 步骤 | 验收 |
|----|------|------|
| 4.1 | 删除 `desktop/src/shells/grow/` | build 通过 |
| 4.2 | 删除 `desktop/src/shells/daily/`（starfield 迁到 `desktop/src/skins/starfield/`） | build 通过；night 视角星空可用 |
| 4.3 | 删除 `desktop/src/shells/project/` | build 通过 |
| 4.4 | 删除 `desktop/src/shells/govern/` | build 通过 |
| 4.5 | 删除 `agent-core/shell_switch.py` 中废弃函数（保留文件但只含 project 相关 3 个函数） | `python agent-core/shell_switch.py` 无 import 错误 |
| 4.6 | 更新 `docs/DESKTOP.md` §3 标记旧壳为 deprecated | 文档交叉引用正确 |
| 4.7 | 更新 `docs/DAILY-SHELL.md` 标记为 superseded | — |
| 4.8 | 更新 `docs/PROJECT-MODE.md` §8.4 | — |

---

## 5. 风险与回滚

### 5.1 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| project 面板逻辑复杂，合并时遗漏 | project 模式下功能缺失 | Phase 2 逐功能对齐，Phase 3 前全功能验收 |
| 暗色主题 CSS 变量覆盖不全 | 部分元素色系错误 | 先在 unified.css 中完整定义变量，再切换 |
| 旧 `meta.active_shell` 导致视角错乱 | 用户体验降级 | Phase 3 有兼容映射（§3.5） |
| pet 依赖 daily session | pet 行为异常 | pet 不改，后端 daily session 映射保留 |

### 5.2 回滚

Git revert 即可。旧壳代码在删除前（Phase 4）始终可用。建议 Phase 3 切换后使用几天再做 Phase 4 清理。

---

## 6. 验收

### 6.1 自动化

```powershell
cd D:\my-agent\agent-core
python activity_router.py          # 退化为只推荐主题后仍通过
python server.py --demo            # 移除 shell.switch 后仍通过

cd D:\my-agent\desktop
npm run build                      # 无 TS 错误
```

### 6.2 手工验收（桌面壳）

| 场景 | 预期 |
|------|------|
| 启动桌面 | 统一壳显示，顶栏显示当前 session 信息 |
| 输入 "造一个 coding 工具" | 聊天流正常，顶栏自动显示 coding 相关意图 |
| 输入 "用 sort_by_extension 整理" | 聊天流正常，顶栏自动显示 workflow 主题 |
| 进入项目模式 | 顶栏显示 project 面板、plan 确认、TASKS 进度 |
| 有待处理 proposal | 顶栏显示提案计数，展开区可操作 |
| 手动切换暗色主题 | 全壳色系切换，聊天内容正常显示 |
| 工具 confirm | 确认卡在聊天流内渲染，按钮可点击 |
| 流式回复 | assistant.delta 逐字显示 |
| 停止回合 | turn.cancel 立即生效 |
| pet 窗口 | 独立窗口正常打开、显示、关闭 |

---

## 7. 记录

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 初稿。分析五壳现状，提出统一壳方案。 |
