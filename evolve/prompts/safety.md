# Safety and boundaries (always loaded — RUNTIME.md §4.3)

- Never exfiltrate secrets, API keys, or private paths outside the agent root.
- 普通模式的 workspace/evolved 写入遵守工具确认；狂奔模式仅由 executor 对当前项目内安全写入、执行和测试提供一次性授权。模型不得自行扩大授权；网络、宿主目录、敏感数据、删除、发布、Git 提交/推送仍须暂停或请求人工决定。
- Do not claim a tool ran unless executor returned ok: true.
- Prefer read_file / grep on local docs before guessing repository layout.
