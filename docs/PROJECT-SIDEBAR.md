# Project Sidebar Redesign

> 状态：设计文档 · 待审核（已决议 12 项关键决策）
> 日期：2026-07-29
> 最后一轮讨论：Plan Agent 架构 + 消息路由模型 + 5 项追加决策

---

## 目录

1. [当前问题分析](#1-当前问题分析)
2. [设计哲学](#2-设计哲学)
3. [整体架构：Plan Agent + 任务流视图](#3-整体架构plan-agent--任务流视图)
4. [Plan Agent 设计](#4-plan-agent-设计)
5. [消息路由：聊天框 → Plan Agent → 主 Agent](#5-消息路由聊天框--plan-agent--主-agent)
6. [侧边栏 UI：任务流主视图](#6-侧边栏-ui任务流主视图)
7. [覆盖面板：地图、验收、项目切换](#7-覆盖面板地图验收项目切换)
8. [变更感知与可视化](#8-变更感知与可视化)
9. [任务交互：右键菜单](#9-任务交互右键菜单)
10. [WS 协议设计](#10-ws-协议设计)
11. [后端变更](#11-后端变更)
12. [文件变更清单](#12-文件变更清单)
13. [实施阶段](#13-实施阶段)
14. [待决问题](#14-待决问题)（已全部分析，决议见 §15）
15. [已决议的设计问题](#15-已决议的设计问题)
    15.1 [Task-stop 与 Plan Agent](#151-task-stop-与-plan-agent-的关系)
    15.2 [Plan Agent 生命周期](#152-plan-agent-的生命周期)
    15.3 [降级策略](#153-plan-agent-不可用时的降级)
    15.4 [主动性边界](#154-plan-agent-的主动性边界)
    15.5 [渐进实施路径](#155-渐进实施路径)
    15.6 [模型选择](#156-plan-agent-的模型选择)
    15.7 [撤销机制](#157-撤销机制)
    15.8 [plan_dirty 确认粒度](#158-plan_dirty-确认粒度)
    15.9 [外部 TASKS.md 修改](#159-外部-tasksmd-修改)

---

## 1. 当前问题分析

### 1.1 宽度固定 260px，不可拖拽

[unified.css:23](D:\my-agent\desktop\src\shells\unified\unified.css#L23) 使用 `grid-template-columns: 260px 1fr` 硬编码宽度。

### 1.2 TASKS.md 只是静态 markdown 渲染

[project-panel.ts:88-92](D:\my-agent\desktop\src\shells\unified\project-panel.ts#L88) 通过 `renderMarkdown()` 渲染。`- [ ] 任务名` 是静态 `<li>`，不可点击交互。

### 1.3 7 层内容垂直堆叠

标题、项目选择器、切换确认卡、plan 确认卡、Tasks/Map tabs、markdown 面板、验收卡——全部垂直堆叠。切换确认和 plan 确认是瞬时状态，不应常驻占位。

### 1.4 更深层的问题：计划没有"主人"

当前 TASKS.md 的读写有三个参与者，但没人真正为"计划"负责：

```
主 Agent ──写──→ TASKS.md ←──读── 侧边栏（被动渲染）
                  
用户 ──聊天说"把测试提前"──→ 主 Agent ──试着改 TASKS.md
                              
但主 Agent 的注意力在写代码上，TASKS.md 对它只是"待办文件"。
当用户说"把 Phase 3 拆细一点"，主 Agent 得从编码上下文切出来，
理解计划结构，改文件，再切回去——两件事都做不好。
```

---

## 2. 设计哲学

三个原则，和上一版"5 个可折叠抽屉"的设计彻底分道扬镳：

### 2.1 侧边栏是任务流的实时投影，不是项目设置面板

侧边栏**始终展示任务流**。地图、验收、项目列表不是"折叠在下面的抽屉"，而是**覆盖式面板**——用时滑出，用完即走。

### 2.2 Plan Agent 拥有计划领域

引入一个专职的 **Plan Agent**（轻量 sub-agent），所有对 TASKS.md / MAP.md / PROJECT.md 的读写都经过它。主 Agent 不直接写 TASKS.md——它向 Plan Agent 报告"完成了什么"，Plan Agent 决定计划如何演进。

### 2.3 "变化"是一等公民

Agent 做任务过程中，计划会变——拆分、新增、重排、跳过。侧边栏不做"静态文档渲染"，而是实时展示变化：新增行淡入、删除行划掉再消失、重排行虚线标记。plan_dirty 不是一行文字，而是可见的 diff。

---

## 3. 整体架构：Plan Agent + 任务流视图

```
          聊天输入
             │
      ┌──────┴──────┐
      │  Plan Agent  │  ← 所有消息先到这里，由它路由
      │  (Haiku)     │
      │             │
      │  判断消息类型 │
      │  ├─ 计划变更 → 自己处理，更新 TASKS.md
      │  ├─ 执行任务 → 透传给主 Agent
      │  └─ 混合     → 提取计划部分 + 透传执行部分
      │             │
      │  管 TASKS.md │
      │  管 MAP.md   │
      │  管 PROJECT  │
      │  管变更日志   │
      └──┬───┬──────┘
         │   │
         │   │ 透传执行类消息
         │   │
         │   └──────────────────┐
         │                      ▼
         │              ┌──────────────┐
         │              │   主 Agent    │
         │              │   (Opus)      │
         │              │              │
         │              │  写代码       │
         │              │  执行工具     │
         │              │              │
         │              │  做完一个 task│
         │              │  ──report──→ Plan Agent
         │              │              │
         │              │  下一步做什么?│
         │              │←─next_task───│
         │              │              │
         └──────────────┴──────────────┘
         │
         ▼
    ┌──────────┐
    │  侧边栏    │  ← Plan Agent 的唯一 UI 表面
    │          │
    │  任务流   │  ← 实时反映 Plan Agent 的内部状态
    │  进度     │
    │  变更提示 │
    │          │
    │  右键菜单 │  ← 用户直接操作计划（拆分/重排/跳过）
    │          │
    │  覆盖面板 │  ← 地图、验收、项目切换
    └──────────┘
```

**关键设计决策**：
- Plan Agent 使用 Haiku 级别模型——大部分操作（勾选、排序）不需要 LLM 调用，只有拆任务、检查依赖需要推理
- Plan Agent 是独立 sub-agent，有自己的消息通道，不与主 Agent 耦合
- 主 Agent 不直接写 TASKS.md——它通过 `report_progress` 告诉 Plan Agent 做了什么

---

## 4. Plan Agent 设计

### 4.1 职责边界

| 职责 | 说明 |
|------|------|
| **接收进度报告** | 主 Agent 完成 task 后告知 Plan Agent"做了什么、实际产出" |
| **更新 TASKS.md** | 根据进度报告和用户操作修改文件 |
| **处理计划变更** | 用户通过聊天或侧边栏提出的拆分/重排/新增/跳过 |
| **维护变更日志** | 记录每次计划变更的 what + why，供侧边栏展示 |
| **依赖检查** | 判断 task 顺序是否合理（如"部署"依赖"测试通过"） |
| **粒度管理** | 主动建议拆过大 task 或合并过碎 task |
| **验收守门** | 确认所有 task 完成且有产出后才允许进入验收 |
| **消息路由** | 判断用户消息应该自己处理还是透传给主 Agent（详见 §5） |

### 4.2 实现形态

**独立 sub-agent，通过 `project.plan.*` 消息族通信。**

```
前端 ←── project.plan.* ──→ Plan Agent ←── report_progress ──→ 主 Agent
                                   │
                                   ▼
                              TASKS.md
                              MAP.md
                              PROJECT.md
```

不内嵌在主 Agent 的 executor 中——那样会让主 Agent 的注意力分散，且无法独立处理用户在侧边栏的操作。

### 4.3 LLM 调用策略

| 操作 | 需要 LLM？ | 说明 |
|------|-----------|------|
| 勾选 task | 否 | 纯行替换 |
| 重排 task 顺序 | 否 | 纯行交换 |
| 拆分子任务 | **是** | 需要理解 task 语义来合理拆分 |
| 新增 task | **是** | 需要理解项目上下文 |
| 检查依赖关系 | **是** | 需要理解 task 之间的逻辑依赖 |
| 判断消息路由 | 大部分否 | 关键词模式匹配；不确定时调一次轻量判断 |
| 响应 plan_dirty | 否 | 检测到变更直接标记 |

大部分时间 Plan Agent 是事件驱动的状态机，LLM 调用按需触发。

### 4.4 对主 Agent 透明

主 Agent 不感知 Plan Agent 的存在。它只做两件事：
- 做完 task → 报告给 Plan Agent
- 收到问题 → 从 Plan Agent 获取"下一步做什么"

主 Agent 不知道"搜索功能"是用户中途插进来的——对它来说，"下一步"永远从 Plan Agent 那取。

---

## 5. 消息路由：聊天框 → Plan Agent → 主 Agent

### 5.1 核心问题

只有一个输入框，但有两个 Agent 在听。用户有灵感时不应该先做一个"该发给谁"的分类决策——灵感就没了。

### 5.2 路由模型

**所有用户消息先经过 Plan Agent，Plan Agent 决定怎么分发。**

| 消息类型 | 例子 | Plan Agent 行为 |
|---------|------|----------------|
| **纯计划变更** | "先做 API 再做 UI"、"把部署提前" | 更新 TASKS.md，不打扰主 Agent，侧边栏显示变化 |
| **纯推进** | "继续"、"开始下一项"、"把那个 bug 修了" | 直接透传给主 Agent |
| **纯任务执行** | "帮我写单元测试"、"重构这个文件" | 透传给主 Agent（这是 HOW，不是 WHAT） |
| **计划+执行混合** | "加个搜索功能，用 ES，现在就做" | 提取计划部分→更新 TASKS.md；执行部分→透传给主 Agent，标注新 task |
| **对当前 task 的反馈** | "路由这块别用装饰器，用蓝图" | 透传给主 Agent（HOW，不是 WHAT） |

判断逻辑不每次都调 LLM。关键词匹配（"先/再/提前/加一个/跳过/拆成"）覆盖大部分情况。不确定时才调一次轻量判断。

### 5.3 交互流示例

```
用户在聊天框输入："我觉得缺个日志系统，先别做部署了，加在 Phase 3 前面"

        │
        ▼
  ┌─────────────────────────────┐
  │  Plan Agent                 │
  │                             │
  │  [分析]                     │
  │  "先别做部署" → 计划变动    │
  │  "加日志系统" → 新 task     │
  │  "Phase 3 前面" → 插入位置  │
  │                             │
  │  [决策]                     │
  │  纯计划层变更，不打扰主 Agent│
  │                             │
  │  [执行]                     │
  │  1. TASKS.md 新增 "日志系统"│
  │  2. "部署" 暂挂             │
  │  3. 标记 plan_dirty          │
  │  4. 推送侧边栏              │
  │     - 新增行淡入            │
  │     - 暂缓行灰色虚线框      │
  │     - banner: "计划变更"    │
  │                             │
  │  [给用户反馈]               │
  │  "已记录。当前主 Agent      │
  │   继续做路由配置。          │
  │   [查看变更]"               │
  └─────────────────────────────┘

主 Agent：继续写路由配置，完全不知道上面发生了。
         做完当前 task 后 Plan Agent 告诉它新的下一步。
```

用户的消息没有被"吃掉"，也没有打断主 Agent。Plan Agent 给了明确反馈——"收到了，已处理，当前不受影响"。

---

## 6. 侧边栏 UI：任务流主视图

### 6.1 整体布局

```
┌──────────────────────┬──────────────────────────────────────┐
│                      │                                      │
│  my-blog   3/7 ▓▓░░  │  topbar: 项目 · my-blog · 3/7 未完成 │
│                      │                                      │
│  ── Phase 1 · 基础 ──│                                      │
│                      │                                      │
│  ✓ 脚手架   done     │                                      │
│  ✓ 数据库   done     │  chat area                           │
│                      │                                      │
│  ● 路由配置  ← 正在做│  ← 当前任务，强调色高亮              │
│  │                   │     agent 活跃时显示 pulse 动画      │
│  │  ○ API 注册       │  ← 主 Agent 拆分出的子任务           │
│  │  ○ 中间件链       │     缩进 + 竖线连接                  │
│  │  ○ 错误处理       │                                      │
│  │                   │                                      │
│  ○ 模板引擎          │                                      │
│                      │                                      │
│  ── Phase 2 · 功能 ──│                                      │
│                      │                                      │
│  ○ 文章 CRUD         │                                      │
│  ○ 评论系统          │                                      │
│  ○ 日志系统  ← 新增   │  ← 用户刚加的，绿色强调渐融入        │
│  ○ 部署      暂缓     │  ← 灰色虚线框                       │
│                      │                                      │
│  ─────────────────── │                                      │
│  计划已变更 · 待确认   │  ← plan_dirty 时浮现的 banner       │
│  [查看变更] [确认]     │                                      │
│                      │                                      │
│  ─────────────────── │                                      │
│  [◎ 任务] [◇ 地图]   │  ← 底部视图切换器                    │
│  [✓ 验收] [☰ 项目]   │                                      │
│                      │                                      │
└──────────────────────┴──────────────────────────────────────┘
        ↑ 可拖拽
```

### 6.2 视图切换器（底部图标行）

侧边栏只有一个主视图——任务流。地图、验收、项目列表是**覆盖面板**，点击底部图标后侧边栏内容水平推出：

```
底部：
  [◎ 任务]  [◇ 地图]  [✓ 验收]  [☰ 项目 (3)]

任务视图：始终显示
地图视图：点击 ◇ → 侧边栏内容滑出 MAP 面板，顶部有 ← 返回按钮
验收视图：点击 ✓ → 验收面板滑出
项目视图：点击 ☰ → 项目列表滑出，包含搜索 + 切换功能
```

### 6.3 任务行样式

```
✓ 已完成任务         ← 文字灰色 + 删除线 + 低透明度
                     ← 已完成任务收缩为紧凑行，不占视觉重点

● 当前任务  ← 正在做  ← 蓝色高亮背景 + 左边圆点 + 字号略大
  │ 子任务 1          ← agent 拆分出的子任务，缩进 + 竖线连接
  │ 子任务 2          ← 无独立 checkbox，跟随父任务状态
  │ 子任务 3

○ 待做任务            ← 正常样式，未勾选

○ 新任务    ← 新增     ← 入场时绿色左边框 + 淡入动画
                     ← 3 秒后渐隐为正常样式

○ 部署      ─暂缓─    ← 灰色 + 虚线边框
```

### 6.4 当前任务高亮规则

按 TASKS.md 行顺序，第一个 `- [ ]` 未完成任务视为"当前任务"。不依赖后端状态——纯前端从解析后的任务列表推导。勾选当前任务后高亮自动移到下一个。

### 6.5 子任务（由主 Agent 自然产生）

主 Agent 做 task 时可能告诉 Plan Agent"这个 task 实际做了 A、B、C 三块"。Plan Agent 记录为子任务，在侧边栏用缩进+竖线表达：

```
  ● 路由配置
  │  ○ API 注册
  │  ○ 中间件链      ← 嵌套在当前任务下，不独立计数
  │  ○ 错误处理
```

子任务不写入 TASKS.md（保持文件简洁），而是 Plan Agent 维护在内存/会话状态中。

### 6.6 进度条（顶部）

```
  my-blog   3/7 ▓▓▓▓▓▓░░░░░
```

始终可见，替代 badge 数字。简单进度条 + 分数。

### 6.7 可拖拽宽度

- CSS 变量 `--sidebar-width`，默认 280px
- 拖拽范围 200px ~ 窗口宽度 50%
- 右边缘 6px 区域响应拖拽
- 持久化到 `localStorage`

---

## 7. 覆盖面板：地图、验收、项目切换

### 7.1 通用行为

- 点击底部图标 → 侧边栏内容**水平推出**，覆盖面板占满侧边栏
- 面板顶部有 `← 返回` 按钮，点击回到任务流
- 同一时间只显示一个面板

### 7.2 地图面板

```
┌──────────────────────┐
│ ← 返回任务    地图     │
│                      │
│  MAP.md 渲染内容…     │
│                      │
└──────────────────────┘
```

纯展示，`renderMarkdown()` 渲染。

### 7.3 验收面板

```
┌──────────────────────┐
│ ← 返回任务    验收     │
│                      │
│  命令：               │
│  python demo_test.py │
│                      │
│  [运行验收]           │
│                      │
│  ✓ 验收通过           │
│  退出码 0（期望 0）    │
└──────────────────────┘
```

- 运行中按钮显示"运行中…" + disabled
- 结果显示后 3 秒自动回到任务视图
- 仅在 confirmed 状态显示入口图标

### 7.4 项目切换面板

```
┌──────────────────────┐
│ ← 返回     我的项目    │
│                      │
│  [搜索或新建…]  [刷新] │
│                      │
│  my-blog    3/7 当前  │  ← 当前项目高亮
│  cli-tool   全部完成   │
│  old-site   0/8       │
└──────────────────────┘
```

点击一个项目：

**如果目标项目无活跃会话 → 直接切换：**

```
┌──────────────────────┐
│ ← 返回     我的项目    │
│                      │
│  切换到 old-site？     │
│  将创建新会话          │
│  [切换]  [取消]        │
└──────────────────────┘
```

**如果目标项目有活跃会话 → 需要确认：**

```
┌──────────────────────┐
│ ← 返回     我的项目    │
│                      │
│  切换到 cli-tool？     │
│  将恢复已有会话        │
│  [切换]  [取消]        │
└──────────────────────┘
```

**不弹全屏 modal。** 确认就在这个侧边栏面板里完成，不遮盖聊天区。用户按 Escape 或点击空白处 → 取消，回到项目列表。

---

## 8. 变更感知与可视化

Plan Agent 每次修改 TASKS.md 后，前端对 "旧快照 vs 新内容" 做 diff，标记三种变化：

### 8.1 新增行

```
  ○ 日志系统  ┃  ← 左侧绿色竖线，持续 3 秒
              ┃     淡入动画，3 秒后融入正常样式
```

### 8.2 删除行

```
  ○ 部署  ─暂缓─  ← 不立即消失，先变成灰色虚线框
                   下一轮 project.state 推送时才移除
```

### 8.3 Phase 重排 / 变更

任务流底部浮现 banner：

```
  ───────────────────
  计划已变更 · 待确认
  + 新增：日志系统
  ~ 暂缓：部署
  ⇅ 重排：Phase 3 前置
  [查看变更] [确认]
```

点击"查看变更"→ 高亮所有变化行，滚动到第一个变化位置。

### 8.4 plan_dirty 自动检测

Plan Agent 维护 TASKS.md 的 Phase 结构指纹。主 Agent 每轮结束后 Plan Agent 对比指纹：
- 指纹相同 → 无变化
- Phase 结构变了（增/删/重排标题）→ 标记 `plan_dirty`
- 只勾选/取消 checkbox → 不算 dirty

---

## 9. 任务交互：右键菜单

用户在任务行上右键 → 弹出上下文菜单（不依赖聊天框）：

```
  ○ 文章 CRUD
       │
       ├─ 拆分…         ← Plan Agent 调 LLM 拆子任务
       ├─ 上移          ← 在 TASKS.md 中交换行
       ├─ 下移
       ├─ 跳过（暂缓）   ← 标灰 + 虚线框，移到 Phase 末尾
       ├─ 标记完成       ← 勾选 [x]
       ├─ 重新打开       ← 取消勾选 [ ]
       └─ 删除          ← 从 TASKS.md 中移除
```

菜单项的行为：
- **拆分**：Plan Agent 分析 task 内容，生成 2-4 个子任务，更新 TASKS.md
- **上移/下移**：纯行交换，不需要 LLM
- **跳过**：不删除，移到 Phase 末尾 + 暂缓标记
- **标记完成/重新打开**：等同于点击 checkbox
- **删除**：从 TASKS.md 移除该行

---

## 10. WS 协议设计

### 10.1 新增消息族：`project.plan.*`

```
前端 → Plan Agent：

  project.plan.toggle_task    {line, done}
  project.plan.reorder_task   {line, new_position}
  project.plan.split_task     {line}
  project.plan.add_task       {phase, description, position?}
  project.plan.drop_task      {line}
  project.plan.skip_task      {line}
  project.plan.unskip_task    {line}
  project.plan.confirm_changes {}

主 Agent → Plan Agent：

  project.plan.report_progress  {task_line, summary, actual_work, subtasks?}

Plan Agent → 前端：

  project.plan.state            {tasks, phases, plan_status, change_log[], fingerprint}
  project.plan.change_accepted  {change_id}
  project.plan.suggestion       {message, kind}    // "Phase 2 过长，建议拆分"
```

### 10.2 `project.plan.state` 详细结构

```json
{
  "type": "project.plan.state",
  "phases": [
    {
      "title": "Phase 1: 基础",
      "tasks": [
        {"line": 0, "text": "初始化项目", "done": true, "status": "done"},
        {"line": 1, "text": "路由配置", "done": false, "status": "current",
         "subtasks": [
           {"text": "API 注册", "done": true},
           {"text": "中间件链", "done": false}
         ]},
        {"line": 3, "text": "模板引擎", "done": false, "status": "pending"},
        {"line": 4, "text": "日志系统", "done": false, "status": "new"},
        {"line": 5, "text": "部署", "done": false, "status": "skipped"}
      ]
    }
  ],
  "plan_status": "plan_dirty",
  "change_log": [
    {
      "id": "ch_001",
      "kind": "add",
      "task_text": "日志系统",
      "phase": "Phase 3",
      "reason": "用户要求：加在 Phase 3 前面",
      "time": "2026-07-29T15:32:00Z"
    },
    {
      "id": "ch_002",
      "kind": "skip",
      "task_text": "部署",
      "reason": "用户要求：先别做部署",
      "time": "2026-07-29T15:32:00Z"
    }
  ],
  "needs_confirm": true,
  "fingerprint": "abc123..."
}
```

### 10.3 保留的现有消息

- `project.state` → 保持原样，作为兼容通道
- `plan.request` / `plan.done` → 保持原样
- `project.switch.request` / `project.switch.done` → 保持原样（但 UI 改为侧边栏内嵌面板，不弹 modal）
- `project.verify` / `project.verify.done` → 保持原样
- `project.list` → 保持原样

---

## 11. 后端变更

### 11.1 `agent-core/project_mode.py`

新增函数：

```python
def toggle_task_line(paths, project_id, line, done) -> dict:
    """替换 TASKS.md 指定行的 [ ] ↔ [x]"""

def reorder_task_line(paths, project_id, line, new_position) -> dict:
    """移动 TASKS.md 中的一行到新位置"""

def add_task_line(paths, project_id, phase_title, description, position) -> dict:
    """在指定 Phase 下插入新 task 行"""

def drop_task_line(paths, project_id, line) -> dict:
    """从 TASKS.md 移除指定行"""

def compute_phase_fingerprint(tasks_text) -> str:
    """计算 Phase 结构指纹（仅 ## 标题，不含 checkbox 状态）"""
    # 已有 phase_fingerprint_from_text() 可复用
```

### 11.2 新增 `agent-core/plan_agent.py`

Plan Agent 的核心逻辑：

```python
class PlanAgent:
    """管理项目计划的轻量 agent。"""

    def __init__(self, paths: AgentPaths, session: Session):
        ...

    def handle_message(self, message: dict) -> RouteDecision:
        """判断消息路由：自己处理 / 透传主 Agent / 混合"""

    def handle_toggle(self, line: int, done: bool) -> dict:
        """处理勾选请求 → 更新 TASKS.md → 返回 plan.state"""

    def handle_split(self, line: int) -> dict:
        """调 LLM 拆分 task → 更新 TASKS.md → 返回 plan.state"""

    def handle_reorder(self, line: int, new_position: int) -> dict:
        """重排 → 更新 TASKS.md → 返回 plan.state"""

    def receive_progress(self, report: dict) -> dict:
        """主 Agent 报告进度 → 更新 TASKS.md → 检查依赖"""

    def next_task(self) -> dict | None:
        """返回下一个未完成 task"""

    def build_state(self) -> dict:
        """构建 project.plan.state 负载"""
```

### 11.3 `agent-core/server.py` — `_dispatch_project`

新增 `project.plan.*` 消息处理分支：

```python
if isinstance(msg_type, str) and msg_type.startswith("project.plan."):
    await self._dispatch_plan(message, repl, bridge)
    return
```

### 11.4 `agent-core/project_api.py`

新增 `dispatch_plan_message()` 函数，对应 `project.plan.*` 消息的处理。

---

## 12. 文件变更清单

### 12.1 前端

| 文件 | 变更量 | 描述 |
|------|--------|------|
| `desktop/src/shells/unified/index.ts` | 大 | DOM 重写（任务流+覆盖面板）、resize 逻辑、右键菜单、Plan Agent 事件处理 |
| `desktop/src/shells/unified/project-panel.ts` | 大 | 新增 `parseTasksMarkdown()`、`renderTaskFlow()`、`renderOverlayPanel()`；废弃旧 render |
| `desktop/src/shells/unified/unified.css` | 大 | CSS 变量、任务流样式、覆盖面板、右键菜单、变更动画 |
| `desktop/src/api/ws.ts` | 中 | 新增 `project.plan.*` 消息类型和发送方法 |

### 12.2 后端

| 文件 | 变更量 | 描述 |
|------|--------|------|
| `agent-core/plan_agent.py` | **新文件** | Plan Agent 核心逻辑 |
| `agent-core/project_api.py` | 中 | 新增 `dispatch_plan_message()` |
| `agent-core/project_mode.py` | 中 | 新增 `toggle_task_line()` 等操作函数 |
| `agent-core/server.py` | 小 | 新增 `project.plan.*` 路由分支 |

---

## 13. 实施阶段

### Phase 1：任务流主视图 + 可拖拽宽度（1 天）

**不涉及 Plan Agent**。纯前端：将侧边栏从 "5 抽屉" 改为"任务流 + 底部图标栏"。

验收标准：
- [ ] 侧边栏默认宽度 280px，可拖拽
- [ ] 任务流解析 TASKS.md 并渲染 Phase 分组
- [ ] `- [x]` → 删除线+低透明度；`- [ ]` → 正常
- [ ] 第一个 `- [ ]` 高亮为当前任务
- [ ] 底部图标栏：任务(当前)、地图、验收(仅 confirmed)、项目
- [ ] 地图、验收、项目切换为覆盖面板（先做面板切换，不连后端）
- [ ] 侧边栏宽度持久化到 localStorage

### Phase 2：Checkbox 交互 + 后端 toggle API（1 天）

验收标准：
- [ ] 点击 checkbox → 乐观更新 + WS `project.task.toggle` → 后端改文件
- [ ] 后端推送 `project.state` 覆盖乐观更新
- [ ] 失败时回退 checkbox + 错误提示
- [ ] 当前任务高亮自动转移

### Phase 3：右键菜单 + 任务操作（1 天）

验收标准：
- [ ] 右键菜单：拆分/上移/下移/跳过/删除
- [ ] 拆分、新增调 Plan Agent LLM
- [ ] 上移下移纯行交换
- [ ] 跳过 → 灰色虚线框 + 移到 Phase 末尾
- [ ] 删除 → 从 TASKS.md 移除

### Phase 4：Plan Agent 核心 + 消息路由（1.5 天）

验收标准：
- [ ] Plan Agent 独立 sub-agent 启动
- [ ] 消息路由：聊天框 → Plan Agent → 主 Agent（或 Plan Agent 自己处理）
- [ ] 主 Agent `report_progress` → Plan Agent 更新 TASKS.md
- [ ] Plan Agent `next_task` → 主 Agent 获取下一步
- [ ] 纯计划变更不打断主 Agent
- [ ] 混合消息正确拆分

### Phase 5：变更可视化 + plan_dirty（0.5 天）

验收标准：
- [ ] 新增行淡入动画（3 秒融入）
- [ ] 删除行先灰再消失
- [ ] Phase 重排标记 + banner
- [ ] "查看变更"高亮差异行
- [ ] "确认变更"消除 banner

### Phase 6：项目切换面板 + 回归测试（1 天）

验收标准：
- [ ] 项目列表搜索过滤
- [ ] 切换确认在侧边栏面板内完成（不弹 modal）
- [ ] 无活跃会话直接切，有活跃会话确认后切
- [ ] 现有功能全回归（project 命令、CLI、chat 不受影响）
- [ ] `agent-core/tests/` 全部通过

---

## 14. 待决问题

以下问题已全部分析，建议方向经讨论后采纳。详见 §15「已决议的设计问题」。

---

---

## 15. 已决议的设计问题

以下 5 项问题已在 2026-07-29 讨论中达成决议。原 §14 中 14.2–14.7 的建议方向也因此确定，一并整理在此。

### 15.1 Task-stop 与 Plan Agent 的关系

**决议：主 Agent 自己停，Plan Agent 有驳回权。**

保持现有 task-stop 机制——主 Agent 每完成一条 TASKS checkbox 后自动停止。有了 Plan Agent 后，区别在于：

- 主 Agent 不自己改 TASKS.md，改完通过 `report_progress` 告知 Plan Agent
- Plan Agent 验证 task 是否**实际完成**（检查有无代码产出、测试是否通过）
- 如果 Plan Agent 判断未完成 → 驳回 report_progress，task 重新标回 `[ ]`，附解释
- 如果完成 → 标记 `[x]`，返回 `next_task`

驳回不会阻止主 Agent 继续——主 Agent 做完 task 已经自己停了。驳回意味着"这个不算，下次继续做"。

### 15.2 Plan Agent 的生命周期

**决议：按项目绑定，不按会话。change_log 跨会话保留。**

- Plan Agent 绑定 workspace 目录（即 `workspace/<project_id>/`），生命周期独立于会话
- 用户"新会话" → 主 Agent 重新初始化，但 Plan Agent 继续运行，change_log 不丢
- 用户关闭 my-agent 时 Plan Agent 的状态序列化到 `workspace/<project_id>/.plan-agent/` 目录
- 再次打开项目 → 恢复 Plan Agent 状态 + change_log
- 项目被删除 → Plan Agent 随之销毁

### 15.3 Plan Agent 不可用时的降级

**决议：软降级。**

降级层级：

| 层级 | 触发条件 | 行为 |
|------|---------|------|
| L1 全功能 | Plan Agent 正常 + LLM 可用 | 所有操作正常 |
| L2 无推理 | Plan Agent 正常 + LLM 不可用（超时/额度） | 勾选、排序正常；拆分、新增 task、依赖检查返回 **"暂不可用"** |
| L3 无 Plan | Plan Agent 进程不可用 | 侧边栏退回纯渲染模式，checkbox 直接通过 `project.task.toggle` 改文件（绕过 Plan Agent） |

降级时侧边栏底部图标行显示状态指示器：

```
  [◎ 任务] [◇ 地图] [✓ 验收] [☰ 项目] [⚠ Plan 降级]
```

点击 ⚠ 展开降级说明。

### 15.4 Plan Agent 的主动性边界

**决议：Plan Agent 只给建议，不自动执行。用户或主 Agent 触发时才动。**

| 场景 | Plan Agent 行为 |
|------|----------------|
| task 连续 3+ 小时未完成 | 侧边栏 banner："此任务耗时较长，是否拆分？" [拆分] [忽略] |
| Phase 超过 12 个 task | banner："Phase 2 有 15 个任务，建议拆分或重排" [查看] [忽略] |
| 检测到外部修改 TASKS.md | banner："检测到外部修改 TASKS.md" [查看变更] [忽略] |
| 建议先做 A 再做 B | **不做。** 顺序判断留给用户，Plan Agent 不替代用户决策 |
| 自动修改 Phase 结构 | **不做。** 只有用户通过右键菜单或聊天明确要求时才修改 |

所有建议类 banner 都有"忽略"按钮，持续 5 分钟后自动消失。用户一天内忽略 3 次同类建议后，当天不再提示同类。

### 15.5 渐进实施路径

**决议：双线并行。UI 线不依赖 Plan Agent 即可单独交付。**

```
线 A（前端 UI）  Phase 1 ──→ 2 ──→ 3 ──→ 5 ──→ 6
                    │
                    │  可独立交付和测试
                    │
线 B（Plan Agent）          Phase 4
                              │
                              └──→ 与线 A 在 Phase 5-6 交汇
```

**关键设计约束**：Phase 1-3 的 checkbox toggle 通过 `project.task.toggle` 直接操作 TASKS.md 文件，不经过 Plan Agent。Phase 4 引入 Plan Agent 后，这些消息改为发送给 Plan Agent（`project.plan.toggle_task`），但旧通道 `project.task.toggle` 保留作为 L3 降级通道。

这意味着：
- Phase 2 结束时用户可以勾选 checkbox，功能完整
- Phase 4 引入 Plan Agent 后获得拆分/重排/依赖检查等高级功能
- 如果 Plan Agent 出问题，系统自动降级回 Phase 2 的直接文件操作模式

### 15.6 Plan Agent 的模型选择

**决议：Haiku 优先，按操作类型分级。**

| 操作 | 引擎 | 说明 |
|------|------|------|
| checkbox 勾选/取消 | 规则引擎 | 纯行替换 |
| 重排 task | 规则引擎 | 纯行交换 |
| 拆分 task | Haiku | 需理解 task 语义，但推理量小 |
| 新增 task | Haiku | 需理解项目上下文 |
| 依赖关系检查 | Haiku | 需理解 task 间的逻辑依赖 |
| 消息路由判断 | 规则引擎（关键词） | 不确定时降级为"透传给主 Agent" |

不引入 Sonnet——拆分和新增 task 的逻辑足够简单，Haiku 能胜任。如果实践发现 Haiku 拆分质量差，后续可升级。

### 15.7 撤销机制

**决议：操作日志 + UI 即时撤销。**

层级：

| 层级 | 机制 | 撤销窗口 | 场景 |
|------|------|---------|------|
| UI 即时撤销 | 操作后侧边栏弹出"已拆分 · 撤销"的 toast，3 秒可点 | 3 秒 | 误操作、反悔 |
| 操作日志回滚 | Plan Agent 保持最近 50 条操作的逆向记录 | 会话内 | 发现之前改错了，需要回退几步 |
| Git 回滚 | `git checkout -- TASKS.md` | 不限 | 大规模误操作、文件损坏 |

UI 撤销就是 toast——用户右键拆分 task 后侧边栏底部弹出"已拆分为 3 项 · 撤销"，点一下恢复。不弹 dialog。

操作日志是 Plan Agent 在内存中维护的环形 buffer，每条记录包含操作前的文件行内容。用户说"撤销刚才那步"时 Plan Agent 回滚。

### 15.8 plan_dirty 确认粒度

**决议：三级自动确认。**

| 变更类型 | 确认方式 | 说明 |
|---------|---------|------|
| checkbox 勾选/取消 | **永远自动** | 不计入 plan_dirty |
| Task 增删 | **30 秒自动确认** | banner 展示 30 秒，用户可点"撤销"；超时默认接受 |
| Phase 增删/重排 | **需要手动确认** | banner 一直显示，直到用户点"确认变更"或"撤销" |
| Task 在 Phase 间移动 | **需要手动确认** | 同 Phase 重排 |

设计理由：checkbox 是最频繁的操作，不需要确认。Phase 结构变更影响 agent 的执行顺序和 task-stop 行为，需要用户知情。

### 15.9 外部 TASKS.md 修改

**决议：不主动监听，依赖 project.state 推送时 diff。**

策略：

- Plan Agent 每次构建 `project.plan.state` 时维护一份 TASKS.md 的**内容快照**（在内存中）
- 下一次收到 `project.state` 请求（来自 turn 结束或用户刷新）时，读当前文件内容 → diff 快照 → 标记变更行
- 如果 TASKS.md 被 git 外部覆盖 → 差异会以"新增/删除"的形式在侧边栏体现
- Plan Agent 的 change_log 仍然以它为权威——外部覆盖造成的 diff 标记为"外部变更"，不是 Plan Agent 发起的

不引入 `watchfiles`——Electron 打包复杂，且外部 git 操作不是主路径。

---

## 附录 A：数据流图（完整）

```
用户操作                          Plan Agent                      主 Agent              文件系统
   │                                │                              │                    │
   │  点击 checkbox                 │                              │                    │
   ├──乐观更新 UI──────────────────→│                              │                    │
   │──project.plan.toggle_task─────→│                              │                    │
   │                                │──读 TASKS.md────────────────→│                    │
   │                                │←──内容───────────────────────│                    │
   │                                │──替换行 [ ]→[x]────────────→│                    │
   │                                │                              │                    │
   │←──project.plan.state───────────│                              │                    │
   │  (覆盖乐观更新)                 │                              │                    │
   │                                │                              │                    │
   │  聊天："加搜索功能，用ES，现在就做"│                              │                    │
   ├───────────────────────────────→│                              │                    │
   │                                │ [分析] 计划变更+执行混合      │                    │
   │                                │──新增 task 到 TASKS.md──────→│                    │
   │←──"已记录"─────────────────────│                              │                    │
   │←──侧边栏刷新（新task淡入）      │                              │                    │
   │                                │                              │                    │
   │                                │  主 Agent 完成路由配置        │                    │
   │                                │←──report_progress────────────│                    │
   │                                │──更新 TASKS.md──────────────→│                    │
   │                                │                              │                    │
   │                                │──next_task: "搜索功能(ES)"───→│                    │
   │                                │                              │ 开始写搜索功能
   │←──侧边栏刷新（路由✓, 搜索●）───│                              │                    │
```

## 附录 B：与旧版设计的关键差异

| | 旧版（5 折叠区块） | 新版（Plan Agent + 任务流） |
|---|---|---|
| 核心隐喻 | 设置面板（5 个抽屉） | 任务流的实时投影 |
| 主视图 | 5 个区块竞争空间 | 任务流独占 |
| 地图/验收/项目 | 折叠在下方的抽屉 | 覆盖面板，用完即走 |
| TASKS.md 所有权 | 无明确 owner | Plan Agent 唯一 owner |
| 用户操作 | 只能勾选 checkbox | 右键菜单：拆分/重排/跳过/删除 |
| 计划变更 | "计划"区块里一行文字 | 任务流内 diff 标记 + 动画 |
| 子任务 | 不支持 | 缩进+竖线，Plan Agent 管理 |
| 消息路由 | 全部发给主 Agent | Plan Agent 路由 |
| 切换确认 | 全屏 modal | 侧边栏内嵌面板 |
| plan_dirty | 手动 detect | Plan Agent 指纹对比自动 detect |
