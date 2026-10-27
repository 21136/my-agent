# 双版本存储安全（sandbox 实现）

日期：2026-10-22。当前仅位于 `experiment/sandbox`，**未合入稳定版**。

## 结论与边界

保留稳定/实验两份源码和隔离数据，先限制备份增长、提供可校验归档、阻止依赖漂移下的错误验收。不借用另一轨的可变 `node_modules`，不把稳定页面当成实验预览。

首次复核没有安装依赖；后续已按用户授权完成 sandbox 依赖对齐。两个阶段均没有删除真实备份、生成真实数据归档、迁移磁盘、提交、合入或重启真实应用，**实际释放空间为 0**。归档工具自身也不删除来源；空间回收需要另行确认处置，不能仅凭 ZIP 存在就清理。

## 本机测量

F: 为 exFAT、分配单元 128 KiB；C: 为 NTFS、分配单元 4 KiB。以下是本轮初始扫描的时间点数据，不是实时容量；分配量为逐文件按分配单元取整的估算，不含目录/文件系统元数据，不等同于精确物理占用。

| 范围 | 逻辑大小 | 分配量估算 |
| --- | ---: | ---: |
| 稳定工作区（不含 sandbox） | 2.362 GiB | 8.654 GiB |
| sandbox | 0.493 GiB | 2.042 GiB |
| sandbox desktop/node_modules | 446.84 MiB | 1.865 GiB |
| sandbox 源码/资源/其余 | 34.89 MiB | 146.5 MiB |
| 稳定 data 下两类 guard 备份 | 约 1.26 GiB | 约 4 GiB |

Git 历史已经由 worktree 共用；主要增量不是另一套 Git 历史，而是依赖和大量小文件。备份保留存在缺陷：目录快照使用文件 unlink，失败被吞掉，导致历史不断增长。

## 1. 备份保留修复

- `.session-guard/<会话>/`：保留 12 个已识别的时间戳历史目录，另保留 `latest`。
- `.file-guard/<键>/`：保留 20 个时间戳文件。
- 只处理已知 guard 路径、时间戳命名、已知平铺文件；未知文件、额外嵌套目录、链接/reparse point 不进入保留计数、不删除。
- 删除失败记录 warning，不再静默吞掉。不会递归删除任意目录。
- **修复启用后，下次保存可能裁剪旧历史。合入稳定或切换到其真实数据前，应先显式归档并完成校验。** 本轮只在临时测试数据上执行过保留裁剪，真实备份未清理。
- 这里的路径检查不是对恶意并发替换文件系统的安全隔离；归档期间应避免其它程序改写 guard 历史。

## 2. 只归档，不自动清理

以下 PowerShell 命令使用 sandbox 工具读取稳定备份。默认命令只盘点：

```powershell
& 'F:\my-agent-main\.venv\Scripts\python.exe' -B 'F:\my-agent-main\.worktrees\sandbox\agent-core\guard_archives.py' --root 'F:\my-agent-main'
```

显式创建**新文件**才会写归档。目标父目录必须已存在；已存在文件不会覆盖。示例日期不自动变化，重跑须选新文件名：

```powershell
& 'F:\my-agent-main\.venv\Scripts\python.exe' -B 'F:\my-agent-main\.worktrees\sandbox\agent-core\guard_archives.py' --root 'F:\my-agent-main' --archive 'F:\my-agent-main\.worktrees\sandbox\artifacts\guard-history-20261022.zip'
& 'F:\my-agent-main\.venv\Scripts\python.exe' -B 'F:\my-agent-main\.worktrees\sandbox\agent-core\guard_archives.py' --verify 'F:\my-agent-main\.worktrees\sandbox\artifacts\guard-history-20261022.zip'
```

- 排除可变 `latest`、链接、未知条目。输出 `skipped_entries` 非零时，不能当作全部历史已经归档。
- 每个成员记录路径、大小和 SHA-256，写完重新读取全部成员校验；独立 `--verify` 也校验清单和准确成员集合。
- 源文件元数据变化、路径变得不安全或校验失败会中止，删除本次新建的失败输出，保留源备份。
- 拒绝归档写入来源 guard 树。即使成功，`sources_deleted` 也为 0。
- **ZIP 未加密，可能包含私密聊天和源码，只能保存在受控私人位置。** 归档不替代现用会话和 `latest` 的完整备份；本工具不提供自动清理或恢复命令。

本轮真实稳定目录的只读盘点：17,852 个历史文件、1,341,719,278 字节，跳过未知条目 0；不含 `latest`。真实压缩率、归档所需时间、恢复演练尚未测量。

## 3. 依赖预检（首次复核，安装前）

```powershell
Set-Location 'F:\my-agent-main\.worktrees\sandbox\desktop'
node .\scripts\dependency-audit.mjs --json
```

