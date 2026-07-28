# BUG-003：提问后整个 dev 进程退出（`没有找到进程`）

- **日期**：2026-07-11
- **发现于**：`start-desktop.bat` / `npm run dev`，Windows
- **状态**：fixed（2026-07-11 续：重启 taskkill 竞态 + GPU 崩溃）

---

## 现象

终端里大量 `200` 输出后，出现类似：

```text
错误: 没有找到进程 "51132"。
请按任意键继续. . .
```

桌面窗口关闭，Vite 开发服务器也一起退出。用户只是发了一个问题，没有主动点「退出」。

## 根因（两层）

### 1. Vite 插件在 Electron 退出时杀掉整个 dev 进程

`vite-plugin-electron` 的 `startup()` 默认：

```js
process.electronApp.once("exit", process.exit);
```

Electron 一旦崩溃或被杀死，**Node/Vite 也跟着 `process.exit`**，`start-desktop.bat` 结束并 `pause`。

### 2. Windows `taskkill` 竞态（续）

BUG-003 首次修复后 Vite 不再随 Electron 退出，但 **自动重启仍失败**：`startup()` 会先 `taskkill` 旧 PID；若 Electron 已以 `0xC0000005`（访问冲突）崩溃，进程已消失，`taskkill` 抛「没有找到进程」，进入无限 `[electron] failed to start` 循环。

终端里的 `200` 多为 Vite 对页面资源的 HTTP 响应，与根因无直接关系。

## 修复

| 文件 | 改动 |
|------|------|
| `desktop/vite.config.ts` | 自定义 `onstart`：Electron **异常退出**后 1.5s 自动重启，不再 `process.exit`；**code=0 正常退出不重启**（2026-07-12）；重启前 `clearStaleElectronApp()` 避免对已死 PID `taskkill`；`--disable-gpu`；ignore 监视 `agent-core`/`data`/`evolve` |
| `desktop/electron/main.ts` | sidecar 固定端口 `8765`；意外退出时自动重启 Python；`stopSidecar` 吞掉已死进程错误；**Windows dev 下 `disableHardwareAcceleration()`** 降低 `0xC0000005` 崩溃 |
| `desktop/src/api/ws.ts` | 断线重连前重新 `getSidecar()` |
| `agent-core/server.py` | `_dispatch` 包 try/except，单条消息异常不拖垮连接 |

## 验证

1. 重新运行 `start-desktop.bat`
2. 发一条会调工具 / 确认的消息
3. 即使 Electron 短暂闪退，终端应显示 `[electron] exited … restarting`，**不应**再出现 `请按任意键继续`

## 预防

- 开发模式不要把 Electron 生命周期绑死在 Vite 进程上
- sidecar 用固定端口 + 自动重启，便于前端 WS 重连
- **正常关窗退出**（dev：`exit 100`）应 **关掉 `npm run dev`**，勿仅停 Electron 留 Vite 常驻；异常崩溃仍只重启 Electron（2026-07-12）
