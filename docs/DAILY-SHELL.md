# 日用壳 · 极致嗨（daily · Amp）

> 版本 **0.3.2** · 2026-07-30  
> 状态：**superseded** — 独立 `shells/daily/` **已删除**；Amp 手感并入 `unified` 的 **`perspective=night`**；starfield 在 `desktop/src/skins/starfield/`。本文保留作设计溯源。  
> 关联：[SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md) · [DESKTOP.md](./DESKTOP.md) §0 · [UX-POLISH.md](./UX-POLISH.md)

---

## 1. 动机与 pivot 史

### 1.1 星图（v0.1 · 已否决）

| 问题 | 说明 |
|------|------|
| 气质错位 | 冷、远、旁观；不像能聊嗨 |
| 实现天花板 | Canvas 粒子易成屏保 |
| 用户反馈 | 「像贴图转圈」「很 low」 |

### 1.2 咖啡馆对谈（v0.2 · 已否决 · 2026-07-12）

| 问题 | 说明 |
|------|------|
| 深木暗底 | 闷；和 grow 纸白反差太硬 |
| busy 同质化 | 与 grow 同款流动，只换 token |
| 角色前缀 | 「你 ·」「助手 ·」别扭 |
| 概念空转 | 「咖啡馆」未落地；画面像暗色聊天窗 |

**决议**：T-904h1–h3 代码 **superseded**；`daily.css` 整文件重做；保留 `index.ts` 功能钩子（recall、confirm、chat-state）。

### 1.3 新方向（已定 · 2026-07-12）

**极致嗨（Amp）** — 打开 daily 像进一场 **你能接住话茬的 live set**：亮、冲、有节奏；字仍然是大哥，嗨在 **底色、动效、排版张力** 里。

| 维度 | 意图 |
|------|------|
| 情绪 | **高能、敢聊、敢接梗**；不是冷静陪伴，不是车间干活 |
| 视觉 | **亮底 + 高饱和霓彩**；拒绝深暗咖啡馆 |
| 动效 | idle 也有轻微 shimmer；busy **比 grow 更快、更花** |
| 信息 | 仍 **零仪表盘**；单栏；无 proposal 顶栏、无 tool 列表 |
| 字 | **更大、更利落**；无 scrim 气泡；无「你 ·」前缀 |

---

## 2. 目标与非目标

### 2.1 目标

1. **嗨感一眼可辨**：和 grow 并排截图，daily 明显更「冲」。
2. **零面板**：单栏对话 + 底栏输入；历史靠 `session.history` / 续接。
3. **对话即唯一内容层**：Markdown 同 grow；中央窄栏。
4. **busy 全壳反馈**：复用 `.is-working` 机制；**色带 / 周期 / 缓动 daily 独占**。
5. **壳保活**：与 grow 切换不丢块、草稿（DESKTOP §3.9.2）。
6. **复用内核**：`chat-state`、WS、confirm 不变。

### 2.2 非目标

| 非目标 | 理由 |
|--------|------|
| 星图 / 粒子星座 | 已否决 |
| 深木暗底咖啡馆 | v0.2 已否决 |
| 三栏 / 会话侧栏 / 「长了什么」 | M0 不做；违背单栏决议 |
| 复制 grow 纸白车间 | 要有 **极致** 区分度 |
| grow 式过程块 / reasoning | daily 默认关 |
| 3D 场景 / 重 canvas | 优先 CSS；M2 才考虑轻量点缀 |

---

## 3. 与 grow 的分工

| | grow · 车间 | daily · Amp |
|---|-------------|-------------|
| 心智 | 造工具、审 proposal | **畅谈、接梗、workflow、qa、recall** |
| 色温 | 纸白赭石、**稳** | **亮底霓彩、冲** |
| 动效节奏 | 5s 暖流、克制 | **3s 多色扫光、更猛** |
| 排版 | 工作台密度 | **更大字号、更紧节奏、无角色前缀** |
| chrome | proposal 顶栏、过程块 | **几乎无** |
| busy | 赭石渐变流动 | **珊瑚 / 品红 / 电黄 / 青** 四色带 |
| 何时切 | coding / evolve / proposal | 聊天、规划、回顾、轻松 Q&A |

