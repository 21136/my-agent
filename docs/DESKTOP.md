# Desktop 当前界面入口

> 当前实现导航；UI 细节以源码为准。本页不再保留早期阶段卡、任务侧栏或教科书式项目流程路线。

桌面主入口使用 unified shell。主要职责包括会话与项目切换、聊天和工具过程展示、确认交互、项目材料查看、终端/服务入口，以及稳定/实验运行车道的清晰区分。具体功能是否可用需核对当前构建与代码。

## 内置浏览器开发预览

普通浏览器没有 Electron preload，直接访问开发地址会显示 `myAgentDesktop preload API missing`。已运行桌面开发环境和本机 sidecar 时，可在 Codex 内置浏览器中显式打开：

- 稳定工作区界面：[http://127.0.0.1:5173/?myAgentBrowserBridge=1](http://127.0.0.1:5173/?myAgentBrowserBridge=1)
- sandbox 界面：[http://127.0.0.1:5174/?myAgentBrowserBridge=1](http://127.0.0.1:5174/?myAgentBrowserBridge=1)

在仓库根目录运行 `start-desktop.bat` 可启动开发工作台；5174 是否运行取决于 sandbox 与启动配置。上面的地址使用开发版浏览器桥接，仅在 Vite 开发模式、HTTP 本机回环地址且带该参数时启用。此桥接默认连接 **本机 127.0.0.1:8765 的真实 sidecar**；若开发服务或 sidecar 未运行，需先检查进程及端口，不能仅凭页面打开就认定已连接。非默认 WebSocket 端口可在启动 Vite 前设置 `VITE_MY_AGENT_BROWSER_WS_PORT`。5174 只是实验 **界面**，不意味着连接了实验后端。

**这不是只读演示，也不是权限沙箱。**查询参数只是前端启用开关，不是认证或授权；页面会与真实后端交互，不要在不可信网页或网络环境中使用。Electron 专属功能（如桌面审批设置、系统选目录及双车道宿主操作）不能靠浏览器桥接替代；审批和权限仍以可信桌面端及后端的实际策略为准。浏览器预览的本地存储与 Electron 不一定相同；界面连上不等于聊天、工具或所有功能已验收。

本次实际核对：通过上述 5174 地址在内置浏览器打开界面，看到“已连接”并展开实际记忆；未验证真实聊天、模型调用或工具执行。

## 代码入口

- Electron 生命周期与窗口：`desktop/electron/main.ts`、`desktop/electron/preload.ts`
- WebSocket/API：`desktop/src/api/ws.ts`
- unified shell：`desktop/src/shells/unified/`
- 项目面板与文档阅读：`desktop/src/shells/unified/project-panel.ts`、`desktop/src/shells/unified/doc-reading.ts`
- 执行状态展示：`desktop/src/shells/chat-state.ts`

## Windows 稳定开发：任务栏图标

**现象（已报、未在云端视觉复现）**：`npm run dev` / `start-desktop.bat` 直接跑 `node_modules/electron/dist/electron.exe` 时，托盘可用 `loadAppIcon()` 显示项目图标，但任务栏常仍显示 **Electron 默认图标**（宿主进程 exe 资源决定，不等于 `BrowserWindow.icon` 失效）。

**做法（稳定 dev，不改 `node_modules`）**：

1. `desktop/scripts/build-app-icon-ico.mjs` 从现有 PNG 生成 `desktop/build/app-icon.ico`（`predev` 会自动执行）。
2. `desktop/scripts/ensure-windows-branded-electron.mjs` 将官方 Electron dist **复制到** `desktop/.branding/electron-dist`，用 `resedit` 仅给副本嵌入项目图标与 `FileDescription`，并设置 `ELECTRON_OVERRIDE_DIST_PATH`。
3. `start-desktop.bat` 与 `scripts/dev-with-preview.mjs` 在 Windows 上启用上述覆盖；`app.setAppUserModelId('my-agent.desktop')` 在 `main.ts` 中于 `app.whenReady` **之前**注册。

**验证（本机）**：`powershell -File desktop/scripts/verify-windows-taskbar-branding.ps1`，再用 `start-desktop.bat` 启动。若任务栏曾固定旧 Electron 快捷方式，需取消固定后重新固定；一般 **不必重启 Windows**，重开 dev 即可。打包产物图标仍走既有 electron-builder 配置（本改动针对稳定 dev）。

## UI 与安全边界

- 工具确认和执行状态展示必须反映后端实际事件；界面提示不能代替 Executor 的权限判断。
- `PROJECT.md`、`DESIGN.md`、`VERIFY.md` 等是项目材料入口；旧文件可以继续显示，但其状态不证明功能已实现或验证通过。
- 稳定/实验运行规则见 [SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md)；发布与恢复约束见 [RELEASE-DEPLOY-ROLLBACK](./RELEASE-DEPLOY-ROLLBACK.md)。
