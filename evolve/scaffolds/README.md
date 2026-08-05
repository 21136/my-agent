# 项目配方索引（Phase 43）

> 真源设计：[docs/PROJECT-RECIPES.md](../../docs/PROJECT-RECIPES.md) v0.2.0  
> **状态**：**spring-vue** · **fastapi-vue** done · `next-prisma` defer

LLM / 人类选配方前可读本文；细则见各子目录 `manifest.json` 与同目录 `README.md`（若有）。

| recipe | 栈 | manifest | 状态 |
|--------|-----|----------|------|
| `spring-vue` | Spring Boot + Vue 3 + Maven + npm | `spring-vue/manifest.json` | **done**（T-4303） |
| `fastapi-vue` | FastAPI + Vue 3 + pip + npm | `fastapi-vue/manifest.json` | **done**（T-4305） |
| `next-prisma` | Next.js + Prisma + SQLite | — | **defer** |

## 调用

```text
run_evolved → scaffold_project
  recipe: spring-vue | fastapi-vue
  target_dir: workspace/<project_id>/
  phase: init | deploy
  dry_run: true   # 只列步骤、不写盘
```

或通过 `create_project(..., template="spring-vue")` 自动跑 `phase: init`。

## 执行面（重要）

配方步骤 **`kind: run_command` only**（T-4307 · 2026-08-05）：

- 构建/安装类步骤写完整 shell 命令（如 `npm install`、`mvn -q -DskipTests compile`）。
- **禁止**在 manifest 中使用 `kind: npm_exec` / `kind: mvn_exec`（工具已 `archived`，LLM 执行面亦不可 `run_evolved` 调用）。

## 勿混淆

| 名称 | 是什么 |
|------|--------|
| `scaffold_project` | evolved 工具 · 读 manifest 编排步骤 |
| `evolve/scaffolds/<recipe>/` | 静态配方 + 模板（Git 真源） |
| `scaffold_demo` | `write_evolve` 演示用空目录 · 见 `evolve/tools/common/scaffold_demo/README.md` |
