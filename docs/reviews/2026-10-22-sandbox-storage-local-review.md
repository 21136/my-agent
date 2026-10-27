# Sandbox 存储安全：最终本地复核与交付记录

日期：2026-10-22（Asia/Shanghai）。接续“构思自研内置浏览器方案”会话的存储安全收尾；本记录不表示浏览器功能已经实现。

## 后续更新（本记录之后）

用户授权 sandbox npm ci 后，依赖已对齐，类型检查及完整构建通过；普通浏览器仍缺少 Electron preload，独立审查与 npm audit 门槛仍未通过。详见 [依赖对齐与构建复核](./2026-10-22-sandbox-dependency-followup.md)。**以下是重装前的历史状态，不再代表当前依赖/构建状态。**

## 首次复核的交付判定

**本地复核与记录完成；仅为 sandbox 候选改动，不具备稳定发布/合入验收结论。**

- 工作区：`F:/my-agent-main/.worktrees/sandbox`，分支 `experiment/sandbox`。
- 当前 HEAD：`6c34432297bff148bea26f3d983e8fefbe87d54f`；交付改动仍未提交，HEAD 本身不包含这些改动。
- 外部审查代理在上个会话未得到有效审查结果；本轮未重试，**独立审查未完成，不计为通过**。本地代码复核不能替代独立审查。
- 未重装依赖、清理真实备份、生成真实备份 ZIP、迁移目录、重启 Electron、提交、推送或合入稳定版。真实备份空间回收仍为 0。

## 新鲜验证结果

运行时：`F:/my-agent-main/.venv/Scripts/python.exe`，pytest 9.1.1；Node v24.15.0，npm 11.12.1。

| 项目 | 结果 | 证据与边界 |
| --- | --- | --- |
| Python 相关小测试 | **20 passed，退出 0** | retention 8 + archive 8 + 既有 file_guard 4；写入/删除只针对临时夹具 |
| Node 相关小测试 | **12 passed，退出 0** | dependency-audit 11 + sandbox-paths 1；其中 npm hooks/Vite 配置断言为静态接线检查，不等于真实 UI 启动 |
| 语法检查 | **通过** | 三个相关 mjs 的 `node --check`；三个 Python 实现文件 `compile()`，未写 bytecode |
| 独立声明类型检查 | **通过，退出 0** | `dependency-audit.d.mts`，strict / noEmit / NodeNext；不代表整个 Electron 项目通过 |
| 稳定依赖只读预检 | **157 包，0 问题，退出 0** | 仅检查安装元数据/版本和 Electron 文件存在，不执行二进制，不代表稳定应用运行验收 |
| sandbox 依赖只读预检 | **失败，21 项，退出 1** | 检查 144 包；Electron lock 45.0.0、实际包与 dist/version 33.4.11；未执行 npm ci |
| `npm run dev:preview` | **阻断，退出 1** | `predev:preview` 拒绝依赖漂移；本次命令未启动 Vite/Electron，不是预览通过 |
| `npm run build` | **阻断，退出 1** | `prebuild` 拒绝依赖漂移；未执行完整 TypeScript/Vite 构建，没有产物验收 |
| Electron 项目类型检查 | **失败，退出 2** | 单独执行 tsc / noEmit，10 个错误；见下文，不宣称构建通过 |
| 稳定 guard 只读盘点 | **完成，退出 0** | 17,852 历史文件 / 1,341,719,278 字节，跳过 0；排除 latest，不含真实归档/恢复演练 |
| 稳定内容隔离 | **通过内容比较** | 上会话实施前 981 个已跟踪文件无变化；本轮起点 984 个已跟踪及未跟踪文件无新增/删除/内容变化 |
| diff / 新文件空白 | **通过** | `git diff --check` 和未跟踪源码/文档空白检查；CRLF 转换及 Node MODULE_TYPELESS_PACKAGE_JSON 警告仍保留 |
| 外部独立审查 | **未完成** | 没有有效审查结论，不算作审查通过 |
| 全量测试、真实 UI、真实归档恢复、稳定合入 | **未进行** | 不由小测试、静态检查或预检阻断结果代替 |

Electron 类型错误均位于本次未修改的 `desktop/electron/main.ts`：1060、1063、1068、1326、1328、1329、1335、1337（2 项）、1339 行，涉及可空窗口、WebContentsView 与 clipboard 类型。尚未在依赖对齐后重测，不能断言全部与依赖无关，也未通过稳定基线对照证明其来源。

### 失败尝试与环境差异

首次复核把 TEMP/TMP 指向 F: exFAT 夹具目录，Python 为 **17 passed / 3 failed**、Node 为 **11 passed / 1 failed**；失败发生在既有隔离辅助函数或链接测试的 symlink/junction 建立阶段。F: exFAT、128 KiB 分配单元，C: NTFS、4 KiB 分配单元已现场确认。

