# 工具目录（按作用分层 · 取消主题硬锁）

> 版本 **0.1.3** · 2026-07-31  
> 状态：**已实施 · done**（Phase 23 · M0～M5 + Mp/Mq/Mr）  
> 触发：主题过滤把「按需加载」做成了执行硬锁；模型已知工具名却被拒 → 不省 context，只增阻力。  
> 关联：[TOOLS.md](./TOOLS.md) · [SHELL-CONSOLIDATION.md](./SHELL-CONSOLIDATION.md) · [MEMORY.md](./MEMORY.md) · `TASKS.md` Phase 23  
> **旁路优化**：提示词瘦身 **Mq / Mr**（§9）— 与工具硬锁解耦，已随本 Phase 落地。

---

## 0. 实施分片（防一次改太多）

**原则**：每个 M 可独立合并/验收；**未验收不进下一 M**。先解锁（体感止血），再换导引（省 context），最后收文档。

| 片 | 做什么 | 刻意不做 | 单测/验收 |
|----|--------|----------|-----------|
| **M0** | 只写 `evolve/tool-catalog/` 真文件（INDEX + 空/薄桶） | 不改 loader/executor/prompt | 文件存在；INDEX ≤2KB |
| **M1** | **只**放开硬锁：`allowed_evolved` = 全部 `active` | 不改 overlay / prompt 文案 | S-80 · S-82 |
| **M2** | **只**删 loader 主题锁定 hints（C6） | 不改 core/topic prompt；不换 INDEX 注入 | 无「确认 … 主题后可用」hints |
| **Mp** | **只**改旧系统提示（C7）：`core.txt` + 主题 prompt 去硬锁措辞 | 不改 allowlist（应已 M1）；不填满桶 | grep 无「确认 workflow/coding/data 主题后」类执行门 |
| **M3** | overlay 改为注入 INDEX（C4） | 不删函数亦可先旁路 | overlay 含 INDEX 标记 |
| **M4** | 补齐各 bucket 正文 | 不改运行时 | 每桶列出工具名 |
| **M5** | TOOLS/MEMORY 交叉 + 总回归 | — | IT-80～81 · **done** |
| **Mq** | 提示词：去掉过时 CLI/多壳地图（§9） | 不改 allowlist；不迁长手册 | grep 无 grow/daily 当主路径；桌面为主入口 |
| **Mr** | 提示词：`write_evolve` 长段迁出 core → evolve 桶（§9） | 不删硬边界短句 | core 明显变短；细则在 bucket |

推荐口令：`做 M0` → `做 M1` → `做 M2` → `做 Mp` → `做 M3` → `做 M4` → `做 M5`；  
旁路：`做 Mq`（建议 **Mp 之后**）、`做 Mr`（建议 **M4 有 evolve 桶之后**）。  
（**Mp 必须在 M3 前或紧挨 M3**。**Mq/Mr 禁止与 M1～M3 同一改动波次**。）

禁止：「一次做完 Phase 23」。

---

## 1. 已决（2026-07-31）

| ID | 决议 |
|----|------|
| **C1** | 桌面只需 **普通窗口** 与 **项目窗口**；项目 = 普通 + 侧栏（Plan/TASKS）。不再用壳/主题决定「能不能调工具」。 |
| **C2** | **取消** evolved 工具的主题硬锁：凡 `status=active` 均可 `run_evolved`（仍受 confirm / WRITE-SCOPE / 项目计划门等既有门禁）。 |
| **C3** | 省 context 的方式改为 **分层工具目录文档**，不是拒调。 |
| **C4** | **默认每轮 system 只注入 INDEX 短目录**（作用分桶 + 指向二层文档路径）；细节靠模型 `read_file` 再读。 |
| **C5** | 主题索引（`_index`）**可继续**服务 prompt / memory 的按需加载；**与工具执行面解绑**。 |
| **C6** | 删除/停用「点名未开放工具请确认主题」类 capability hints（与 C2/C4 冲突）。 |
| **C7** | **同步改旧系统提示**：`agent-core/prompts/core.txt` 与 `evolve/prompts/{coding,workflow,data,…}.md` 中凡「须确认主题才能调工具 / 须出现在 session catalog」的措辞，改为指向 INDEX / `read_file` 桶文档；**主题 prompt 可仍按需加载，但不再写执行硬锁。** |
| **C8** | **Mq**：更新过时产品地图——桌面为主入口；窗口 = 普通 / 项目（+侧栏）；`project.md` 等不再把 **grow/daily** 当用户主路径（后端会话线标签可留技术名，prompt 用语对齐产品）。 |
| **C9** | **Mr**：`core.txt` 只留硬边界；`write_evolve` / staging / base64 长手册迁到 `evolve/tool-catalog/buckets/evolve.md`（或等价），需要时再 `read_file`。 |

### 非目标（本 Phase）

