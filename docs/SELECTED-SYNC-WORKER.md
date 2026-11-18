# 自我改造 Worker：只同步本次改动

在已认证的「自动改造实验区」使用「动手」，例如：「把稳定区刚合入的 `e59eead` 诊断修复同步到实验区，保留实验区原本的改动。」只有语义路由调用了 `sync_selected` 工具才启动；讨论、反向发布和未配置的同步请求不会改写源码。不会把落后的提交顺手全合过去。

## 一次性准备与授权

- 使用现有 [自动改造启动入口](./SELF-EVOLUTION-AUTO-EXFAT.md)，策略模型必须是精确注册 ID `0x567-flash`。模型不会收到两区根目录、聊天历史、私有数据文件或未选路径。
- 从安装源码根目录构建可信验证镜像，并取得本机不可变的 `sha256:<64 位>` 镜像 ID：

  ```powershell
  docker --host npipe:////./pipe/dockerDesktopLinuxEngine build -t my-agent-selected-sync:local tools/selected-sync-verifier
  docker --host npipe:////./pipe/dockerDesktopLinuxEngine image inspect my-agent-selected-sync:local --format '{{.Id}}'
  ```

- 在稳定区 `data` 或其他操作者控制的位置配置策略。从同一个 PowerShell 启动现有入口，使子进程继承 `$env:MY_AGENT_SELECTED_SYNC_POLICY = 'F:\my-agent-main\data\selected-sync.policy.json'`。**不能**使用 sandbox 中的文件授予同步权。没有配置就不会启用。默认为仅验证候选，显式设 `publish_enabled: true` 后，本策略授权的同步通过检查即可写入，无需逐条确认。

策略示例（需替换完整 Git SHA、镜像 ID 和范围；文件为 JSON）：

```json
{
  "schema": "selected-sync.v1",
  "stable_root": "F:/my-agent-main",
  "source_ref": "refs/heads/codex/self-hosting-e2e",
  "image": "sha256:<本机固定镜像 ID>",
  "publish_enabled": false,
  "profiles": [{
    "commit": "<已授权稳定提交完整 SHA>",
    "selected_paths": ["agent-core/execution_lifecycle.py"],
    "verification_paths": ["agent-core/tests/test_execution_lifecycle.py"],
    "baseline_checks": ["agent-core/tests/test_execution_lifecycle.py"],
    "candidate_checks": ["agent-core/tests/test_execution_lifecycle.py"]
  }]
}
```

可登记最多四个已授权提交；`selected_paths` 只能来自对应单父提交本身的增改。将检查依赖的源码、配置和只读 fixture 列入 `verification_paths`，不含已选路径。测试用例由操作者固定：基线覆盖实验区已有行为，候选还须覆盖新功能；新增的固定回归测试可由该稳定提交提供。混合前后端改动要有两类检查。Docker 只接收构建的快照，不挂载工作盘，不联网、不自动下载依赖。文档、Dockerfile 不会被当成执行指令。

## 能力及边界

- 工具只允许查看已选文件中具体冲突块。未冲突内容由 Git 用**本次提交的父版本**做三方合并。LLM 可以合并一个或多个已查看的块，不能指定工作区路径、扩大范围、改验证用例或直接运行 Git。
- 宿主分别验证原实验快照与候选；只有已授权且检查通过后，才重新核对两区 HEAD、实验区暂存及相关文件状态，并写入已选文件。成功不等于 Git 提交、推送、稳定区合入或 UI 热重载。
- 候选保存在稳定区 `data/selected-sync/<id>.json`，其中 `verified` 表示是否已通过固定检查。写入中断若无法安全回退，在 `.worktrees/.selected-sync-journal-*/recovery.json` 留恢复材料并停止后续写入。先对照记录和现场再处理残留锁，不强行覆盖并发编辑。
- 第一版只支持稳定 → 注册的 `experiment/sandbox` 的选定文本新增与修改，不支持删除、重命名、二进制、反向同步或追平整个历史。也不会替代实验 → 稳定已有发布流程。
