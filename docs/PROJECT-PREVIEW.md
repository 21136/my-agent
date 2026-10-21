# 项目内嵌预览（PROJECT-PREVIEW）

> 版本 **0.1.0** · 2026-10-21 · **状态：设计已签 · 实现未开始**  
> 关联：[WORKBENCH-UI.md](./WORKBENCH-UI.md) · [DESKTOP.md](./DESKTOP.md) · [BROWSER.md](./BROWSER.md) · [PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) · [PROJECT-VERIFY.md](./PROJECT-VERIFY.md) · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md) · [AGENT-TOOL-EXPERIENCE.md](./AGENT-TOOL-EXPERIENCE.md) · Phase **65** · [TASKS.md](./TASKS.md) T-6500～T-6515

---

## 0. 一句话

在 **项目工作台** 里用 **受控 WebContentsView** 打开用户登记的 **本地/测试前端 dev 整站**（`http://127.0.0.1:端口` 等），与终端里起的后端照常通信；**人和 Agent 围绕同一块实时 UI** 协作（截图、控制台摘要、点选注释），**不**自研浏览器内核，**不**做成通用上网浏览器。

对标心智：**Codex App 内嵌浏览器 + 本地 dev**（见 [OpenAI · In-app browser](https://developers.openai.com/codex/app/browser)）；实现锚点：**现有 B 站 `WebContentsView` 样板**（`desktop/electron/main.ts` · `music-view-bounds.ts` · `unified/index.ts` 音乐 slot）。

**明确不做（本 Phase）**：M6 — Playwright 深检、完整网络面板、操作录制、e2e 生成等（见 §8）。

---

## 1. 已决

| # | 结论 |
|---|------|
| 1 | 预览 = **活网页**（整站 SPA），不是把 DOM 塞进聊天 React 树 |
| 2 | 载体 = **`WebContentsView`**；禁用 `<webview>`、弃用的 `BrowserView` |
| 3 | 主进程管 **创建/定位/显隐/销毁**、**导航规则**、**Session partition**；渲染进程只提供 **占位 slot + 地址栏/环境 UI** |
| 4 | 页面内容 **不可信**：`contextIsolation` + `sandbox` + 无 Node；预览页 **无** 应用 `preload` / IPC |
| 5 | Agent **默认不**自动灌整页 HTML / Cookie / 密码；观察靠 **用户确认或工具门控** 的截图与摘要 |
| 6 | 起停进程走 **sidecar 已有执行层**（`run_command` / `run_service` / `local_preview`）；**禁止**渲染进程 `spawn` |
| 7 | 与 `browser_open`（系统浏览器）并存：外网、强 SSO、扩展依赖 → **外开** |
| 8 | 里程碑 **M0～M5** 为产品目标；**M6 不纳入**本 Phase |

---

## 2. 目标体验（用户故事）

1. 绑定项目后，工作台有 **「预览」** 主区（与聊天、文档、音乐等并列）。
2. 用户在本地起 **后端**；在预览里 **打开已登记的前端 dev URL**（或一键起登记过的 `npm run dev` 再等端口）。
3. 预览里跑的是 **完整前端**（路由、HMR、调本机 API），与在 Chrome 开 `localhost` 等价。
4. 用户 **截图附到对话**，或进入 **点选/注释** 模式，让 Agent 改 UI。
5. Agent 通过工具拉 **当前帧 / console 摘要**（在允许策略下），改码后可再看同一预览。

---

## 3. 架构五块

### 3.1 页面视图（UI + Electron）

| 层 | 职责 |
|----|------|
| **unified 工作台** | 地址栏、环境列表、预览占位 `[data-preview-web-slot]`、`ResizeObserver` / scroll / resize 同步 bounds（复用音乐页模式） |
| **主进程** | `PreviewViewManager`：环境 id → `WebContentsView`；`setBounds` / `setVisible`；切主区时隐藏 |
| **参考代码** | `music:open-bilibili-search` · `music:set-bilibili-view-state` · `clampMusicViewBounds` |

目标模块（实现时抽取，音乐可后续迁入同一 manager）：

```text
desktop/electron/
  preview-view-bounds.ts      # 泛化 music-view-bounds
  preview-url-policy.ts       # 登记 origin 校验（泛化 bilibili-search）
  preview-view-manager.ts
desktop/src/shells/unified/
  preview-panel.ts            # 预览主区 UI
```

### 3.2 测试环境登记与导航规则

每项目一份配置（建议路径，实现可微调）：

`workspace/<projectId>/.my-agent/preview-environments.json`

```json
{
  "version": 1,
  "environments": [
    {
      "id": "web-dev",
      "name": "前端 dev",
      "startUrl": "http://127.0.0.1:5173",
      "allowedOrigins": ["http://127.0.0.1:5173"],
      "partition": "persist:project-<id>-preview-web-dev",
      "start": {
        "cwd": "frontend",
        "command": "npm run dev",
        "ready": { "type": "tcp", "host": "127.0.0.1", "port": 5173 }
      }
    }
  ]
}
```

**URL 策略**（主进程强制）：

- 仅 `http:` / `https:`；禁 `javascript:`、`file:`、URL **userinfo**
- 导航 / 重定向 / `window.open`：目标 origin 须在 **该环境的 `allowedOrigins`**（精确 scheme + host + port，**无**通配域名）
- SSO：IdP origin **须单独登记**；禁止「跟一次跳转自动放开整站」
- 未登记：拦截或对话框确认 / `shell.openExternal`（产品默认：外开需确认）

**IPC**：所有 `preview:*` handler 校验 `event.sender === workbenchWindow.webContents`。

### 3.3 会话隔离

- 每环境独立 **`session.fromPartition(partition)`**
- `persist:` 前缀 → 登录态持久化（仅预览 profile，非系统 Chrome）
- UI：**清除本环境登录态** → `session.clearStorageData()`（范围实现时列清 cache/cookies）
- 复杂企业 SSO：文档引导 **系统浏览器**；内置预览优先 **loopback + 简单登录**

### 3.4 Agent 观察接口

| 通道 | 内容 | 默认 |
|------|------|------|
| 用户 **截图附到对话** | `webContents.capturePage()` → temp → 现有附件管线 | M0 |
| **console / 加载失败** | `console-message`、`did-fail-load` 环形缓冲 | M0 摘要 |
| 工具 **`preview_capture`** | Agent 请求当前帧（会话/项目策略门控） | M2 |
| 工具 **`preview_console`** | 最近 N 条 error / 未处理 rejection 摘要 | M2 |
| **点选检查** | 用户开启后主进程注入 **极简** 拾取脚本 → selector + role + 小图 | M3 |
| **页内 pin 注释** | 多条 pin → 一条改 UI 任务 | M3 |

**禁止默认**：整页 `document.documentElement.outerHTML`、Cookie 全文、未脱敏表单值进模型上下文。

与 Phase 61 **T-6206 `browser_inspect`**：共用 **allowlist 语义**；headless Playwright 属 **M6 外**，不在本 Phase 实现。

### 3.5 本地服务生命周期

| 动作 | 执行方 |
|------|--------|
| 启动 dev | sidecar：`run_service` 或登记命令 + 端口等待（对齐 `local_preview`） |
| 停止 dev | 用户确认后停对应 service / 进程组 |
| 预览 `loadURL` | 主进程，**仅在 ready 探活通过后**（M1） |
| 日志 | 预览旁 dock **stdout/stderr**（EXEC-OBSERVABILITY 风格） |

`evolve/tools/coding/local_preview`：**打开外网浏览器**逻辑保留；扩展事件「工作台预览已打开同一 URL」（M1）。

---

## 4. 里程碑 M0～M5

### M0 · 挂上 dev + 人工观察（最小可用）

| 交付 | 验收 |
|------|------|
| 项目主区 **预览** + web slot | 绑定项目可见；未登记有空态 |
| 登记 **单环境** loopback `startUrl` + `allowedOrigins` | 配置校验失败有明确错误 |
| `WebContentsView` 加载登记 URL | 整站可点；API 调本机后端（与用户自己起服一致） |
| 显隐 / bounds 同步 | 切离预览主区则隐藏 view；resize 不漂移 |
| **截图附到对话** | 图进 composer 附件 |
| console / 加载失败 **条数摘要**（可选展开） | 不自动写入模型，仅 UI |

**任务**：T-6500～T-6504 · **IT-6500** · **S-6500**

### M1 · 起停服务 + 与 local_preview 联动

| 交付 | 验收 |
|------|------|
| 环境 `start` 块：command、cwd、ready 探活 | 失败展示日志尾部 |
| **停止 dev** 与 **关预览** 分离 | 关预览不杀进程 |
| 前后端 **两条环境**（可选编排：先后启动） | 文档示例 + 一项 IT |
| Agent / 用户 `local_preview` 成功后 **工作台自动打开同 URL** | 同一登记 origin |

**任务**：T-6505～T-6508 · **IT-6501** · **S-6501**

### M2 · Agent 工具读预览

| 交付 | 验收 |
|------|------|
| `preview_capture`（evolved 或 builtin 适配层） | 预览未开时明确 blocked |
| `preview_console` / `preview_errors` | 仅登记 origin 内；输出有上限 |
| patch 应用后 **可选** 静默截图进工具结果 | 默认关或项目设置 |
| 与 `run_project_tests` / 验证绿 **提示看预览** | 文案 + 可选一键聚焦预览 |

**任务**：T-6509～T-6511 · **IT-6502** · **S-6502**

### M3 · Codex 式协作（指哪打哪）

| 交付 | 验收 |
|------|------|
| **点选检查模式** | 附：小图 + selector/accessible name + bbox |
| **页内 pin 注释** | 多条 pin → 生成结构化用户消息 |
| **地址栏 / 路由同步** | Agent 可请求 `preview:navigate`（policy 内） |
| **设备视口** 预设 | 375 / 768 等 |
| 刷新 / 硬刷新 / **清缓存**（单环境） | 主进程 API |

**任务**：T-6512～T-6514 · **IT-6503** · **S-6503**

### M4 · 多环境与联调

| 交付 | 验收 |
|------|------|
| **多环境切换**（dev / staging / Storybook 等） | 各独立 partition |
| `.env` / 第二套 command 登记 | 文档约定 |
| SSO **persist + 清除** | 未登记 IdP 不能 silent 跳转 |
| HMR 后用户/Agent 再截帧 | 手工 S 项 |

**任务**：T-6515～T-6517 · **S-6504**

### M5 · 工作台集成

| 交付 | 验收 |
|------|------|
| 预览旁 **dev 日志 dock** | 接 sidecar 服务输出 |
| 文件/路由 **弱联动**（可选 `routes` 约定） | 无约定则 noop |
| `deliverable_review` **附预览截图** 证据 | 可选字段 |
| 验证轨 **预览可达** 谓词（可选） | 与 RUNAWAY verification 门同风格 |

**任务**：T-6518～T-6520 · **S-6505**

---

## 5. IPC 草案（preload → main）

| 通道 | 说明 |
|------|------|
| `preview:get-config` | 读当前项目环境列表（或仅渲染进程读文件 + main 校验写） |
| `preview:open` | `{ envId, bounds }` |
| `preview:set-state` | `{ visible, bounds?, envId? }` |
| `preview:navigate` | `{ envId, url }` policy 通过才 load |
| `preview:reload` / `preview:hard-reload` | |
| `preview:clear-session` | `{ envId }` |
| `preview:capture` | → temp 路径 |
| `preview:inspect-toggle` | 开启/关闭点选 |
| `preview:set-viewport` | `{ width, height, deviceScaleFactor? }` |

类型定义进 `preload.ts` · `MyAgentDesktopApi`；实现前 **禁止** 扩大为任意 URL 无登记加载。

---

## 6. 安全与权限

- 遵循 Electron 远程内容建议：沙箱、上下文隔离、限制导航与弹窗、校验 IPC 来源。
- **预览 WebContents** 与主工作台 **不同 partition**；不与 `bilibili-search` 共用（除非日后统一 manager 仍保持 partition 隔离）。
- 截图/注释进对话 = **用户显式动作** 或 **工具确认策略**（M2）。
- 登记命令执行 = **现有确认管线**（danger / 项目 ENV 允许列表）。

---

## 7. 与现有能力分工

| 能力 | 角色 |
|------|------|
| `browser_open` | 系统浏览器；外网、分享链接 |
| `http_request` | 探活 / 读 body |
| `local_preview` | 起服 + 探活 +（原）外开；M1 起 **联动工作台预览** |
| `run_service` | 长驻 dev 进程与日志 |
| 音乐 B 站 view | **参考实现**；后期迁入 `PreviewViewManager` 或保持独立 partition |
| T-6206 `browser_inspect` | 未来 headless；**共用 allowlist 配置**，不重复造登记 |

---

## 8. 非目标（含明确排除的 M6）

- 通用浏览器、书签、任意上网
- Playwright/trace/无头回归 **作为本 Phase 交付**（原 M6）
- 完整 DevTools / 网络面板克隆
- 渲染进程任意起进程
- 未登记 URL 静默加载
- 自动把 Cookie/整页 DOM 送入 LLM

---

## 9. DOC-04 准入（Phase 65）

| 项 | 内容 |
|----|------|
| **矩阵影响** | Desktop unified 工作台 · Electron 主进程 · 项目绑定写 scope · 工具/确认管线（M2+） |
| **回归** | IT-6500～IT-6503 · S-6500～S-6505（见 [TASKS.md](./TASKS.md)） |
| **Terminal** | 非主战场；仅 BUG-P0 安全时可跟端口 |

---

## 10. 实现顺序建议

1. **M0**：`preview-view-manager` + unified 预览主区 + 配置 JSON + 截图 + 摘要  
2. **M1**：起停 + `local_preview` 联动  
3. **M2**：Agent 工具 + 验证提示  
4. **M3**：点选 + pin + 视口  
5. **M4**：多环境 + 登录态  
6. **M5**：日志 dock + 审查/验证挂钩  

音乐 WebContentsView **可不阻塞 M0**；M0 验收通过后再 refactor 复用 manager。

---

## 11. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-10-21 | 初稿：M0～M5 产品目标；M6 排除；五块架构与任务 ID |