**对照原则**：grow = 下午车间；daily = **晚上开麦**。

---

## 4. 画面结构

### 4.1 层级（自底向上）

```text
┌─ app-chrome（全局顶栏）────────────────────────────────────┐
├─ z0  amp-ambient         亮底 + 慢速 shimmer / 色偏漂移（CSS）      │
├─ z1  amp-vignette        极轻边缘收束（可选；勿压暗成咖啡馆）        │
├─ z2  daily-chat-layer    居中对话列（max-width ~40rem）             │
├─ z3  daily-composer      底栏 **胶囊**输入条（发送 ▶）                    │
└─ z4  daily-confirm-glass confirm 毛玻璃（仅等待时）                 ┘
```

**无** starfield、**无** proposal 顶栏、**无** 每条消息 scrim 底。

### 4.2 线框（常态 · idle）

```text
│ ░░ 亮米白底，边缘有极淡霓彩 shimmer，不抢字 ░░░░░░░░░░░░░░░░░░░░ │
│                                                            │
│  你                                                        │
│  这周想先把 Vue 项目跑起来，顺便把 workflow 理顺                 │
│                                                            │
│  助手                                                        │
│  行，先 Node 版本和包管理器对齐，再…（Markdown）                 │
│                                                            │
├────────────────────────────────────────────────────────────┤
│  输入消息…                                      [发送 ▶]    │
└────────────────────────────────────────────────────────────┘
```

- 角色：**grow 式小标题**（`你` / `助手`），**不要**「你 ·」点号前缀。
- 正文：**无**半透明气泡底；可选用户侧 **1px 左边 accent 线**（轻，不包整块）。

### 4.3 线框（助手执行中 · is-working）

```text
│ ████ 全壳四色渐变快扫（3s）+ inset 霓虹描边脉冲 ████████████████ │
│  助手                                                        │
│  正在查…（流式，字号略大）                                      │
├────────────────────────────────────────────────────────────┤
│  处理中…（accent 饱和字）                         [发送 禁用]  │
└────────────────────────────────────────────────────────────┘
```

**不**列 tool 名；**不**展开过程块。

### 4.4 confirm

毛玻璃卡片居中；背景 **维持 is-working 或略降速**，不突然变「冷静咖啡馆」。

---

## 5. 氛围语义（busy / recall）

### 5.0 全窗联动（**DESKTOP §3.2.3 L1**）

busy 时除 `daily-shell` 外，**`.app-frame`** 与 **app-chrome** 顶栏同步渐变（`agent-busy.ts` → `setAgentBusy(busy, "daily")` + `setActiveShell`）。壳内子层高透明玻璃，避免「只有背景在变色」。

### 5.1 状态机

| 状态 | 视觉 |
|------|------|
| **idle** | 亮底 + 慢 shimmer（~10s 周期，幅度小） |
| **is-working** | 全壳 **四色** 渐变流动 + inset 描边脉冲 |
| `confirm.request` | 维持 `is-working`；毛玻璃 confirm |
| `recall` | 对话区最近 k 轮 **accent 左边线 + 略提字号**；无镜头 |

| 事件 | 行为 |
|------|------|
| 用户发送 / `turn.start` / 流式 / tool | `chat.isWorking()` → `is-working` |
| `assistant.done` 且空闲 | 移除 `is-working` |
| `prefers-reduced-motion` | 停流动 → **静态多色 inset 描边**（仍要有色，不是灰） |

### 5.2 与 grow 的动效差异（硬约束）

