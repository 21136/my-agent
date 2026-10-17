# Skills Registry and Runtime Design

> 版本：0.1.0 · 2026-10-16
> 状态：**current · 已确认 · implemented**
> 范围：内置 Skill 注册、发现、按需加载和 system prompt 索引；不改变 Executor 权限 owner。

## 1. 目标

把当前分散在 `evolve/tools`、`prompts`、`plan_agent` 和测试中的稳定工作流，整理为可发现、可验证、可按需加载的 Skill 包。Skill 是工作流知识层，不是权限绕过层，也不直接执行任意第三方脚本。

## 2. Skill 包格式

```text
evolve/skills/<skill-id>/
  SKILL.md       # frontmatter + workflow instructions
  meta.json      # 可选扩展元数据
  references/    # 可选参考资料
  scripts/       # 可选脚本；默认不自动执行
```

`SKILL.md` frontmatter 至少包含：`name`、`description`、`triggers`、`allowed_tools`、`risk_level`、`verification`、`version`。

## 3. Runtime 边界

- `SkillRegistry` 只扫描受信任的内置目录；解析失败的 Skill 进入 diagnostics，不阻塞主 Agent。
- system prompt 默认只注入短 Skill index；只有当前请求强匹配或显式选择时才加载一个 Skill 的正文。
- Skill 的 `allowed_tools` 是提示/审计元数据，不替代 Executor 的真实权限判断。
- Skill 选择属于当前回合，不写入 `SessionMeta`，不形成 sticky mode。
- Skill 正文、脚本和 references 按文件大小/路径边界读取；不执行第三方脚本。

## 4. 第一批内置 Skill

- `ordinary-project-execution`：普通项目意图、路径、风险和证据边界；
- `external-project-adoption`：外部项目扫描、junction 接入和恢复验证；
- `project-verification`：测试/构建/验收证据收集；
- `systematic-debugging`：复现、追因、最小修复和回归；
- `documentation-governance`：文档状态、权威级别和链接审计。

## 5. 验证

Registry 单测覆盖 frontmatter、坏包隔离、触发匹配、正文按需加载和路径穿越拒绝；loader 单测确认 `skill_index`/`skill_context` 只在 overlay 中出现，且不影响 `include_overlay=False` 的静态 prompt。
