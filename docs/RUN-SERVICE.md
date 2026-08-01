# run_service — 托管长驻进程

> 版本 **0.1.0** · 2026-08-01  
> 工具：`evolve/tools/common/run_service` · Phase **25**

## 问题

`mvn_exec` / `npm_exec` / `repl` 都有 **policy 超时**；超时后内核会杀子进程。  
因此不能用它们跑 `mvn spring-boot:run`、`npm run dev` 这类**不退出**的服务。

## 能力

| action | confirm | 说明 |
|--------|---------|------|
| `start` | 是 | 后台启动；可选 `ready_regex` / `ready_port` 等到就绪 |
| `stop` / `restart` | 是 | 杀进程树（Windows `taskkill /T`） |
| `status` / `list` | 否 | 查存活 |
| `logs` | 否 | 读 `data/services/<name>.log` 尾部 |
| `wait` | 否 | 等到就绪或超时 |

状态文件：`data/services/<name>.json`（已在 `data/` gitignore 下）。

## 调用示例

```json
{
  "tool_name": "run_service",
  "arguments": {
    "action": "start",
    "name": "huiyi-backend",
    "command": "mvn spring-boot:run",
    "working_dir": "workspace/huiyi/backend",
    "ready_regex": "Started .*Application",
    "ready_port": 8080,
    "ready_timeout_sec": 120
  }
}
```

## 非目标（M0）

- 不替代 bat / 用户手动起服务
- 不做跨机编排、不健康自动重启
- 不把超时拉长塞进 `mvn_exec`

## 回归

- **IT-75**：start → ready → logs → stop（`tests/test_run_service.py`）
- **IT-76**：status/list 不 confirm；start 需 confirm
