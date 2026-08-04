# 项目工作台界面重设计（WORKBENCH-UI）

> 版本 **0.2.1** · 2026-08-03 · **状态：M0 done**（Q1～Q3 已按默认签字）  
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

### Q1～Q3（2026-08-02 · 采纳建议默认）

| # | 已决 |
|---|------|
| Q1 | **A** — pet 从默认入口拿掉；顶栏「伴侣窗」/ 托盘可开 |
| Q2 | **A** — 亮/暗在设置（外观）；**不**用 dark 覆盖 `data-perspective` |
| Q3 | **A** — 无项目时空态只允许选/建项目；聊天与拖文件 gated |

---

## 1. 目标体验（对标 Cursor，缩到自己）

| Cursor 感 | 本工作台 |
|-----------|----------|
| 打开就是干活 | 打开就是**某个项目**（或「选项目」空态） |
| Agent 在侧/底，不挡主线 | **聊天默认占主列**；计划 **审阅 / 完整 TASKS** 临时占主列（见 [PLAN-REVIEW-UI.md](./PLAN-REVIEW-UI.md)）；侧栏仅短卡入口 |
| 不切「模式」才能用工具 | **无壳选择器**；无「先进 project 才有工具」 |
| 改文件有着落 | 路径 → 项目账本；侧栏能回看 |

**非目标（本版）**：做成第二个 IDE；保留 pet 浮动球主入口；继续维护 default/night 多套「像另一个 App」的入口。

---

## 2. 信息架构

（同 v0.1：顶栏 + 侧栏加速器 + 主区对话；空态中央选/建项目。）

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

---

## 4. 里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **D0** | 本文拍板 | **done** |
| **M0** | 启动进工作台；空态；侧栏默认展开 | **done** |
| **M1** | 顶栏项目/会话切换打磨；路径写入 → 静默挂账 | todo |
| **M2** | 文档与 DESKTOP §0 同步；可深藏 pet | todo |
| **M3** | 计划审阅主列（[PLAN-REVIEW-UI.md](./PLAN-REVIEW-UI.md) PRU-M0） | todo |

### 手工冒烟（S-340）

1. 启动桌面 → 工作台（非 pet 球）  
2. 无项目 → 空态「新建 / 我的项目」；不能发消息  
3. 新建或选择项目 → 空态消失，可聊  
4. 顶栏「伴侣窗」仍可开 pet；关工作台进托盘不自动弹 pet  

---

## 5. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-02 | 初稿 |
| 0.2.0 | 2026-08-02 | Q1～Q3 已决；M0 实现 |
| 0.2.1 | 2026-08-03 | 主输入 **自动路由** Plan（§15.11.1）；Alt+发送强制主 Agent；见 [PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) |
