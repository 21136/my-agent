# 执行可靠性（EXEC-RELIABILITY）

> 版本 **0.8.0** · 2026-08-02 · **状态：D1 已签 · M3a+M3b done**  
> Phase **35** · RUNTIME-GUARDS **G14**  
> 关联：[RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) · [SHELL-CHANNEL.md](./SHELL-CHANNEL.md) · [RUN-SERVICE.md](./RUN-SERVICE.md) · [TOOL-RETRY.md](./TOOL-RETRY.md) · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md)  
> 触发：huiyi 联调——假「前端 OK」；`node_modules` 截断；`rmdir`/`npm install` 超时或取消导致剧本空转。

---

## 0. 一句话

**宣称结果须过后置条件；同招失败须熔断；修好环境靠本地可执行的长任务能力——不靠日志剧本猜病因，不外包给外部 Agent 产品。**

---

## 1. 问题归类

| 表象 | 共性根因 |
|------|----------|
| 说「已启动 / 可打开 :3000」实际拒绝连接 | **假成功** |
| 同一命令连败仍再试 | **无熔断** |
| 认出依赖坏了，但 `rmdir` 120s 超时、`install` 被取消 | **执行面太脆**（非「判断文案」问题） |
| 日志正则剧本误判类/路径 | **过度启发式**（见 §3.4 废止） |

**非目标**

| 非目标 | 理由 |
|--------|------|
| 接入 OpenHands / Aider / Claude Code 等整仓 Agent | 要的是**只属于本仓库的项目助手**；可**借鉴思路**，源码落在 `my-agent` |
| 用剧本库覆盖 npm/sql/端口等生态 | 误判成本高（huiyi：类 E 挂 npm 剧本、status 误触 port-dead） |
| Playwright 真浏览器断言 | 仍 defer；端口/alive/ready 足够 M3 |
| 替代 Progress Gate / Checker | 正交组合 |

---

## 2. 与已有闸门的分工

```text
G13 空头动作门     →  说要干却不调工具
TOOL-RETRY         →  参数/schema 错才免配额
Progress Gate      →  勾 TASKS 要本回合对口证据
Checker 完成声明门 →  非 PASS 不许「已验收/沉淀完成」
G14 本文（保留）   →  成功声明后置条件 + 同指纹熔断 + 侧栏可见
G14 M3（新增）     →  本地长命令超时分层 + 显式修复工具（可选）
（废止）剧本 nudge →  不再用 stderr 正则自动指路
```

---

## 3. 原语（修订）

### 3.1 失败分型（保留为观测，弱执法）

A–F 分型可继续写入 `evolve_log` / 侧栏 `failure_class`，供人看。  
**M3 起：分型不得自动注入「请按某某剧本执行」类内核消息。**

| 类 | 例 | free-retry？ | 同招再试？ |
|----|-----|--------------|------------|
| **A** | schema | 是 | 修正后 |
| **B–E** | 依赖/缺表/鉴权/进程死 | 否 | 计熔断；换招由**模型或用户**决定 |
| **F** | 取消/超时 | 否 | 停 |

**例外（BUG-024 · T-4242）**：同 segment 内 **`inline_write_max` 重复 ≥N（默认 2）** → 停 tool + staging 内核；第 2 次起 **不再** TOOL-RETRY inline。见 §3.6。

### 3.2 后置条件（保留 · M0）

起服成功话术须本回合 `run_service` **ready + alive**（等）；否则改写 + notice。  
工具结果可 `ok`；挡的是助手口头成功（Q3）。

### 3.3 熔断（保留 · M0）

同 execute segment、同 call 指纹连续 ≥ N（默认 3）→ 内核熔断提示 + 禁止同招。  
解除：新用户消息 / 新 segment。

### 3.6 重复 inline 写入 guard（BUG-024 · fixed）

| 项 | 说明 |
|----|------|
| 问题 | `validation_error` + `inline_write_max` 为 A 类 → 不计 P5/G14 → guard 连刷 |
| 规则 | streak ≥ `MY_AGENT_INLINE_WRITE_GUARD_MAX`（默认 **2**）→ `inline_write_guard_blocked` → agent 停 tool |
| 内核 | `EXEC_INLINE_WRITE_NUDGE_MESSAGE`（staging 路径，非 core.txt） |
| 重置 | `begin_turn` / 新 user 消息 |
| 文档 | [bugs/2026-08-05-inline-write-repeat-guard-loop.md](./bugs/2026-08-05-inline-write-repeat-guard-loop.md) · IT-98 |

### 3.4 剧本库 — **废止（D1 · 2026-08-02）**

| 已实现（M1） | M3 处置（待签字后落地代码） |
|--------------|------------------------------|
| `P-npm-corrupt` / `P-sql-missing` / `P-port-dead` 自动 nudge | **关闭注入**（`queue_playbook_nudge` 空操作或删调用） |
| 侧栏「剧本：P-…」 | 改为不展示；或仅展示 `failure_class` |
| `core.txt` 剧本禁令条文 | 删或改为「勿盲重试；用长超时命令 / 显式工具」 |
| IT-162 | 保留为历史；新增 IT 断言「不再注入剧本文案」 |

