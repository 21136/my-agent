# scaffold_demo（非配方 · 非注册工具）

> **不是** `scaffold_project` 项目配方。  
> **不是** registry 里的 evolved 工具（本目录默认无 `tool.toml`）。

## 用途

- `agent.py --demo` / Phase 16 `write_evolve` 演示的**落盘目标目录**（临时写入 `main.py` + `tool.toml` 后验收/清理）。
- 与 [`evolve/scaffolds/`](../../../scaffolds/) 项目配方（`spring-vue` · `fastapi-vue`）**无关**。

## 为何目录常为空

演示脚本会 `write_evolve` 写入本目录，跑 demo/checker 后可能删除或覆盖。  
空目录 = 正常；**不要**在此手工沉淀生产工具。
