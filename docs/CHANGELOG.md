# 文档变更记录

## [0.2.14] - 2026-07-09

### 修订

- `RUNTIME.md` v0.2.5：`LLM_TIMEOUT_SEC=120`；pro/flash context 上限；`--record` §2.3；`context_pressure` M2
- `TOOLS.md` v0.2.3：confirm `a` §6.3；超长 tool 落盘 §6.4
- `GOVERNANCE.md` v0.2.10：§5.2.1 软冲突（≥3 词）
- `EVOLVE.md` §6：防重复精确相等 + ≥2 词软警告
- `PROJECT.md`：`failure_count` 移除；`--record`；`requirements.txt`
- `LAYERS.md`：M1c 与 PROJECT 对齐
- `TASKS.md`：T-006/108/109/201/207/601 验收对齐
- 新增根目录 `requirements.txt`（`httpx>=0.27`）

---

## [0.2.13] - 2026-07-09

### 修订

- `GOVERNANCE.md` v0.2.9：§3.1 L2 路径（仅 `read_file`→`evolve/memories/**`）
- `MEMORY.md` v0.2.3：§8 审计事件对齐 `entity_used`
- `PROJECT.md` §7.3：M1c 紧随 M1b、不阻塞首版
- `TASKS.md`：Phase 3 / T-602a 对齐

---

## [0.2.12] - 2026-07-09

### 修订

- `RUNTIME.md` v0.2.4：默认 `LLM_MODEL=deepseek-v4-flash`；含 `coding` 主题 → `LLM_MODEL_CODING=deepseek-v4-pro`；S2 仍用 flash
- `evolve/_index.toml`：`coding` 标注 `llm_model`
- `MEMORY.md`：索引示例同步
- `TASKS.md`：T-201 / T-205 验收对齐

---

## [0.2.11] - 2026-07-09

### 修订

- `RUNTIME.md` v0.2.3：§8 context 压缩常数已决（对齐 Cursor 机制；85% 自动、`压缩` 手动、K=8、digest≤8k、messages.jsonl 不截断）
- `TASKS.md`：T-207 / T-208 验收对齐

---

## [0.2.10] - 2026-07-09

### 修订

- `TOOLS.md` v0.2.2：`fetch_url` 已决（`httpx`、stdlib HTML、SSRF、限额、§7.5 schema + `final_url`）
- `TASKS.md`：T-104c 验收对齐

---

## [0.2.9] - 2026-07-09

### 修订

- `TOOLS.md` v0.2.1：`web_search` 后端已决（默认 DeepSeek 原生搜索 + 可选 Brave）；§7.4 env / 行为 / schema
- `RUNTIME.md` v0.2.2：新增 §10 使用侧反馈（exit、`MY_AGENT_FEEDBACK_ON_EXIT`、L2+、单实体）
- `GOVERNANCE.md` v0.2.8：§6.5 exit 反馈协议
- `PROJECT.md` §4.4：反馈分期与开关
- `TASKS.md`：T-104b 验收对齐；T-602 拆 a/b/c

---

## [0.2.8] - 2026-07-09

### 修订

- `MEMORY.md` v0.2.2：§9 三条已决（本会话 memory 不重复列表；换主题默认替换、`加主题` 追加；evolve 变更不重载、换主题时重载）
- `RUNTIME.md` v0.2.1：`加主题` 命令；overlay 重载表
- `TASKS.md`：T-205 对齐主题替换/追加

---

## [0.2.7] - 2026-07-09

### 新增

- `docs/GOVERNANCE.md`：M4 治理（review / audit、suspect、`ReviewReport` schema、Git 习惯）

### 修订

- `PROJECT.md` §4.5：引用 GOVERNANCE；review vs audit 分工
- `EVOLVE.md` §9：M4 治理类 evolve_log 事件
- `TASKS.md`：Phase 6 拆 T-601～T-606、T-601a/b

---

## [0.2.6] - 2026-07-09

### 新增

- `docs/EVOLVE.md`：M2 进化写入（proposal 格式、检查点、防重复、接受路由）

### 修订

- `PROJECT.md` v0.2.6：§4.3 / R8 / §7.1 统一为 ≤2/检查点；引用 EVOLVE
- `TASKS.md`：T-005e done；T-403/T-407 对齐已决项
- `EVOLVE.md` §12：新会话不软问、口头升格 ≤1/会话、pending supersede、其余默认
- `MEMORY.md` / `RUNTIME.md`：交叉引用 EVOLVE

---

## [0.2.5] - 2026-07-09

### 新增

