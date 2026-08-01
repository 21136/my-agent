# 拖拽文件设计（FILES-DROP）

> 版本 **0.1.1** · 2026-07-30  
> **状态**：**M0 done**（挂载点现为 **unified / pet**；旧 project/grow/daily 壳路径已删）  
> 关联：[DESKTOP.md](./DESKTOP.md) §0 · [PROJECT-MODE.md](./PROJECT-MODE.md) · [HOST-SCOPE.md](./HOST-SCOPE.md) · [TOOLS.md](./TOOLS.md) §7.1

---

## 0. 已决摘要

| ID | 决议 |
|----|------|
| **F1** | 拖放真源在 **sidecar**；渲染进程只上报本地绝对路径 |
| **F2** | 拖入 **≠** 自动发消息；先 **附件 chip**，用户点发送才进回合 |
| **F3** | 助手仍通过 `read_file` / host builtin / `run_evolved` 动手；**WS 不传文件正文** |
| **F4** | **project 视角**（已绑项目）：区外文件 → `workspace/<project>/_incoming/<drop_id>/` |
| **F5** | **非 project 视角**：区外文件 → `workspace/_drops/<session_id>/<drop_id>/` |
| **F6** | 已在 **host 托管区**内 → **引用** `host:<id>/rel`，不复制（T-1205） |
| **F7** | 已在 **workspace** 内 → **引用** `workspace/rel`，不复制 |
| **F8** | **agent 内部**（`evolve/`、`agent-core/`、`docs/` 等非 workspace）→ **硬拒**（拖放策略；WRITE-SCOPE 放开的是工具写，不是拖放落点） |
| **F9** | 允许 **纯附件** 发送（无文字时注入默认句） |
| **F10** | 计划门 **不挡** 用户拖入 `_incoming/`；**挡** 助手写 `src/`（既有 P10） |
| **F11** | 历史回放：`user` 消息含 `[附件]` 块（服务端拼文本） |

**实现锚点**：`desktop/src/file-drop.ts` · `composer-attachments.ts` · `shells/unified/index.ts` · `shells/pet/`。

## 1. 动机

桌面 Agent 的典型动作是「把本地代码/文档丢给助手」。纯打字描述路径摩擦大；复制进 `workspace/` 再说明也繁琐。

拖拽应成为 **project 视角**（做产物）的一等输入方式，与非项目会话的通用拖放共用协议、分落点策略。

---

## 2. 落点策略

```text
拖入绝对路径
    │
    ├─ agent 内且非 workspace ──► 拒绝
    ├─ 敏感 / 系统路径 ────────► 拒绝
    ├─ 已在 host root 内 ──────► ref = host:<id>/rel（不复制）
    ├─ 已在 workspace 内 ──────► ref = workspace/rel（不复制）
    ├─ project 壳 + 已绑项目 ──► 复制到 <project_root>/_incoming/<drop_id>/
    └─ 其它 ───────────────────► workspace/_drops/<session_id>/<drop_id>/
```

**project 壳 `_incoming/`**：用户导入的参考代码/素材；**不等于**已确认的 `src/` 交付物。助手 `read_file` 可读；要写入项目树仍受 `project_plan_status` 约束。

---

## 3. 交互

| 状态 | UI |
|------|-----|
| `dragover` | composer 虚线高亮 + 「松开以添加文件」 |
| `staged` | chip 列表（文件名 · 大小 · 移除） |
| `confirm` 待决 | 禁止拖入、禁止发送（与底栏锁定一致） |
| 无绑项目（project 壳） | 拖入 → `file.error`「请先打开或新建项目」 |

**M0 拖放区**：project 壳 **composer footer**（`#grow-composer`）。M1 扩至聊天主区。

---

## 4. 限制（M0）

| 项 | 值 |
|----|-----|
| 单文件上限 | 32 MB（超过：暂存失败或仅元数据，见实现） |
| 单次最多 | 20 个文件 |
| 文件夹 | **拒绝**，提示拖入文件 |
| `read_file` 可读 | ≤512 KB UTF-8 文本；否则 `readable_text: false` |

---

## 5. WS 协议

### 5.1 客户端 → 服务端

| type | 载荷 |
|------|------|
| `file.stage` | `{ "paths": ["C:\\…"] }` |
| `file.unstage` | `{ "attachment_id": "…" }` |
| `user.message` | `{ "text": "…", "attachments": ["id", …] }` — `text` 可与 `attachments` 二选一或兼有 |

### 5.2 服务端 → 客户端

| type | 载荷 |
|------|------|
| `file.staged` | `{ "items": [{ "id", "name", "ref", "size", "mime", "readable_text", "copied" }] }` |
| `file.unstaged` | `{ "attachment_id" }` |
| `file.error` | `{ "message", "path"? }` — 单文件失败不阻断同批其它文件 |

### 5.3 注入 LLM 的用户消息

```text
[附件]
- main.py → workspace/doudizhu/_incoming/a1b2/main.py (4.0 KB, text/x-python)
- spec.pdf → host:desktop/spec.pdf (1.2 MB, 不可直接 read_file)

请把这些模块并进当前项目
```

---

## 6. Electron

- preload 暴露 `getPathForFile(file)`（`electron.webUtils`）。
- `dragover` / `drop` 在 composer；`preventDefault()`。
- pet 壳拖放：**done**（与 unified 共用 `file-drop.ts`）。

---

## 7. 模块

| 路径 | 职责 |
|------|------|
| `agent-core/file_stage.py` | 路径判定、复制、session 级 staged 表、消息块格式化 |
| `agent-core/server.py` | 路由 `file.*`、扩展 `user.message` |
| `desktop/src/file-drop.ts` | drag 监听、chips、WS |
| `desktop/electron/preload.ts` | `getPathForFile` |
| `desktop/src/shells/unified/index.ts` | 挂载点（+ pet） |

---

## 8. 安全

| 动作 | confirm |
|------|---------|
| 复制到 `_incoming/` / `_drops/` | 否（等同用户自行放入 workspace） |
| 引用 `host:` 只读 | 否 |
| denylist / agent 内部 | 硬拒 |

不把文件正文写入 WS 或 `evolve_log`（对齐 TOOLS §10）。

---

## 9. 里程碑

| ID | 范围 | 状态 |
|----|------|------|
| T-1201 | `file_stage.py` + `file.stage` | **done** |
| T-1202 | preload + `file-drop.ts` | **done** |
| T-1203 | `user.message` + 附件注入 | **done** |
| T-1204 | **project 壳**拖代码验收 | **done** |
| T-1205 | host 内免复制 | **done** |
| T-1206 | grow / daily 复用 | defer |
| T-1207 | `session.history` 附件展示 | defer |
| T-1208 | pet 壳 | defer |

**M0 完成标志**：project 壳绑定项目 → 拖入区外 `.py` → chip → 发送 → 助手首轮 `read_file` 成功。

---

## 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-12 | 初稿；**F4** project 优先 `_incoming/`；开放问题 1–4 已决 |
