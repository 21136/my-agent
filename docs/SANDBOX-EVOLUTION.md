# 自我进化 Sandbox（SANDBOX-EVOLUTION）

> 稳定驾驶舱改实验副本，**不碰稳定工作区里的 `desktop/` 与 `agent-core/` 文件**。

## 你要的工作方式（产品契约）

在 **实验进化会话**里提需求：加功能、加工具、改架构。**开实验进化会话**会走 **sandbox sidecar（8766）**，直接试跑 sandbox 里的 agent-core；稳定 **8765 / 5173** 并行，不合入前稳定 product 树仍不被改写。

| 期望 | 含义 |
|------|------|
| 稳定版「还是那样」 | 不合入前，根目录 `agent-core/`、`desktop/` 不被助手写入；**5173 主壳**照常 |
| 实验版「变成你要的」 | 改动在 sandbox worktree；**5174** 预览那套 UI/代码 |
| 不合入也安全 | 实验做坏了、**5174 打不开**，不应等于稳定不可用；稳定仍可聊天、做项目、用 Cursor 修 sandbox |
| 稳定能修实验 | 在稳定环境只读看、在 `.worktrees/sandbox` 里改；或合入前由你在 Cursor 里 patch sandbox |
| 满意再合入 | **一键合入**整支分支；合入后 **同步 sandbox**，避免 5174 仍停在旧树 |

**执行与写入（实现口径）：**

- **双 sidecar**：稳定 **8765**（根目录 agent-core）+ sandbox **8766**（`.worktrees/sandbox` agent-core），各用各自 `data/` 与 interface lock，**互不影响**。
- **壳预览 → 开实验进化会话**：连 **8766**，在 sandbox 里**试跑新工具 / 新 agent-core**；顶栏会标「运行在 sandbox agent-core」。
- **普通稳定会话**：仍走 **8765**；需要只改 sandbox 文件、仍用稳定运行时，可用带 `write_scope_root` 的实验会话（侧栏标题 `[实验]`）。
- **验收**：5174 = sandbox `desktop`；5173 = 稳定 `desktop`（两条 Vite，互不影响，除非你把主窗口绑到 5174）。

**仍要注意：**

- 实验会话数据在 **sandbox `data/`**；稳定会话在 **稳定 `data/`**；侧栏列表会合并两边，切换会话会自动换 WS 端口。
- sandbox sidecar 崩了 **不应**拖垮 8765；稳定照常聊天、做项目。
- 「采用预览为主壳」只换 5173/5174 地址，**不是**换 agent 运行时；合入/同步后应 **主壳恢复稳定 5173**。

## 存储模型（不是两个 Git 仓库）

| 存什么 | 份数 |
|--------|------|
| `.git` 历史与对象 | **1 份**（主目录与 sandbox 共用） |
| 稳定工作区文件 | 主目录检出（如 `main`） |
| 实验工作区文件 | `.worktrees/sandbox/` 检出（如 `experiment/sandbox`） |
| `data/` | **各工作区各一份**（gitignore，互不影响） |
| `desktop/node_modules` | 通常各装一份（磁盘主要增量） |

合入稳定 = `git merge experiment/sandbox`（或 PR），不是复制文件夹。

## 一次性准备

```bat
init-sandbox.bat
```

会创建 `.worktrees/sandbox`、分支 `experiment/sandbox`，并在 sandbox 里 `npm install`。

## 日常规则

1. **稳定**：只用仓库根目录 `start-desktop.bat`；聊天、做项目；**不要**在根目录改实验性壳/工具。
2. **实验**：Agent / 你只改 `.worktrees/sandbox/**`（`agent-core`、`desktop` 等）。
3. **预览**：重启 `start-desktop.bat` 后，**5173 = 稳定 desktop**，**5174 = sandbox desktop**（存在 sandbox 时自动）。预览轨已对齐稳定版的 **生成图 hydrate + lightbox**（`inline_images` / `show_images`）；改完后刷新 5174 或重启 dev。
4. **试新 agent-core**：优先 **壳预览 → 开实验进化会话**（8766）；旧路：设置 → 候选 Host 切到 sandbox（**会替换全局 sidecar**，不如双轨）。
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

## 合入后必修（双轨最容易漏的一步）

稳定上 `git merge experiment/sandbox` **不会**自动更新 `.worktrees/sandbox` 里的文件。合入后 **experiment/sandbox 分支往往仍停在旧提交**，5174 继续跑旧实验树，看起来像「预览壳坏了 / 功能没了」。

**做法（任选其一）：**

- 壳预览 → **同步 sandbox 与稳定**
- 或 `sync-sandbox-worktree.bat`（把当前稳定分支 merge 进 sandbox worktree）

然后 **重启 `start-desktop.bat`**。

## 已知漏项与对策（双版本清单）

| 漏项 | 现象 | 对策 |
|------|------|------|
| 合入后未同步 sandbox | 5173 正常、5174 缺功能或很旧 | **同步 sandbox 与稳定** + 重启 |
| 稳定未提交改动被 stash | 合入后壳预览/图标消失 | 现已 **合入后自动 stash pop**；仍建议及时 **commit** |
| 只用「采用预览为主壳」 | 以为已合入代码 | 仅换 URL；真合入用 **一键合入实验分支** |
| 普通会话写稳定树 | 实验改动写进根目录 | 用 **开实验进化会话**（`write_scope_root`） |
| 试 agent-core 未切候选 Host | 5174 UI 新、sidecar 仍旧 | 设置 → 候选 Host 指 `.worktrees\sandbox`（验收后切回） |
| `data/` 各一份 | 稳定与 sandbox sidecar 会话不共享 | 设计如此；切候选 Host 即换 data |
| `.worktrees/` 误提交 | 嵌套仓库污染 Git | 已加入 `.gitignore` |
| 文档写 `main`、你在 `codex/…` | 命令行分支名不一致 | merge/sync 以 **当前检出分支** 为准（界面已显示） |
| init 已存在但极旧 | sandbox 长期未同步 | `sync-sandbox-worktree.bat` 或删 worktree 后重装 |
| merge 冲突（同步 sandbox） | 两边都改过 lightbox / package.json 等 | 同步默认 **以稳定为准**（`merge -X theirs`）；仍失败则在 `.worktrees/sandbox` 手解后 commit |
| 双份 `node_modules` | 磁盘占用 | 正常；sandbox `npm install` 独立一份 |
| **采用预览为主壳** 后重启仍像实验壳 | `data/state.json` 的 `desktop_shell_renderer_url` 记住 5174 | 壳预览 → **主壳恢复稳定 5173**；合入/同步成功也会自动清除 |
| `check-stable-before-sandbox-merge` 写 `main` | 你实际在 `codex/…` 等分支 | 脚本默认用 **当前 HEAD 分支** 比较 |
| sandbox worktree 切错分支 | 合入/提交报分支不对 | 在 `.worktrees/sandbox` 保持 `experiment/sandbox` |
| 只改 desktop 未切候选 Host | 新工具在 sidecar 不生效 | 设置里候选 Host 指 sandbox（验收后切回稳定根） |

**推荐闭环：** 实验会话改 sandbox → 5174 验收 → sandbox commit → **一键合入** → **同步 sandbox** → 重启 → 5173 验收 → 继续下一轮实验。