| 参数 | grow | daily · Amp |
|------|------|-------------|
| 渐变周期 | 5s | **3s** |
| 色带数量 | 3 | **4** |
| 色相 | 赭石暖 | **珊瑚 + 品红 + 电黄 + 青** |
| inset 脉冲 | 2.4s | **1.8s** |
| idle | 无 / 极淡 | **有 shimmer** |

**禁止**与 grow 共用 `--ma-grow-flow-*` 或 `--daily-flow-*` 与 grow token 同值。

---

## 6. 对话层

### 6.1 排版

| 元素 | 样式 |
|------|------|
| 列宽 | `max-width: 40rem` 居中 |
| 角色 | `你` / `助手` 小标题；`text-muted`；**无 · 后缀** |
| 正文 | 比 grow **大一号**（~1.12rem）；`line-height: 1.6`；**高对比深色字 on 亮底** |
| 用户强调 | 可选 `border-left: 3px solid var(--daily-accent)` |
| 弱化 | 最近 **2** 轮全清晰；更早 `opacity: 0.55`；滚入恢复 |
| Markdown | 复用 `markdown.ts`；链接 / 强调用 `--daily-accent` |
| 流式 | 光标或末字 **轻微 accent 闪烁**（可选 i2） |

### 6.2 过程块

| grow | daily |
|------|-------|
| 默认展开过程 | **不渲染** |
| reasoning | **不显示** |
| busy 文案 | `处理中…` / `等待确认…` / `就绪` |

---

## 7. 色系与 token

**气质**：亮底霓彩（R 友好）；顶栏跟全局 theme，daily 区内 **自洽高饱和 accent**。

| Token | 用途 | 参考值 |
|-------|------|--------|
| `--daily-bg` | 底 | `#faf6f2` |
| `--daily-surface` | 输入底 | `#ffffff` |
| `--daily-text` | 正文 | `#1a1210` |
| `--daily-text-muted` | 小标题 | `#6b5d52` |
| `--daily-accent` | 强调 / 发送 / 链接 | `#ff2d92` |
| `--daily-accent-2` | 副强调 | `#ff6b35` |
| `--daily-border` | 边 | `rgba(255, 45, 146, 0.22)` |
| `--daily-flow-a` | 渐变（珊瑚） | `#ff6b6b` |
| `--daily-flow-b` | 渐变（品红） | `#ff2d92` |
| `--daily-flow-c` | 渐变（电黄） | `#ffd93d` |
| `--daily-flow-d` | 渐变（青） | `#4ecdc4` |
| `--daily-shimmer` | idle 漂移 | `linear-gradient(120deg, transparent, rgba(255,45,146,0.06), transparent)` |

亮色全局顶栏时：daily 主区 **仍亮底**；不用深场。

---

## 8. 技术方案

### 8.1 文件布局

```text
desktop/src/shells/daily/
  index.ts          # mount；去角色 · 前缀；保留 recall / confirm
  daily.css         # 整文件重做 · Amp token + 动效
  amp-ambient.css   # 可选：idle shimmer 拆出

# deprecated（h6 删除）
  starfield.ts
  starfield.css
  constellation.ts
```

### 8.2 渲染选型

| 阶段 | 方案 |
|------|------|
| **i1** | **纯 CSS**：四色 `linear-gradient` + `background-position` + idle `@keyframes amp-shimmer` |
| **i2** | `is-working` 绑 WS；发送微动效（`transform` 一帧 bounce，可关） |
| **i3** | recall 聚焦；reduced-motion；可选轻量 canvas 火花（&lt; 20 点，**仅 idle**，禁旋转整层） |

### 8.3 复用

| 保留 | 废弃 |
|------|------|
| `chat-state.ts` | v0.2 咖啡馆 CSS |
| confirm glass | 「你 ·」「助手 ·」模板 |
| `session.history` | 星图 / constellation 写入 |
| `.is-working` 状态机 | grow 同款 token 值 |

---

## 9. 里程碑

