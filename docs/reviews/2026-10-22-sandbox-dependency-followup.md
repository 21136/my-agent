# Sandbox 依赖对齐与构建复核：后续交付记录

日期：2026-10-22（Asia/Shanghai）。这是[首次本地复核](./2026-10-22-sandbox-storage-local-review.md)之后的新一阶段，以下结果覆盖“依赖 21 项问题、类型检查 10 项错误、构建被阻断”的旧状态；旧日志保留。

## 结论

**依赖版本/二进制预检、类型检查、完整构建已通过；真实应用预览、独立审查和安全漏洞门槛仍未通过。** 仍仅为 `experiment/sandbox` 的未提交候选改动，不表示稳定发布批准。

用户明确授权 sandbox 执行 `npm ci`。实际仅在 `F:/my-agent-main/.worktrees/sandbox/desktop` 重建依赖，锁文件未修改；未借用稳定 node_modules。最终比较已确认：稳定工作区 984 个已跟踪/未跟踪文件内容无变化；稳定依赖元数据及 Electron 二进制共 161 项指纹无变化。

## 安装经过与根因

1. `npm ci --no-fund` 成功，新增 157 包、审计 158 包，退出 0；package-lock.json 的 SHA-256 未变化。
2. 安装后复查：157 个 npm 包版本均对齐，但缺少 Electron dist，预检仍失败。这不是 npm ci 失败，也不是绕过预检的理由。
3. 本次锁定的 Electron 45 包无 postinstall 脚本，二进制按需下载。为避免启动门槛早于首次运行下载的相互阻塞，显式执行锁定包自带的 `node node_modules/electron/install.js`；退出 0，dist/version 为 45.0.0。未使用 stable 二进制或放宽检查。
4. 对齐依赖后，原先 10 个类型错误减少至 3 个；clipboard 相关 7 个诊断消失，未修改 clipboard 实现。剩余 3 个是预览切换中可空全局窗口引用（2 项）和不支持的 `isVisible()` 调用（1 项）。

官方参考：Electron 安装文档 `https://www.electronjs.org/docs/latest/tutorial/installation` 的 Binary download step；View API `https://www.electronjs.org/docs/latest/api/view` 的 getVisible。实际执行的入口与 API 同时核对了本地锁定包的 install.js 和 electron.d.ts，未仅凭最新网页代替锁定版本验证。

## 最小 sandbox 源码修复

- `desktop/electron/main.ts`：捕获并检查局部 `mainWindow` 引用，保留 IPC sender 校验；两次 webContents 使用该非空引用；View 可见性查询改为 `getVisible()`。未扩大修改 clipboard 或其它 Electron 功能。
- `desktop/scripts/dependency-audit.mjs`：只读修复提示增加锁定 Electron 二进制的显式安装步骤。检查器本身不安装、复制或修复依赖。
- `desktop/tests/dependency-audit.test.mjs`：增加缺失二进制时提示正确且仍不写入修复的测试。先观察新增断言失败（11 过/1 失败），改提示后通过；原始 red 日志保留。

## 新鲜验证