仅恢复默认 C: NTFS TEMP/TMP 后，同一测试清单与断言得到上表 20/12 通过；pytest 自身的 tmp_path/basetemp 仍位于 sandbox artifacts。未为通过测试修改产品源码、删除断言或跳过用例。**链接夹具在 exFAT 下的失败保留为环境限制，不宣称已修复。**

复核脚本还遇到过 GBK 控制台无法输出 Node 检查符号、以及一次重复 desktop 路径错误；只校正复核脚本的 UTF-8 输出和路径，保留中断日志，之后完整重跑。不能把那些中断尝试计入通过。

## 代码复核范围

本地逐项检查了快照候选识别/路径边界、仅已知平铺载荷裁剪、latest/未知/链接保留、删除失败告警；归档新建不覆盖、逐成员大小/SHA-256 校验、失败输出清理与来源保留；依赖 lock/manifest/安装版本、外链拒绝、Electron 版本、npm/Vite 前置接线及不借用另一轨依赖的路径。未发现需在本轮扩大源码修改的具体问题；这不是独立审查批准，也不是并发恶意文件系统替换的安全证明。

源码范围：

- `agent-core/file_guard.py`、新增 `guard_snapshots.py`、`guard_archives.py`。
- 新增 `agent-core/tests/test_guard_retention.py`、`test_guard_archives.py`。
- 新增 `desktop/scripts/dependency-audit.mjs`、`dependency-audit.d.mts`、`desktop/tests/dependency-audit.test.mjs`。
- `desktop/scripts/dev-with-preview.mjs`、`sandbox-paths.mjs`、`desktop/package.json`、两份 Vite 配置。
- 说明文档、实施计划和本交付记录；本轮续接仅新增复核证据并补文档，未继续修改产品源码。

实施说明：[双版本存储安全](../SANDBOX-STORAGE.md)。计划：[sandbox storage safety](../superpowers/plans/2026-10-22-sandbox-storage-safety.md)。

## 命令及本机证据

以下命令在 sandbox 根目录执行；禁用 pytest 缓存和 Python bytecode，全量测试未运行。链接相关夹具使用默认 NTFS Windows TEMP；测试不读取或清理真实会话。

```powershell
& 'F:\my-agent-main\.venv\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider agent-core/tests/test_guard_retention.py agent-core/tests/test_guard_archives.py agent-core/tests/test_file_guard.py
node --test desktop/tests/dependency-audit.test.mjs desktop/tests/sandbox-paths.test.ts
node desktop/scripts/dependency-audit.mjs --json
node desktop/scripts/dependency-audit.mjs --root 'F:\my-agent-main\desktop' --json
# 下列两个 npm 命令本次都被 prehook 阻断，退出 1；不是启动或构建通过。
npm --prefix desktop run dev:preview
npm --prefix desktop run build
```

成功完成全部复核步骤的本机日志目录：
`F:/my-agent-main/.worktrees/sandbox/artifacts/storage-final-d33e14306a0b47a3a681fb2189d3b7a9/`。
其中 `summary.json` 逐项记录命令、工作目录及实际退出码；“预期失败被观察到”只证明阻断机制，不代表依赖、构建或 UI 通过。另有 `python-focused.log`、`node-focused.log`、`sandbox-dependencies.log`、`build-blocked.log`、`electron-typecheck.log`、`stable-inventory.log`。

初次 exFAT 失败证据在 `artifacts/storage-final-89beee69f7c24137b062866008a08274/`；路径错误中断证据在 `artifacts/storage-final-e722d789e34345e3aeecb79f8731bd6a/`。本机 artifacts 被 Git 忽略，日志不会自动随将来的 Git 提交交付；本文保留可读结果与命令。夹具和日志不是用户数据归档，不可据此清理真实备份。

## 后续验收顺序（尚未执行）

1. 在明确允许的 sandbox desktop 目录对齐依赖，再运行 deps:check；不要借用稳定的可变 node_modules。
2. 在对齐后的安装上复测 Electron 类型检查、完整构建；仍有错误再逐项处理。
3. 在真实 5174 预览与 Electron 中验证实验功能，不能把稳定 5173 页面作为实验验收证据。
4. 取得有效独立代码审查；与本地复核分开记录。
5. 在明确授权下真实归档、逐成员校验并进行恢复演练；尤其在启用会自动裁剪历史的保留修复前完成备份。任何来源清理须另行确认。
6. 上述门槛满足后再讨论提交/稳定合入，并按仓库规定同步 sandbox；本轮没有授权也没有执行。
