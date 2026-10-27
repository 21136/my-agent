# 开发态双版本合入流程补齐（2026-10-23）

## 当前结论

sandbox 已补齐 Git 采用服务和产品入口，但**用户要的可用闭环尚未完成**。真实稳定安装未升级；常用 Vite 开发态 Host 会随源码合入重启控制进程，新入口因此明确禁止这种 Host 的真实采用。还需固定控制端/独立启动器架构及正式发布，不能靠手工合入或替换预览 URL 绕过。原生 Electron 点击/重启验收未完成，不是 Evolution E 正式发行完成。

## 本轮改动

- 现有壳预览页增加「检查合入状态」「返回稳定版」，保留原一键合入入口。renderer 不再自行提供自动提交/stash 权限或使用 window.confirm 充当授权。
- main/preload 通过可信工作台主 frame 和精确 origin 限制入口；主进程原生对话框展示两个目录、分支、整支实验提交及本地备份文件范围，默认取消。测试 Host 禁止采用。
- 合入确认绑定 HEAD、改动内容和暂存区，复查变化；同仓库互斥锁，拒绝接管遗留 merge/rebase 等操作。固定提交 SHA，不按可移动分支名执行合入。
- 源码白名单包含 desktop/agent-core/protocol/docs/scripts 及工具目录，拒绝敏感路径、源码外改动、提交中的链接/子模块、跨仓库工作区。子进程清除可重定向 Git 根或索引的环境变量。
- 精确备份/恢复稳定区原有暂存与未暂存状态；按本次唯一标签找到 stash 对象，按 SHA 恢复，备份保留到用户验收。不 pop/drop 其它任务的 stash。
- 稳定分支移动前，使用 merge-tree、无引用的预测提交和独立临时索引预检合并、本地内容恢复、暂存状态恢复及未跟踪文件碰撞；Windows 别名同样阻断。已知冲突保持稳定 HEAD 不动，恢复本次原始工作。Git stash 非零退出也可能改动索引，必须先找回本次精确对象再恢复。
- 无法预见的收尾失败为 partial，绝不误报完整成功。成功后仅 fast-forward 同步 sandbox，原子清除主壳预览地址，不自动重启或调用模型。
- 主进程在任何确认和 Git 写操作前阻断 Vite 开发态 Host、测试 Host 的真实采用，避免控制进程被源码热更新中途重启。

## 验证证据

- npm run test:sandbox-adopt：**30 passed / 0 failed / 0 skipped**，日志 artifacts/dual-version-adoption-20261023/focused-final-30.txt。之后新增的开发 Host 安全门槛另跑 **1 passed / 0 failed**，日志 host-gate-final.txt（共 31 个不同用例分批验证，不称单次 31 项整跑）。
- 其中 Git 测试使用独立临时真实仓库及 worktree，不是模拟 merge；覆盖正常合入、连续两轮实验、暂存状态保持、既有 stash 保留、冲突中止、恢复失败、确认后变更、锁及目录/权限边界。
- 增加受绝对路径校验保护的测试夹具自动清理后，再跑「连续两轮」「主壳设置」两个用例：**2 passed / 0 failed**，日志 cleanup-verification.txt。该 2 项与上述 30 项重叠，不另算新增通过项。
- Electron strict TypeScript noEmit 通过；新增 renderer controller 定向 strict TypeScript 检查通过。
- 最终完整 desktop npm run build 通过；依赖审计 **157 locked packages / 0 issues**。有原有构建工具选项/大 chunk 警告，不声称构建无警告。
- 真实目录仅运行 getStatus 只读检查，识别当前稳定分支、experiment/sandbox 和待处理改动；没有在真实分支 stage/commit/stash/merge。
- 稳定树基线校验：981 个已跟踪文件 SHA-256、HEAD 和原 dirty status 全部保持不变，见 artifacts/dual-version-adoption-20261023/stable-verification.json。
- 所有本轮遗留临时 Git 仓库在验证绝对路径、专用目录和 fixture 身份后清理；保留 RED/GREEN 日志和编辑前源码副本，未删除真实备份。

## 真实 UI 验收与发布缺口

- 本轮确实通过浏览器打开 http://127.0.0.1:4179/，标题 my-agent；实际页面显示「启动失败：myAgentDesktop preload API missing」，console 未捕获相关错误。没有给网页注入假 preload，也没有把 HTTP 200/测试 controller 等同于桌面按钮可用。
- 新 main/preload 仅在 sandbox；更换 renderer 预览 URL 不会更新运行中的稳定控制端。现有开发态控制进程与被更新源码未分离，Vite build/watch 会重启 Electron；需要先完成固定控制发行/独立启动器及源码目标绑定，再做一次性控制端发布和真实桌面验收。本轮没有擅自开始这一新增架构或发布工作。
- 没有启动 Electron/sidecar/候选，没有读取或复制私有凭据，没有模型调用；P1/H1/M1 仍未因此验收。
- 没有安装依赖、全量测试、真实仓库暂存/提交/合入/推送；稳定脏改动和已有备份保持原样。
- 这是开发态 Git 采用，不是独立 Launcher/固定发行、数据迁移、唯一写租约或进程崩溃恢复。外部 Git 工具和编辑器不受应用锁控制；没有操作系统级隔离保证。

## 独立复核

使用用户指定的 6-luna 只读复核：发现「恢复冲突在合入提交后才检测」和「Windows 大小写碰撞漏检」两项 P2；已补 RED/GREEN 回归并修复。后续复核未发现新的重要回归。此为代码复核，不等于原生运行验收或独立安全审计。

## 主要文件

- desktop/electron/sandbox-adopt.ts
- desktop/electron/main.ts、desktop/electron/preload.ts
- desktop/src/shells/unified/shell-dev-adoption.ts、shell-dev-panel.ts、index.ts
- desktop/src/api/ws.ts、desktop/package.json
- desktop/tests/sandbox-adopt.test.ts、desktop/tests/shell-dev-adoption.test.ts
- docs/SANDBOX-EVOLUTION.md
