# 自我进化 Sandbox（SANDBOX-EVOLUTION）

> 稳定驾驶舱改实验副本，**不碰稳定工作区里的 `desktop/` 与 `agent-core/` 文件**。

## 存储模型（不是两个 Git 仓库）

| 存什么 | 份数 |
|--------|------|
| `.git` 历史与对象 | **1 份**（主目录与 sandbox 共用） |
| 稳定工作区文件 | 主目录检出（如 `main`） |
| 实验工作区文件 | `.worktrees/sandbox/` 检出（如 `experiment/sandbox`） |
| `data/` | **各工作区各一份**（gitignore，互不影响） |
| `desktop/node_modules` | 通常各装一份（磁盘主要增量） |

合入稳定 = `git merge experiment/sandbox`（或 PR），不是复制文件夹。

存储测量、备份归档、依赖预检与尚未完成的验收，见 [双版本存储安全](./SANDBOX-STORAGE.md)。

## 一次性准备

```bat
init-sandbox.bat
```

会创建 `.worktrees/sandbox`、分支 `experiment/sandbox`，并在 sandbox 里 `npm install`。

## 日常规则

1. **稳定**：只用仓库根目录 `start-desktop.bat`；聊天、做项目；**不要**在根目录改实验性壳/工具。
2. **实验**：Agent / 你只改 `.worktrees/sandbox/**`（`agent-core`、`desktop` 等）。
3. **预览**：重启 `start-desktop.bat` 后，**5173 = 稳定 desktop**，**5174 = sandbox desktop**（存在 sandbox 时自动）。预览轨已对齐稳定版的 **生成图 hydrate + lightbox**（`inline_images` / `show_images`）；改完后刷新 5174 或重启 dev。
4. **试新 agent-core**：设置 → 候选 Host 根目录选 `.worktrees\sandbox`（须在 Agent 根下）；会切换 sidecar，**短暂**用于验收，失败会恢复。
5. **合入**（界面或命令行）：
   - 桌面 **壳预览** → **一键合入实验分支**：可自动 **在 sandbox 提交**、**stash 稳定树 product 改动**，再 `git merge experiment/sandbox`（整支，含 **agent-core、desktop**）。做不了的事（缺 sandbox、没有可合入提交等）会一句话说明，不堆手册式提示。**采用预览为主壳**只换 5173/5174 地址，不算合入。
   ```bat
   check-stable-before-sandbox-merge.bat
   git checkout main
   git merge experiment/sandbox
   ```
   检查脚本会确认：**稳定树** `agent-core/`、`desktop/` 等无未提交改动，且 **sandbox 已 commit**；通过后再 merge。合并后测试并 `start-desktop.bat`。

环境变量：

- `MY_AGENT_SANDBOX_ROOT` — 覆盖 sandbox 路径（默认 `<repo>/.worktrees/sandbox`）
- `MY_AGENT_SKIP_PREVIEW_DEV=1` — 不启 5174

## 谁改哪（避免「只能找 Cursor 消化」）

| 场景 | 改哪里 |
|------|--------|
| **桌面里 my-agent 助手**（实验进化会话） | 只写 `.worktrees/sandbox/**`；5174 验收 |
| **Cursor 里你或我** | 可直接改稳定树或 sandbox；不受 `write_scope_root` 限制 |
| **合入稳定** | `check-stable-before-sandbox-merge.bat` → `git merge experiment/sandbox` |

自我进化的闭环是：**你在实验会话里让助手改 sandbox → 5174 看图/用壳 → sandbox 里 commit → merge**，不是「稳定写不进去就只能在本对话里改 stable」。

## 实验进化会话（推荐）

侧栏 **壳预览** → **开实验进化会话**：新建桌面会话，`meta.write_scope_root = .worktrees/sandbox`。

- 写文件类工具（`write_text` / `patch_file` / `write_evolve` / `generate_image` 落盘等）**只能**落在该目录下；稳定树与其它路径可读不可写。
- 会话列表标题带 `[实验]`；主聊天区顶栏显示蓝色写入范围提示。
- 普通稳定会话不受影响；需要改稳定树时请换会话或清空 scope。

## Agent 写入范围（无专用会话时）

自我修改 / 改壳 / 新工具：**仅** `.worktrees/sandbox/` 下路径；稳定树仅只读参考。合入前不要改主工作区对应文件。

关联：[SELF-MODIFICATION-TRANSACTION.md](./SELF-MODIFICATION-TRANSACTION.md) · 壳预览 T-6500b · `switchSidecarCandidate`（`desktop/src/host-settings.ts`）

## Evolution Slice A T9 verification boundary (2026-10-23)

