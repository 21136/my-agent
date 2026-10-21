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