| 项目 | 结果 | 验收边界 |
| --- | --- | --- |
| sandbox 依赖预检 | **157 包，0 问题，退出 0** | 包版本、路径与二进制版本/存在检查；不代表无安全漏洞 |
| 稳定依赖只读预检 | **157 包，0 问题，退出 0** | 使用 sandbox 检查器读取稳定安装；不执行稳定安装或稳定应用 |
| Electron 项目类型检查 | **通过，退出 0** | `tsc -p desktop/tsconfig.node.json --noEmit`，buildinfo 在 artifacts |
| 完整 npm build | **通过，退出 0** | 实际完成 TypeScript、renderer、Electron main/preload 产物构建；非发布/运行验收 |
| Python 相关测试 | **20 passed** | retention/archive/file_guard；只写临时夹具 |
| Node 依赖/路径测试 | **13 passed** | 包含本轮新增缺失二进制提示回归；不算真实窗口交互 |
| Node 预览相关测试 | **7 passed** | URL policy 4、panel 2、embed zoom 1；未跑全量前端测试 |
| 语法/声明检查 | **通过** | 修改的 audit mjs、构建 main.js/preload.js 语法及 d.mts 严格类型检查 |
| 隔离 Electron 真实运行小夹具 | **通过，退出 0** | 实际执行 sandbox Electron 45.0.0，无窗口、无 sidecar；真实 WebContentsView 的 setVisible(false/true) 与 getVisible() 分别为 false/true。不是 mock，但仅验证二进制和 View API，不验证真实应用 |
| 5174 renderer 服务 | **启动并响应 HTTP 200** | 入口 HTML、src/main.ts、src/api/ws.ts 实际请求成功；验证结束后仅停止本测试创建的服务树 |
| 浏览器实际页面 | **应用启动/交互未通过** | URL 与标题正确；真实页面显示“启动失败：myAgentDesktop preload API missing”。没有注入假 preload、拦截路由或模拟 sidecar |
| 独立外部代码审查 | **仍未完成** | 本轮未重试，不计通过 |
| npm audit | **未通过，退出 1** | 报告 7 个受影响包项：4 high / 3 low；不是 7 个已证明可利用的路径；未运行 audit fix 或升级锁文件 |

真实浏览器检查的流程为：5174 页面载入 → renderer 尝试获取 Electron preload → 应进入真实工作台。实际停在缺失 preload，因无有效控件未执行交互，不能声称“首屏可用”或“真实预览通过”。使用 Codex 统一浏览器工具/IAB；视口 1241×668，无 Vite 错误浮层，捕获到的 warn/error console 列表为空，但可见启动失败已足以否决验收；没有为通过测试制造 mock。

## 仍存在的警告与门槛

- 构建有 markdown 静态/动态导入同存、超过 500 kB 的 chunk 警告，以及 main/preload 子构建的 Rollup `platform` / `codeSplitting` unknown-options 警告。构建退出 0 不消除这些警告；尚未在真实 Electron preload/sidecar 工作台验证产物。
- Node TS 测试仍有 MODULE_TYPELESS_PACKAGE_JSON 警告；隔离 Electron 夹具有 libpng iCCP profile 警告，均未隐去。
- npm audit 的 high 项为 nanoid、postcss、source-map-js、vite；low 项为 dompurify、katex、mermaid。这是本次审计时点输出，未完成可达性、利用条件或修复升级验证；不能宣称安全审查通过。
- 未重启真实 Electron 应用、未启动真实 sidecar、未操作真实会话/备份、未归档或删除来源、未提交/推送/合入。隔离 Electron 测试只使用独立 artifacts profile，不是现用应用重启。
- 后续应在另行确认真实应用启动/测试数据边界后验证 preload + sidecar + 5174 预览切换，取得独立审查并单独处理漏洞；归档恢复及稳定合入仍需原有授权门槛。

## 证据位置与复跑

本机证据目录（Git 忽略，不自动随将来提交交付）：
`F:/my-agent-main/.worktrees/sandbox/artifacts/storage-deps-b39f08c9ed374e4dbdcd86cc74e3d937/`。

主要文件：npm-ci.log、install-result.json、electron-install-result.json、electron-typecheck.log（修复前 3 项）、electron-typecheck-final.log（退出 0）、dependency-hint-red.log、node-focused-final.log、preview-policy-focused.log、build-final.log、npm-audit.log、electron-runtime-smoke.json、preview-http.json、preview-browser.json、preview-renderer.png；最终隔离比较见 final-verification.json。

```powershell
Set-Location 'F:\my-agent-main\.worktrees\sandbox\desktop'
npm ci --no-fund
# npm ci 后显式补齐锁定版本二进制；安装检查本身不自动下载。
node .\node_modules\electron\install.js
npm run deps:check
node .\node_modules\typescript\bin\tsc -p tsconfig.node.json --noEmit
npm run build
# 只读安全审计；退出 1 是本轮实际观察结果，不自动修复。
npm audit --json
```

复跑安装会重建 sandbox node_modules，勿在稳定目录执行，勿在未授权情况下启动真实应用或切换真实数据。首次复核的 20/12 结果与失败日志仍可追溯；本阶段变为 Python 20 + Node 20（13+7），不代表全量测试通过。