The sandbox T9 scripted code path is implemented: an explicitly authorized `t4-recovery-node-v1` profile admits only the exact three T4 candidate files in addition to the catalog index; those mutable bytes are registered separately from the fixed control-release digest. The trusted Host uses a fixed Node runtime/profile and a descriptor factory bound to the exact candidate snapshot. The CLI now contains the visible H1 launcher and sidecar WebSocket driver, reuses the current Session and existing confirmation receipts, captures the operation journal and process logs, and stops owned components. Agent/M1 remains fail-closed without a provider adapter; scripted mode makes no model call.

No real H1/P1 run was performed. No Electron app/Host/sidecar/WebSocket session/named pipe/candidate/model startup occurred; M1, independent review and security audit remain open. The 2026-10-23 code-only rerun passed the full sandbox `npm run build` and a read-only dependency audit (157 packages, 0 issues). A loopback Vite preview returned the page and six assets with HTTP 200, but rendered visual/interaction acceptance remains open. See [T9 implementation status](reviews/2026-10-22-evolution-a-implementation.md).

## 2026-10-23 开发态双版本合入闭环（sandbox 实现）

日常操作仍在原桌面的「壳开发预览」页面，不要求用户打开终端执行 Git：

1. 在实验版修改并试用；「检查合入状态」显示待保存/待合入内容或阻断原因。
2. 点击「一键合入实验分支」，由稳定 Host 主进程弹出**原生确认**，展示源码、协议、文档和稳定区备份范围。renderer 不能自行提供自动提交/stash 授权、路径或提交说明；测试 Host 不开放采用操作。
3. 确认绑定两个 HEAD、文件内容和暂存区快照；等待确认期间变更会使该次确认失效。同一仓库的合入操作使用互斥锁，不抢占遗留锁，不接管已存在的 merge/rebase/cherry-pick。
4. 只提交已确认的源码发布白名单；协议、文档包含在内。拒绝源码外改动、已提交的敏感路径、链接/子模块及跨仓库 worktree。稳定区范围外未暂存工作不自动提交；范围外已暂存工作直接阻断，避免混入合并提交。
5. 稳定区本地改动按精确路径备份，使用**本次备份对象**恢复并保留原暂存状态。备份按唯一标签识别，保留到用户验收后处理；不执行无引用的 stash pop，也不自动删除其它任务的 stash。
6. 稳定分支移动前，用 merge-tree 和私有索引预检分支合并、本地内容、原暂存状态及未跟踪文件/目录碰撞；Windows 按大小写和尾点/空格别名保守检查。已知冲突先恢复本次备份并停止，稳定 HEAD 不移动，不自动选边。Git stash 自身失败也先捕获本次对象并恢复原暂存状态。无法预见的后续恢复或同步失败仍返回 partial（ok=false），绝不冒充完整成功。
7. 成功后只用 fast-forward 同步实验分支，清除主壳的预览地址偏好；不自动重启当前会话。用户关闭后从原 start-desktop.bat 启动，在稳定版验收。再次实验仍在 sandbox。

边界：这是**开发态 Git 采用流程**，不等于 Evolution E 的正式发行、独立 Launcher、版本/数据迁移或崩溃恢复验收；也不使 P1/H1/M1 自动通过。遗留锁需要检查现场而非自动删除，保留的备份也没有自动清理。Git 外部编辑器/其它 Git 工具不受本应用的互斥锁约束；这不是操作系统级隔离。

控制端更新存在一次性发布边界：**仅把新 renderer 放到预览地址，不会升级正在运行的稳定 Electron main/preload**。本轮代码只写 sandbox，不能据此宣称当前稳定安装已有这套新合入能力。当前 start-desktop.bat 是 Vite 管理的源码热更新入口，合入 main/preload/config 可能在操作未结束时杀掉并重启控制进程。因此新 main 会明确阻断开发态 Host 和测试 Host 的真实采用；仅拷贝几个控制文件或重启开发壳并不足够。下一阶段必须先设计并发布与可变源码分离的固定控制端/独立启动器，再在真实桌面原生确认中验收。该架构工作尚未实现；不以外部 Agent 手工 merge 代替日常产品闭环。

定向验证：在 sandbox/desktop 执行 npm run test:sandbox-adopt。真实 Git 测试仅在 sandbox/artifacts/dual-version-adoption-20261023/git-fixtures 的临时双工作区内做提交、备份、合入、同步，不触碰真实稳定/实验分支；原生 Electron 对话框、真实分支采用及重启仍需分别验收。网页预览没有 Electron preload 时不能代替桌面验收，不注入假 preload 绕过此边界。
