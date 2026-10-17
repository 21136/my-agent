# 可替换 Skill Provider 与版本化运行时设计

> 版本：0.1.0 · 2026-10-17
> 状态：**implemented · local directory source**
> 范围：在现有内置 Skill Registry 之上增加可替换来源、版本选择、回合固定和评测/回滚边界。

## 1. 背景与问题

当前 Skill 已能从 `evolve/skills/<skill-id>/SKILL.md` 被发现、按触发词选择并注入当前请求的 system prompt。现有 `SkillRegistry` 直接面向受信任的本地目录，适合第一批内置 Skill，但不适合长期承载以下变化：

- Skill 正文和流程会随模型能力、工具契约和项目阶段迭代；
- 同一 Skill 需要保留多个版本并支持回滚；
- 不同模型或不同 Agent Runtime 可能需要不同的 Skill 版本；
- 后续可能增加本地覆盖、缓存同步或经过审批的远程来源；
- 需要记录一次回合究竟使用了哪个来源、版本和内容哈希。

本设计不把 Skill 变成任意代码插件，也不把 Skill 的 `allowed_tools` 直接升级为权限绕过机制。

## 2. 目标

1. 保留现有 `SkillRegistry`、`SKILL.md` 和内置 Skill 的兼容调用方式。
2. 把“Skill 从哪里来”和“如何选择 Skill”从本地目录实现中抽象出来。
3. 支持稳定的 `skill_id + version + content_hash` 身份。
4. 在一个 Agent 回合内固定 Skill 包版本，避免回合中途热更新导致证据不可复现。
5. 支持以后增加本地覆盖/缓存来源，而不修改 loader 的调用协议。
6. 为模型升级后的 Skill 评测、灰度和回滚保留数据边界。
7. 继续由 Session/Executor 负责真实工具权限；本阶段不把 Skill 元数据误报为硬沙箱。

## 3. 非目标

本阶段明确不做：

- 远程 Skill 市场或自动下载；
- 第三方 Skill 脚本自动执行；
- Skill 自动升级当前正在运行的回合；
- 多级 Skill 继承、依赖解析和动态代码加载；
- 以 Skill 声明替代现有 Executor/Session 权限判断；
- 自动发布、Git push 或无需用户批准的生产更新。

## 4. 核心抽象

### 4.1 SkillSource

Skill 来源提供可发现的描述信息和按身份加载的包：

```python
class SkillSource(Protocol):
    @property
    def source_id(self) -> str: ...

    def list_descriptors(self) -> Iterable[SkillDescriptor]: ...

    def load(self, skill_id: str, version: str | None = None) -> SkillPackage: ...
```

第一版只实现 `DirectorySkillSource`，读取现有受信任目录 `evolve/skills/`。后续来源必须显式注册，不能通过 Skill 正文自行声明来源。

### 4.2 SkillIdentity

每个可加载 Skill 使用以下身份：

```text
skill_id       例如 project-verification
version        例如 1.0.0
source_id      例如 builtin-directory
content_hash   规范化 SKILL.md 内容的 sha256
```

`content_hash` 用于审计和回滚确认，不用于替代版本号。

### 4.3 SkillResolver

Resolver 根据当前请求、模型和 Runtime 约束选择一个 Skill 版本：

```python
class SkillResolver(Protocol):
    def resolve(
        self,
        user_text: str,
        *,
        model_id: str,
        runtime_version: str,
        source_preference: tuple[str, ...] = (),
    ) -> SkillSelection | None: ...
```

`SkillSelection` 至少包含：

```text
skill_id
version
source_id
content_hash
package
match_score
```

当前触发词选择逻辑保留为默认 Resolver 行为；兼容性和来源优先级先做可选字段，不改变已有 Skill 的选择结果。

### 4.4 SkillRuntime

SkillRuntime 是单个 Agent 回合的只读快照：

```python
class SkillRuntime(Protocol):
    def select_for_turn(self, user_text: str) -> SkillSelection | None: ...

    def format_prompt_overlay(self) -> str: ...

    def identity(self) -> SkillIdentity | None: ...
```

回合开始时解析一次。后续同一回合的 LLM/tool loop 使用同一个 `SkillSelection`，不会重新读取目录中的最新正文。

## 5. 包格式扩展

现有字段继续兼容：

```yaml
name:
description:
triggers:
allowed_tools:
risk_level:
verification:
version:
```

新增字段采用可选方式：

```yaml
api_version: 1
status: stable
compatibility:
  runtime: ">=0.2.0"
  models:
    - 0x567-flash
    - tokeness-luna
capabilities:
  - inspect_files
  - run_tests
```

第一版只解析并记录 `api_version`、`status`、`compatibility`、`capabilities`；未声明时使用兼容默认值。兼容性字段在第一阶段用于审计和评测标签，不自动阻止现有 Skill 被选择，避免升级时出现静默不可用。