也可用 `npm run deps:check`。检查器不依赖 node_modules 中的库，不修改安装：

- 比对 package.json 与 npm v2/v3 lock 根依赖声明、安装包版本、平台/CPU 条件。
- 检查 Electron 可执行文件是否存在及 `dist/version` 是否与 lock 一致；**未执行二进制，也不证明二进制哈希或运行验收**。
- 拒绝依赖根/包链接逃出本轨 desktop 安装目录；workspaces/链接包需另定策略。
- npm 的 dev、dev:main-only、dev:preview、build、preview 前置检查；Vite 配置也执行检查，双轨启动器分别检查主轨和预览轨。
- sandbox 存在但安装缺失时，不再悄悄借稳定 Vite 或配置。预览轨失败不表示稳定轨已经验收实验改动。

真实只读检查结果：稳定 157 个包通过；sandbox 检查 144 个包、21 项问题。锁定 Electron 45.0.0，sandbox 安装及 dist/version 为 33.4.11。启动 `npm run dev:preview` 实际在 prehook 以 1 退出，未启动 Vite 或 Electron。

首次复核工具仅提示在对应 desktop 目录显式 `npm ci` 后复查，当时未执行；后续工具提示已补充锁定 Electron 二进制安装步骤，安装后的更新见下文。修复安装、重新预览和真实 UI 验收仍待完成。稳定启动器未修改，所以这些启动保护目前仅在 sandbox 代码中，不能宣称稳定已受保护。

## 后续存储选项（未实施）

可单独评估将依赖/构建缓存放到 NTFS，或采用按锁版本的不可变依赖存储；需先验证容量、路径、原生依赖和安装行为。不能直接把两轨可变 node_modules 合成一份。F: exFAT 不支持硬链接，不能承诺 pnpm hardlink 在该卷实现去重。本轮没有更换包管理器或迁移任何目录。

## 首次复核的验证记录与剩余门槛

- Python guard retention/archive/file_guard：20 项通过，只使用临时 fixture 执行写入与删除。
- Node dependency-audit/sandbox-paths：12 项通过；npm 显式测试清单已加入新测试，未跑全量套件。
- 三个相关 mjs 语法检查、三个 Python 实现文件语法编译、dependency-audit.d.mts 独立严格类型检查通过。
- Electron 项目 `tsc -p tsconfig.node.json --noEmit` **未通过**：未修改的 electron/main.ts 有 10 个错误，位于 1060、1063、1068、1326、1328、1329、1335、1337、1339 行，涉及可空窗口、WebContentsView 与 clipboard 类型。依赖漂移可能影响其中部分错误，本轮不扩大修改范围、不宣称构建通过。
- 981 个稳定已跟踪文件与实施前 SHA-256 基线逐一比较，无变化；既有未提交修改仍保留。
- 已尝试 `npm run build`，在 `prebuild` 以 1 退出，完整构建未执行；未进行真实 Electron 重启、真实备份归档/恢复验收或稳定合入。

## 首次本地复核与交付

2026-10-22 已重新完成相关小测试（Python 20、Node 12）、只读依赖预检和稳定内容比较；依赖 21 项问题、Electron 类型检查 10 项错误、构建/预览前置阻断仍存在。外部审查代理未取得有效结果，**独立审查未完成、不计通过**。

首次 exFAT TEMP 链接夹具失败及恢复默认 NTFS TEMP 后的重跑结果、每条验收门槛、精确命令和本机日志位置，见 [最终本地复核与交付记录](./reviews/2026-10-22-sandbox-storage-local-review.md)。这不是稳定发布批准。

## 后续依赖对齐与构建更新（2026-10-22）

在用户明确授权后，sandbox 的 npm ci 与锁定 Electron 45.0.0 二进制安装均完成，package-lock 未改；依赖预检 157 包/0 问题，类型检查及完整 npm build 均退出 0。仅在 sandbox 最小修复预览窗口非空引用和 getVisible 接口，并补齐缺失二进制的只读修复提示。相关测试 Python 20、Node 20（依赖/路径 13 + 预览相关 7），未跑全量套件。

真实 Electron 二进制/View 可见性已通过隔离无窗口夹具；5174 renderer 服务能响应 HTTP 200，但普通浏览器实际显示 preload API missing，**真实应用预览/交互仍未验收**。独立外部审查仍未完成，npm audit 另报告 7 项（4 high / 3 low），构建仍有大 chunk 与插件选项警告。未启动真实 sidecar、未重启现用应用、未改真实备份，也未提交或合入。

完整命令、前后结果、截图和验收边界见 [依赖对齐与构建复核](./reviews/2026-10-22-sandbox-dependency-followup.md)。