| 非目标 | 说明 |
|--------|------|
| 一次删光 `meta.topics` / 主题确认 UI | 可后续；先解绑工具 |
| 自动生成完美目录（LLM 写 INDEX） | 先人手维护 + 校验脚本可选 |
| 取消 confirm / 写路径 deny-list | 权限仍走原管线 |
| 合并 pet 窗 | pet 仍独立 |
| Mq/Mr 与 M1～M3 同波大改 | 防回归难拆 |

---

## 2. 问题陈述（为何改）

原设计：「磁盘不是瓶颈，context 才是」→ 主题拆分做 **组织与按需加载**。

实现漂移：

1. 会话 `topics` 常为空 → 清单几乎只剩 common  
2. executor **硬拒**不在清单的工具  
3. hints 又把未开放工具名写进 system → **既费 token 又制造「不能调」**

结论：**工具侧硬锁不是「省 context」，是权限幻觉**；真正省 context 应是「短索引 + 按需读详情」。

---

## 3. 窗口模型（产品）

| 窗口 | UI | 工具面 |
|------|-----|--------|
| **普通** | unified · perspective `default`（或 night） | 同一套：INDEX + 全 active 可调 |
| **项目** | unified · perspective `project` + 左侧栏 | **相同**工具面；侧栏多 Plan Agent / TASKS |

后端 `active_shell` / `shell_sessions` 若仍存在：仅作**会话线标签**（续接用），**不**过滤 evolved 清单。

---

## 4. 分层目录（省 context）

### 4.1 层级

```text
evolve/tool-catalog/          # 建议落盘位置（真源，随 evolve 版本）
  INDEX.md                    # L0：短目录 —— 唯一默认可注入 system 的工具导引
  buckets/
    write.md                  # L1：写/改文件类
    run.md                    # L1：执行 / 构建 / 测试
    organize.md               # L1：整理目录 / 批量
    project.md                # L1：项目进度 / 目录查询
    evolve.md                 # L1：进化写工具 / clone
    …
  # L2 可选：单工具仍用 tool.toml description + README；需要时 read_file
```

也可放在 `docs/tool-catalog/`；**已决倾向 `evolve/tool-catalog/`**（与工具同仓、随进化更新）。

### 4.2 INDEX 形态（示意 · 宜短）

```markdown
# 工具索引（本会话默认只看本页）

需要细节时：read_file evolve/tool-catalog/buckets/<桶>.md
调用：run_evolved · tool_name=<名>（须 active）

| 桶 | 何时读 | 路径 |
|----|--------|------|
| 写文件 | 新建/改文本、搬移、回收站 | buckets/write.md |
| 设计文档 | 四类设计文档、图表源及本地渲染 | buckets/design.md |
| 执行构建 | npm/mvn/python/测试/demo | buckets/run.md |
| 整理 | 按扩展名/去重/归档 | buckets/organize.md |
| 项目 | 进度勾选、项目目录 | buckets/project.md |
| 进化 | write_evolve、git_clone 进 tools | buckets/evolve.md |

Builtin（始终）：read_file · list_dir · grep · web_search · fetch_url · run_evolved
```

目标体量：**远小于**今日「全量 evolved 清单 + capability hints」。

### 4.3 每轮注入规则（C4）

| 注入 | 不注入（默认） |
|------|----------------|
| Builtin 说明（可压缩） | 按主题拼的全量 evolved catalog |
| `INDEX.md` 全文或截断版（设上限，如 ≤2KB） | 各 `buckets/*.md` |
| 既有 core / 项目 overlay（若有） | 「确认 xx 主题后可用 yy」hints |

模型需要某桶时：`read_file evolve/tool-catalog/buckets/….md`，再 `run_evolved`。

### 4.4 登记约定

- **按作用分桶**，不按 `evolve/tools/<scope>/` 目录名强制一一对应（目录 scope 可保留作物理存放，**不再决定可见性**）。  
- 新工具 `status→active` 时：更新对应 bucket + INDEX 一行（人工或 `write_evolve` 后检查清单）。  
- `suspect` / `archived`：**不进**执行面，也**不进** INDEX。

---

## 5. 运行时变更（按 M 切开 · 动手时）

| M | 层 | 做什么 | 风险 |
|---|-----|--------|------|
| M1 | Allowlist | `session_evolved_allowlist` / `_sync_allowed_evolved` → 全部 `active`；主题不再过滤执行面 | **最高收益 / 改动面相对小**；先做 |
| M2 | Hints | `format_capability_hints` 去掉「确认 xx 主题后可用」段 | 小 |
| **Mp** | **System prompts** | `core.txt`：catalog 句改为 INDEX；`evolve/prompts/workflow.md` / `coding.md` / `data.md` 去掉「确认主题后才可调用」；可改为「详见 tool-catalog/buckets/…」 | 小；**漏做会导致模型自设门禁** |
| M3 | Overlay | 注入 INDEX 文本；停用或旁路按 topics 拼的全量 catalog | 中：注意截断上限 |
| M0/M4 | 文件 | catalog 真源 | 无运行时风险 |
| M5 | Docs/测试 | 交叉修订 + 打包 | 低 |