- `docs/RUNTIME.md`：对话层（续接 session、system 拼装、DeepSeek、digest 压缩）

### 修订

- `MEMORY.md` v0.2.1：短期记忆含 digest；默认续接 thread
- `PROJECT.md` §4.3：proposal 仅显式触发；exit 不强制
- `TASKS.md`：Phase 2 重拆 T-201～T-210；T-005d

---

## [0.2.4] - 2026-07-09

### 新增

- `evolve/_index.toml`：统一主题索引（prompt + memory + tool_dirs）
- `docs/TOOLS.md` v0.2：6 Builtin、主题 evolved、`tools/common/`

### 修订

- `MEMORY.md` v0.2：索引迁至 `evolve/_index.toml`；主题加载含 evolved 清单
- `TASKS.md`：T-104a～c、T-308、T-005c；种子 `write_text` 在 common/
- `PROJECT.md` §4.4、§8 目录

---

## [0.2.3] - 2026-07-09

### 新增

- `docs/MEMORY.md`：记忆三件套（prompt / 久远 / 短期）+ 按主题两阶段路由

### 修订

- `LAYERS.md` §2.2：L1 拆为三件套，引用 MEMORY.md
- `TASKS.md` Phase 3：T-301～T-307 对齐记忆设计；T-005b 文档 task
- `PROJECT.md` §4.4、§8 目录、§7.3 M1c：与 MEMORY 一致

---

## [0.2.2] - 2026-07-09

### 新增

- `docs/LAYERS.md`：先 tool 后 skill 的分层与建设顺序
- `docs/TOOLS.md`：工具系统设计（builtin / evolved、`tool.toml`、执行流）
- `docs/TASKS.md`：任务清单 T-001～T-906，细分到每个 task

### 修订

- `PROJECT.md` §7：M1 拆为 M1a/M1b/M1c；M1 不含 skill；引用 TASKS.md
- `README.md`：文档索引与审阅顺序

---

### 作者修订（去 Word 专项化）

- `PROJECT.md` 升至 0.2.1：MVP、验收、里程碑改为 **领域无关**
- 取消 M0.5 Word 脚本里程碑；下一步为 **M1 CLI**
- `templates/` 重命名为 `assets/`（通用可选静态资源）
- Word/复杂文档讨论移至 **附录 C**（可选场景，非核心）

### 保留（自 v0.2.0 评审，仍有效）

- §4.4 使用侧协议、proposal 降噪、Git 真源、行为导向验收
- Cursor SKILL.md + `meta.json`、无技术沙箱诚实声明

---

## [0.2.0] - 2026-07-09

### 整合四轮 LLM 评审

评审文件：

- `docs/reviews/2026-07-09-grok-review.md`
- `docs/reviews/2026-07-09-review-cursor-agent.md`
- `docs/reviews/2026-07-09-claude-review.md`
- `docs/reviews/2026-07-09-gpt-5-5-review.md`

整合摘要：`docs/REVIEW-SUMMARY.md`

### PROJECT.md 主要变更

- 版本升至 0.2.0
- 新增 §2.3 与 Cursor 生态关系（已决）
- 新增 §4.4 使用侧协议（显式调用、引用日志、失效反馈）
- 重写 §4.3（对话边界、降噪、字段、evidence 原文）
- §4.5 冲突/失效/回滚（含 `my-agent review`）
- 新增 §5.4 母版生命周期、§5.5 无母版降级、§5.6 Word 工具路径
- 重写 §6.1 部署架构（Git 真源）
- 收紧 §6.3 技术栈（MVP vs 后续）
- 诚实化 §6.4 安全边界
- 重排 §7 里程碑（M0.5～M4）、重写验收标准
- 更新 §8 目录与 `meta.json` 示例
- 扩展 §9 风险（R7～R9）
- §10 开放问题 → 已决/剩余表
- §12 记录评审决议

### 采纳

- 使用侧闭环、M0.5 优先、行为导向验收、Git 真源、SKILL.md 兼容、proposal 降噪、隐私默认值、无沙箱诚实声明

### 拒绝

- 第二 LLM 审校 proposal；MVP 多 LLM adapter / SQLite

### 延后

- 自动路由、expires_at/confidence、进程沙箱、Web UI（均标 M4+）

---

## [0.1.0] - 2026-07-09

### 新增

- 项目脚手架目录结构（U 盘 `D:\my-agent`）
- `docs/PROJECT.md` 主项目文档
- `docs/REVIEW-GUIDE.md` 多 LLM 评审指南
- `README.md` 入口说明
- `data/state.json` 初始状态
