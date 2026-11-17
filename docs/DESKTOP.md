# Desktop 当前界面入口

> 当前实现导航；UI 细节以源码为准。本页不再保留早期阶段卡、任务侧栏或教科书式项目流程路线。

桌面主入口使用 unified shell。主要职责包括会话与项目切换、聊天和工具过程展示、确认交互、项目材料查看、终端/服务入口，以及稳定/实验运行车道的清晰区分。具体功能是否可用需核对当前构建与代码。

## 代码入口

- Electron 生命周期与窗口：`desktop/electron/main.ts`、`desktop/electron/preload.ts`
- WebSocket/API：`desktop/src/api/ws.ts`
- unified shell：`desktop/src/shells/unified/`
- 项目面板与文档阅读：`desktop/src/shells/unified/project-panel.ts`、`desktop/src/shells/unified/doc-reading.ts`
- 执行状态展示：`desktop/src/shells/chat-state.ts`

## Windows 稳定开发：任务栏图标

**现象（已报、未在云端视觉复现）**：`npm run dev` / `start-desktop.bat` 直接跑 `node_modules/electron/dist/electron.exe` 时，托盘可用 `loadAppIcon()` 显示项目图标，但任务栏常仍显示 **Electron 默认图标**（宿主进程 exe 资源决定，不等于 `BrowserWindow.icon` 失效）。

**做法（稳定 dev，不改 `node_modules`）**：

1. `desktop/scripts/build-app-icon-ico.mjs` 从 **`build/icon-256.png`**（固定优先级，不按 mtime 选 `public/icon-32`）生成 `desktop/build/app-icon.ico`，并可选合并 `icon-32` 多尺寸（`predev` 会自动执行）。**托盘/窗口** `NativeImage` 仍优先加载 `icon-256.png`；`.ico` 主要用于 branded `electron.exe` PE 嵌入。
2. `desktop/scripts/ensure-windows-branded-electron.mjs` 将官方 Electron dist **复制到** `desktop/.branding/electron-dist`，用 `resedit` 仅给副本嵌入项目图标与 `FileDescription`，并设置 `ELECTRON_OVERRIDE_DIST_PATH`。复制源必须始终为 `node_modules/electron/dist`（**不能**经 `require('electron')` 解析，否则在 `start-desktop.bat` 已设置 override 后第二次 ensure 会把源与目标当成同一路径并 `cpSync` 失败）。
3. `start-desktop.bat` 与 `scripts/dev-with-preview.mjs` 在 Windows 上各调用一次 ensure（子进程继承 override）；第二次应为幂等「fresh」或安全重建。集成测见 `desktop/tests/dev-launcher-branding.test.mjs`。`app.setAppUserModelId('my-agent.desktop')` 在 `main.ts` 中于 `app.whenReady` **之前**注册。

**验证（本机，视觉须人工）**：

- 只读指纹：`node desktop/scripts/report-windows-icon-provenance.mjs`（路径 + SHA256；`runtimeExpectedFirst` 应为 `build/icon-256.png`）。
- `powershell -File desktop/scripts/verify-windows-taskbar-branding.ps1`（含上述 report）。
- `MY_AGENT_ICON_DEBUG=1` + `start-desktop.bat`：主进程日志打印 `[icon] runtime tray/window: icon-256.png (...)`。
- 托盘 vs 任务栏：托盘看 `loadAppIcon` 选的 PNG；任务栏看 branded `electron.exe` 的 PE 图标（manifest `icoSha256` / `canonicalPngSha256` 变更后需 ensure 重建）。

**回归说明（PR#117）**：若 `app-icon.ico` 在 runtime 候选中排在 `icon-256.png` 之前，或 ico 由 `newestSource()` 从 `icon-32`/`public/icon-32` 生成，托盘会回退到旧小图，且任务栏 PE 嵌入同样劣质。云端测试只断言**选中的文件名/哈希**，不代替 Windows 目视验收。打包产物图标仍走既有 electron-builder 配置。

## UI 与安全边界

- 工具确认和执行状态展示必须反映后端实际事件；界面提示不能代替 Executor 的权限判断。
- `PROJECT.md`、`DESIGN.md`、`VERIFY.md` 等是项目材料入口；旧文件可以继续显示，但其状态不证明功能已实现或验证通过。
- 稳定/实验运行规则见 [SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md)；发布与恢复约束见 [RELEASE-DEPLOY-ROLLBACK](./RELEASE-DEPLOY-ROLLBACK.md)。