权限仍保留（各 M 都不动）：`confirm`、WRITE-SCOPE、项目 draft 计划门、task-stop、host scope。

**M1 注意**：放开后模型可能一次看到更多可调用名（若 M3 未做、旧 catalog 仍在）。可接受：先止血「不能调」；省 context 交给 M3。若 M1 后 token 明显涨，**立即做 M3**，不要回头加锁。

---

### 已知冲突文案（Mp 必改 · 2026-07-31 盘点）

| 文件 | 问题句（摘要） |
|------|----------------|
| `agent-core/prompts/core.txt` | `tool_name` must appear in the **session evolved catalog** |
| `evolve/prompts/workflow.md` | **确认 workflow 主题后**，以下工具经 `run_evolved` 调用 |
| `evolve/prompts/coding.md` | 本主题在会话确认 **coding** 后注入…（注入时机可留；勿暗示未确认则不可 `run_demo` 等） |
| `evolve/prompts/data.md` | 确认会话含 **data** 主题…（造工具步骤可留「目录用 data/」；勿当执行锁） |
| `loader.format_capability_hints` | 「确认 workflow/coding 主题后可用」（属 M2，非 prompt 文件） |

`evolve/prompts/project.md`：计划门 / npm_exec 等**保留**（不是主题硬锁）。

---

## 6. 影响矩阵（DOC-04）

| 面 | 影响 | 回归 |
|----|------|------|
| Context | 每轮工具导引变短；偶发多一次 read_file 读桶 | 量 token / 轮次体感 |
| 执行 | 任意 active 可调 | 不再因空 topics 拒 `patch_file` 等 |
| 主题 | 与工具解绑 | prompt/memory 行为可暂不变 |
| 桌面 | 仍两窗；无新「主题确认才能用工具」依赖 | 普通/项目冒烟 |
| 安全 | 不扩大写权限语义 | confirm 用例仍绿 |

### 回归 ID（动手时）

| ID | 断言 |
|----|------|
| S-80 | `topics=[]` 时仍可 `run_evolved(patch_file)`（若 active） |
| S-81 | system overlay 含 INDEX 关键词；**不含**「确认 workflow 主题后可用」 |
| S-82 | `suspect` 工具仍不可调 |
| IT-80 | 模型路径：读 INDEX →（可选）读 bucket → 调用；无「不在清单」误拒 |
| IT-81 | 项目窗与普通窗 allowlist 一致（项目另有侧栏/计划门） |

---

## 7. 实施门

- **文档**：本文件（含 §9 Mq/Mr）+ TASKS Phase 23 分片 → **done**  
- **代码 / 改 prompt 正文**：分片已全部落地（M0～M5 · Mp/Mq/Mr）

---

## 8. 与旧文档关系

| 旧条款 | 处理 |
|--------|------|
| TOOLS.md「主题过滤清单」「仅确认主题后注入」 | Phase 23 实施后改为指向本文件；硬锁删除 |
| MEMORY.md 主题拆分 | **保留**其对本 prompt/memory 的动机；**不**再推导「必须锁工具」 |
| Phase 21 project 并入 `scope=project` | 硬锁取消后自然满足；F1 可简化为「无需特例」或保留无害 |
| DESKTOP / SHELL-CONSOLIDATION 两窗 | Mq 与产品措辞对齐 |

---

## 9. 提示词旁路优化（Mq / Mr · 已决）

> **Mq**：done（2026-07-31）。**Mr**：done（2026-07-31）。

> 与「取消工具硬锁」正交：即使工具门已开，旧 prompt 仍可能 **教错地图** 或 **每轮灌低频手册**。

### Mq — 产品地图对齐（C8）

| 改 | 不改 |
|----|------|
| `core.txt` Identity：会话以**桌面**为主；CLI 命令作补充 | 删掉压缩/记住等能力本身 |
| `project.md`：「切 grow 造工具」→「到**普通窗口**/非项目线再 `write_evolve`」 | 计划门 / 一停 / ENV / 构建纪律 |
| 主题 prompt 里「本仓库 MAP/TASKS」标成 **维护 agent 内核时**；用户项目跟项目三件套 | 删掉 coding 习惯全文（可后移） |

验收：`grep` prompt 目录无「顶栏切 grow/daily」类主路径；桌面两窗表述一致。

### Mr — core 瘦身（C9）

| 改 | 不改 |
|----|------|
| 迁出 `write_evolve` 逐步说明书（staging / base64 / on_conflict 长列表）→ `buckets/evolve.md` | 「不许假装执行」「只经 run_evolved」「confirm」短句 |
| core 造工具处改为一行指针：`read_file evolve/tool-catalog/buckets/evolve.md` | safety.md；turn discipline 主干 |

验收：`core.txt` 行数明显下降（目标：Tool discipline 造工具块 ≤5 行指针）；evolve 桶含原细则。

### 建议顺序

```text
… → Mp → M3 → M4(含 evolve 桶骨架) → Mq → Mr → M5
         或 Mp 后先插 Mq（地图），Mr 仍等 M4
```
