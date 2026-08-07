# 代码库语义搜索（CODEBASE-SEARCH）

> 版本 **0.1.1** · 2026-08-07 · **状态：Fable5 评审已吸收 · 实现 todo（Pack 5）**  
> 父文档：[ROADMAP-PACK-1245.md](./ROADMAP-PACK-1245.md) §6  
> 承接：[CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) Track I M2 · 原 T-4225 → **T-5500 done(doc)**  
> 关联：[TOOLS.md](./TOOLS.md) · `glob_file_search` · [WRITE-SCOPE.md](./WRITE-SCOPE.md) · [PROJECT-MODE.md](./PROJECT-MODE.md)

---

## 0. 一句话

在 **Glob 按文件名** 之上，增加 **`codebase_search` 按语义找代码片段**；索引限定 **当前 project_root**，本地存储；**M0 默认本地 BM25（方案 C）**，外部 embedding API（方案 A）须 **显式 opt-in**。

---

## 1. 动机

| 工具 | 擅长 | 弱项 |
|------|------|------|
| `glob_file_search` | 按路径模式找文件 | 不知「登录逻辑在哪」 |
| `grep` | 按字符串找内容 | 同义词/概念漂移 |
| `list_dir` | 浅列目录 | 大仓递归慢 |

Cursor `@codebase` 类体验缺口；Pack 5 补此层（**栈-B** · LDM §6）。

---

## 2. 已决（CS 系列）

| ID | 决议 |
|----|------|
| **CS-1** | 新 builtin **`codebase_search`**（只读）；**不**新增 evolved 嵌套 |
| **CS-2** | 默认索引范围 = **当前绑定 `project_root`**；未绑项目时 = **agent root 下 evolve+workspace**（可配置缩窄） |
| **CS-3** | 索引存 `data/indexes/<project_id>/`（gitignore）；含 `meta.json` + chunk 文件 |
| **CS-4** | 分块：按文件 · 语言感知可选 M1；默认 **按行块 80～120 行** overlap 20 |
| **CS-5** | 查询返回 **top_k≤8** 片段（path + line range + score + 短摘要） |
| **CS-6** | **confirm 免确认**（与 grep 同级） |
| **CS-7** | 索引刷新：**手动** `codebase_index_refresh` tool 或 project 壳首次绑定后台一次；**不**每 turn 全量 |
| **CS-8** | **M0 必做**（T-5501）：walk 时尊重 **`.gitignore`** + **内置 deny**（见 §2.1）；**禁止**将 deny 外文件送入 embedding |
| **CS-9** | **M0 默认检索后端 = 方案 C（BM25）**；方案 A（外部 embedding API）= **数据外发面**，须 `MY_AGENT_CODEBASE_EMBED=1` 或首次绑项目时用户确认 |
| **CS-10** | 方案 A 不可用时（无 key / API 失败）→ **自动降级 C**，写 `index meta.backend=bm25` + notice |

### 2.1 内置 deny（M0 · 与 glob 对齐并扩展）

| 模式 | 说明 |
|------|------|
| `node_modules/` · `.git/` · `__pycache__/` · `dist/` · `build/` · `target/` | 构建/依赖树 |
| `*.min.js` · `*.map` · 大二进制（>512KB） | 产物/噪声 |
| `.env` · `.env.*` · `*credentials*` · `*secret*` · `*.pem` · `*.key` | 密钥面（路径段匹配） |

### 非目标

| 非目标 | 说明 |
|--------|------|
| 替代 grep | 内容精确匹配仍用 grep |
| 全 agent 盘自动索引 | 默认仅 project_root |
| 云端索引服务 | 本地-only（LDM-1） |
| IDE LSP 符号跳 | 不做 |
| M0 默认外发 embedding | **禁止**（CS-9） |

---

## 3. Embedding / 检索后端（签字项）

| 选项 | 说明 | 默认 |
|------|------|------|
| **A** | 与主 LLM 同厂商 embedding API（env `EMBEDDING_MODEL`） | **opt-in only**（`MY_AGENT_CODEBASE_EMBED=1`） |
| **B** | 本地 `sentence-transformers` 小模型 | M2 可选 |
| **C** | 无 embedding，**BM25 关键词** | **M0 默认** |