## 6. 来源、优先级与更新

第一阶段来源集合：

```text
builtin-directory
```

设计上保留以下来源优先级：

```text
显式本地覆盖 > 受信任缓存 > 内置目录
```

但本阶段不实现后两者。未来增加来源时必须满足：

1. 来源返回稳定的 `source_id`；
2. 包加载后计算内容哈希；
3. 未通过格式/路径/大小检查的包进入 diagnostics；
4. 不自动执行 `scripts/`；
5. 更新前保留旧版本；
6. 更新失败时继续使用上一个已验证版本。

更新行为采用“下一回合生效”：

```text
回合开始 → 解析并固定 Skill
回合执行 → 不重新加载 Skill
更新完成 → 下一回合才可选择新版本
```

## 7. 与权限系统的边界

`allowed_tools` 在本设计中仍是 Skill 流程建议和审计元数据，不直接成为 Executor 的权限表。

未来如果要实现严格 Skill 工具隔离，应另行增加 `SkillPolicyCompiler`：

```text
Session/Executor policy
        ∩
Skill capability policy
        ↓
EffectiveToolPolicy
```

该策略需要同时作用于：

1. 发给模型的工具 schema；
2. Executor 的二次校验；
3. 工具拒绝事件和审计记录。

本设计只预留 `capabilities` 与 `SkillRuntime` 边界，不在本次接口改造中改变现有权限 owner。当前实现已完成本地目录 Provider、Skill identity、回合级 selection pinning 和可选审计元数据；远程来源与严格 Skill 工具隔离仍未实现。

## 8. 评测与回滚

每个 Skill 版本可关联离线/真实评测记录：

```text
skill_id
version
content_hash
model_id
runtime_version
scenario_id
routing_passed
prompt_loaded
tool_policy_result
verification_result
run_id
created_at
```

评测结果不能直接修改生产默认版本。推荐流程：

```text
新 Skill 版本
  → 临时评测
  → 0x567-flash / 其他模型对比
  → 人工确认
  → 标记 stable
  → 下一回合生效
```

失败时通过固定 `skill_id@version` 回滚，而不是恢复一份不可追踪的文件副本。

## 9. 兼容性要求

现有调用方继续可用：

- `SkillRegistry.discover(root)` 保留；
- `registry.skills` 保留；
- `registry.select(text, limit=1)` 保留；
- `registry.load(name)` 保留；
- 当前 `skill_index` / `skill_context` overlay 名称保留。

新接口通过适配层接入，不要求一次性修改所有调用方。旧调用若不提供模型/runtime 信息，使用默认内置来源和现有触发词选择。

## 10. 验收标准

### Registry/Source

- 内置目录源能发现当前 11 个 Skill；
- 现有 Registry 单测全部保持通过；
- 相同 Skill 可加载并计算稳定 content hash；
- 非法路径、超限正文和坏 frontmatter 仍进入 diagnostics；
- 不执行 Skill 脚本。

### Resolution/Version

- 同一请求可解析为明确的 `skill_id/version/source_id/content_hash`；
- 回合内多次构建 prompt 使用同一 Skill identity；
- 更新 Skill 文件不会改变已经开始的回合；
- 下一回合可以读取新版本；
- 未声明新字段的旧 Skill 行为不变。

### Runtime/Live

- 使用真实 `0x567-flash` 完成至少四类代表性流程：项目扫描、计划、验证、发布前检查；
- 记录实际模型、Skill identity、工具调用和最终验证结果；
- 失败回合不被报告为 Skill 通过；
- 临时评测不写生产项目。

### 回滚

- 可以按 `skill_id@version` 选择旧版本；
- 当前版本损坏时不阻塞整个 Agent，能报告 diagnostics 并回退到已验证版本或明确无可用版本；
- 回滚后下一回合使用旧版本 identity。

## 11. 预计改动边界

首个实现计划只允许触及：

- `agent-core/skill_registry.py` 或新增等价 Provider 模块；
- `agent-core/loader.py` 的 Skill overlay 接入；
- Skill Registry/Loader 相关单测；
- `docs/` 中本设计和必要的运行说明。

不触及：

- 生产项目数据；
- 外部 Skill 下载；
- Executor 的现有权限 owner；
- 不相关的 Desktop/UI、Runaway 状态机或模型路由实现。

## 12. 实现状态与后续边界

已确认并实现：

1. 第一版只实现 `DirectorySkillSource`，暂不做远程/缓存来源；
2. Skill 版本更新采用“下一回合生效”；
3. 本次不改变 `allowed_tools` 的 advisory 语义；
4. 严格 Skill 工具隔离作为后续独立任务，不与 Provider 接口混做。

后续如果需要远程来源、模型兼容性硬门禁或 Skill 级工具沙箱，应新增独立设计和评测矩阵。