| 阶段 | 交付 | 验收 |
|------|------|------|
| **i0** | 本文档 v0.3 定稿 | 用户确认「极致嗨」语义 | **done** |
| **i1** | 亮底 + 新排版 + idle shimmer | 打开 daily **即**感到「冲」 | **done** |
| **i2** | daily 独占 `is-working` 四色快扫 | 执行时一眼不是 grow | **done** |
| **i3** | recall 聚焦 + reduced-motion + 尺寸 | 非最大化正常 | **done** |
| **i4** | 全窗染色 + grow 沉浸 + 柔化 UI | 顶栏与壳同步；胶囊输入 | **done** |

---

## 10. 任务拆分（T-904i · Amp 重做）

| ID | 任务 | 依赖 | 状态 |
|----|------|------|------|
| T-904g | Shell **daily** 总览 | T-904e | **done**（Amp i1–i9） |
| T-904g2 / g6 | 对话层 + chat-state | — | **done**（保留） |
| T-904h1–h3 | ~~咖啡馆视觉~~ | — | **superseded** |
| **T-904i1** | Amp token + 亮底 + idle shimmer | g2 | **done** |
| **T-904i2** | 排版重做（无 · 前缀、胶囊输入） | i1 | **done** |
| **T-904i3** | 四色 `is-working`（≠ grow 周期/色） | i2 | **done** |
| **T-904i4** | recall 对话聚焦 | T-905, i3 | **done** |
| **T-904i5** | reduced-motion + 窗口尺寸验收 | i3 | **done** |
| **T-904i7** | 全窗 app-chrome 染色 | i3 | **done** |
| **T-904i8** | grow 整壳沉浸（交叉） | i7 | **done** |
| **T-904i9** | 隐藏 Electron 系统菜单 | T-904f | **done** |
| T-904i6 | 清理 starfield / constellation（可选） | i2 | defer |

---

## 11. 旧实现处置

| 资产 | 处置 |
|------|------|
| `chat-state` + composer 逻辑 | **保留** |
| `daily.css` v0.2 咖啡馆 | **整文件覆盖** |
| `index.ts` recall / confirm | **保留**；改 `renderTurnBlock` 文案结构 |
| `starfield.ts` 等 | deprecated → i6 删除 |
| `data/constellation.json` | 不再写入 |

---

## 12. 已决（2026-07-12 · v0.3）

| ID | 问题 | 决议 |
|----|------|------|
| **Q1** | daily 视觉主路线 | **极致嗨（Amp）**；否决星图、咖啡馆 |
| **Q2** | 布局 | **单栏**；无侧栏 M0 |
| **Q3** | busy | **全壳变色**；机制同 grow，**色带/节奏 daily 独占** |
| **Q4** | 底色 | **亮底**；不要深木暗场 |
| **Q5** | 角色呈现 | grow 式 `你`/`助手`；**不要 · 前缀** |
| **Q6** | 记忆可视化 | M0 不做；recall 仅对话聚焦 |
| **Q7** | 过程块 / reasoning | **默认关** |

### 12.1 待定（实现前可拍板）

| ID | 问题 | 备选 |
|----|------|------|
| **Q8** | 发送微动效 | **B** 新消息 1 帧 bounce |
| **Q9** | idle 点缀 | **A** 纯 CSS shimmer |
| **Q10** | 流式末字闪烁 | **要**（`▍` accent 闪烁） |
| **Q11** | 全窗 busy | **要** — app-chrome + 壳内玻璃（T-904i7） |
| **Q12** | 输入形态 | **胶囊一体条**；无硬边框（2026-07-12 柔化） |

---

## 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.x | 2026-07-12 | 星图方向 |
| 0.2.0-draft | 2026-07-12 | 咖啡馆对谈（已否决） |
| 0.3.0-draft | 2026-07-12 | **极致嗨 Amp**；h* superseded；新增 i* |
| **0.3.1-draft** | 2026-07-12 | i1–i9 **done**；§5.0 全窗联动；胶囊输入 |