**CS-Q1（已关闭）**：M0 ship **C 为主**；A 为增强档，编码前须在 UI/ENV 有 opt-in。**T-5501 编码前**本节约束为硬门槛。

**方案 A opt-in 文案（首次绑定提示 · 草案）**：「语义索引可将代码片段发往 embedding API；默认仅本地 BM25。开启？」

---

## 4. API 草约

### 4.1 `codebase_search`

```json
{
  "query": "用户登录 JWT 校验在哪",
  "top_k": 5,
  "path_prefix": "backend/"
}
```

**返回**（ToolResult data）：

```json
{
  "hits": [
    {
      "path": "backend/auth.py",
      "start_line": 40,
      "end_line": 88,
      "score": 0.82,
      "snippet": "..."
    }
  ],
  "index_stale": false,
  "backend": "bm25"
}
```

### 4.2 `codebase_index_refresh`（M0 可合并为 search 内 lazy refresh）

```json
{ "force": false }
```

---

## 5. 实现落点（T-5501～5502）

| 模块 | 文件 | 职责 |
|------|------|------|
| 分块+索引 | `agent-core/codebase_index.py` | walk（**deny+gitignore**）· chunk · embed/BM25 · save |
| 查询 | 同上 | load · rank |
| builtin | `tools/builtin/codebase_search.py` | schema + run |
| 可选 refresh | `tools/builtin/codebase_index_refresh.py` 或 evolved | 重建索引 |
| 路径 | `paths.py` | `index_dir(project_id)` |

### 5.1 与 loader

- INDEX 脚注一行：`codebase_search` = 语义找代码；glob = 按名；grep = 按串
- `core.txt`：大仓找逻辑 **先 semantic 或 glob**，再 read（E+F 短句，不长教程）

---

## 6. DOC-04

| 面 | 档位 | ID |
|----|------|-----|
| builtin 列表 | P1 | IT-550 |
| 路径越界 | P0 | IT-551 |
| deny / 密钥路径不入索引 | P0 | IT-551b |
| gitignore 跳过 | P0 | IT-552（与 T-5501 同测） |
| stale / refresh 提示 | P1 | **IT-553** |
| 手工 | P1 | **S-550** |

### IT-550

- 临时 project 含 `auth.py` 与 `login_handler.ts`；query「JWT login」→ 命中 auth 相关块

### IT-551 / IT-551b

- `path_prefix` 越 project_root 拒绝
- 含 `.env` / `credentials.json` 路径 **不**出现在索引 chunk 列表

### IT-552

- `node_modules/foo.js` 被 gitignore/deny 排除

### IT-553

- 修改源文件后 `index_stale=true`；`force` refresh 后 `false`

### S-550

- huiyi：「会议列表 API 在哪」→ 一轮 search + read 定位主文件

---

## 7. 任务表

| ID | 内容 | 状态 |
|----|------|------|
| T-5500 | 本文 + ROADMAP 挂钩 | **doc** |
| T-5501 | M0：**deny+gitignore** + BM25 index + `codebase_search` + IT-550/551/551b/552/553 | **done** |
| T-5502 | M1：增量 refresh · 方案 A opt-in UI/env（IT-553 回归） | **done** |
| T-5503 | M2：本地 embedding（方案 B） | defer |
| S-550 | 手工 smoke | **done** |

---

## 8. 开放问题

| # | 问题 | 默认 |
|---|------|------|
| CS-Q2 | chunk 是否 AST 感知（py/ts）？ | M2 defer |
| CS-Q3 | 索引是否含 `docs/`？ | **默认含 project_root 全部文本**；二进制跳过 |

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-07 | 初版：自 T-4225 升格为 Pack 5 真源 |
| 0.1.1 | 2026-08-07 | **Fable5 P0**：deny/gitignore 前移 M0 · 默认 BM25 · A opt-in · IT-553 |
| 0.1.2 | 2026-08-07 | T-5502 验收与 ROADMAP 对齐（IT-553 回归） |