**理由（用户已决倾向）**：剧本靠日志启发式，**反而让判断不准确**；huiyi 联调证明「路指对了、车跑不动」时，问题在执行超时而非再多一条正则。

### 3.5 本地执行硬化（M3 · 新主轴）

产品定位：**源码与进程均在用户机器上的个人项目助手**。  
不引入外部 Agent 依赖；只把「长任务跑得完」做进现有 `run_command` / 可选薄工具。

| 项 | 默认提案 |
|----|----------|
| **超时分层** | 保留短默认；匹配 `npm|pnpm|yarn install`、`rmdir`/`rimraf`/`Remove-Item.*node_modules` 等走**长超时**（建议默认 900–1800s，环境变量可配） |
| **显式修复工具（可选）** | `repair_node_modules`：一次 confirm → 删 `node_modules`（可选 lock）→ package manager install → 返回是否干净；**由模型或用户点名调用**，不由剧本自动触发 |
| **与熔断关系** | 长任务失败仍计熔断；换用 `repair_*` 或改 command 即新指纹 |
| **与后置条件** | install 成功 ≠ 可声称「页面可用」；起服仍须 ready+alive |

---

## 4. 里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| **D0** | §3 初版四原语签字 | **done** |
| **M0** | 后置条件成功声明门 + 熔断 N=3 | **done**（IT-160/161） |
| **M1** | 失败分型 + 剧本 nudge | **done**（IT-162）· **策略上由 D1 废止自动剧本** |
| **M2** | 侧栏可靠性条 | **done**（S-160） |
| **D1** | 废止剧本自动注入；采纳 §3.5 本地执行硬化 | **done**（2026-08-02 用户：「开始动手吧」） |
| **M3** | 关剧本 nudge + `run_command` 超时分层 +（可选）`repair_node_modules` | **done**（IT-163/164/165） |

---

## 5. DOC-04（M3）

### 5.1 影响矩阵

| 面 | 影响 |
|----|------|
| `run_command` / SHELL-CHANNEL | **是**（超时分层） |
| evolve 新工具（若做 repair） | **是** |
| `exec_reliability` / agent 注入 | **是**（关 playbook nudge） |
| 侧栏 reliability | **小**（去掉剧本行） |
| 后置条件 / 熔断 | **保持** |
| 外部 Agent / 云托管 | **否**（明确不引入） |

### 5.2 回归 ID

| ID | 内容 |
|----|------|
| **IT-160～162 / S-160** | 历史保留；M3 不破坏后置条件与熔断 |
| **IT-163** | 剧本自动 nudge **不再**出现在 transcript |
| **IT-164** | 匹配 install/rmdir 类命令使用长超时（或配置生效） |
| **IT-165** | （若做）`repair_node_modules` dry_run/confirm 路径 |

---

## 6. 开放问题 → 默认提案（D1）

| # | 问题 | 默认 |
|---|------|------|
| Q6 | 是否保留 failure_class 日志？ | **是**（观测）；不驱动自动文案 |
| Q7 | 是否实现 `repair_node_modules`？ | **M3a 先做超时分层**；M3b 再薄工具（可同一 PR 或随后） |
| Q8 | 长超时秒数？ | **`MY_AGENT_RUN_COMMAND_LONG_TIMEOUT_SEC=1800`**；短默认不变 |
| Q9 | 侧栏是否仍显示「剧本」？ | **否**；显示后置条件 + 熔断 + 可选 failure_class |
| Q10 | 引入 OpenHands 等为依赖？ | **否**；只抄思路进本仓 |

---

## 7. 签字

**D0（已签）**

- [x] 后置条件 + 熔断 + DOC-04 开 M0（2026-08-02）

**D1（已签 · 2026-08-02「开始动手吧」）**

- [x] **废止**剧本自动 nudge（§3.4）
- [x] 采纳 §3.5 本地执行硬化（超时分层；repair 可选、显式调用）
- [x] 采纳 §6 Q6–Q10 默认
- [x] 明确：不外包、不整仓接入外部 Agent；助手源码只在 my-agent

---

## 8. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0–0.5.0 | 2026-08-02 | D0～M2：后置条件、熔断、分型+剧本、侧栏 |
| 0.6.0 | 2026-08-02 | **D1 草案**：废止剧本主轴；改本地执行硬化；拒外部 Agent 整仓接入 |
| 0.7.0 | 2026-08-02 | D1 签字；M3a 开工（关剧本 + 长超时） |
| 0.7.1 | 2026-08-02 | M3a：关剧本 nudge；run_command 长超时分层；IT-163/164 |
| 0.8.0 | 2026-08-02 | M3b：`repair_node_modules` 显式工具；IT-165 |
