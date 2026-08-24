# Terminal 交互界面改造设计

> 状态：终端 UI 第一轮实施中；每项改造先更新本设计，再同步实现和验收。  
> 适用版本：Ink Terminal UI（`MY_AGENT_TERMINAL_UI=ink`）  
> 关联设计：[TERMINAL-MODE.md](./TERMINAL-MODE.md) §6.6

## 1. 目标

把当前 Terminal UI 从“能完成一轮 Agent 对话的 TUI”提升为“适合长时间、高频使用的 Agent 工作台”。重点不是增加装饰，而是降低以下交互成本：

1. 用户不知道当前是否仍在运行、运行到了哪一步；
2. 用户滚动查看旧内容后，无法快速回到最新输出；
3. 输入、确认、取消和命令操作缺少一致的快捷键和提示；
4. 长回答、思考过程和工具活动同时出现时，视觉焦点不稳定；
5. 窄终端窗口下状态栏和内容容易拥挤。

## 2. 现状判断

当前布局已经具备清晰的渲染分层：

```text
Welcome → Transcript → Live Thinking / Live Assistant → Composer → Status
```

现有优势：

- 思考和回答使用独立 live pane，流式刷新不会把所有内容重新当作已提交 transcript；
- 思考结束后可以折叠，工具活动集中在状态栏；
- transcript 有滚动窗口和虚拟列表预算；
- confirm、plan、activity 已经通过事件管线进入 UI。

现有短板：

- Composer 仍然是单行输入模型，输入历史和命令发现性不足；
- 滚动状态没有足够明显的“已离开底部”和“回到底部”反馈；
- StatusPane 可能同时承载模型、目录、工具、耗时和计划信息；
- 运行中、等待确认、已取消、失败等状态缺少统一的视觉和快捷键语义；
- 终端是字符网格，设计必须优先保证窄宽度、纯色和无鼠标操作可用。

## 3. 设计原则

### 3.1 状态优先

任何时刻用户都应能回答三个问题：

- Agent 是空闲、思考、执行、等待确认还是失败？
- 如果正在执行，当前活动是什么？
- 我输入的内容会发送给 Agent，还是会响应当前确认？

### 3.2 键盘优先，鼠标增强

所有核心操作必须可以用键盘完成。鼠标滚轮只作为滚动增强，不作为唯一入口。终端原生选中、复制行为不由应用接管。

### 3.3 流式内容稳定

流式内容可以增长，但不能让 Composer 位置跳动，也不能因为状态栏文字变化而造成阅读焦点丢失。已提交内容和 live overlay 继续保持分离。

### 3.4 信息渐进披露

默认只显示完成任务所需的信息：当前活动、简短状态和最终结果。详细 reasoning、工具细节和计划步骤可以展开，但不应压住回答正文。

### 3.5 降级可用

在窄窗口、无真彩、鼠标不可用或 Ink 回退到 legacy 时，核心输入、取消、确认和查看结果仍然可用。

## 4. 目标交互模型

### 4.1 底部 Composer

Composer 保持固定在底部，但明确区分三种状态：

| 状态 | 主提示 | 可用操作 |
|---|---|---|
| idle | `> 输入消息` | 输入、提交、历史、命令 |
| working | `> Agent 正在运行 · Esc 取消` | 滚动、取消；默认不提交新消息 |
| confirm | `确认执行 … · [y]es [n]o [a]ll` | 只接受确认选择或取消 |

idle 状态下建议补齐：

- 上下箭头浏览本次终端会话的输入历史；
- `Ctrl+L` 清理视觉屏幕但不删除会话 transcript；
- `Ctrl+C` 第一次取消当前回合，空闲时不退出进程；
- `Ctrl+D` 在空输入时退出；
- `/` 开始命令时显示可用 slash command 提示。

输入历史只保存在当前终端会话内，不写入 Agent 消息历史，不把历史中的敏感内容主动显示到 transcript。

### 4.2 Transcript 阅读位置

定义两个明确的阅读状态：

- **follow mode**：视口跟随最新输出；
- **review mode**：用户主动向上滚动，视口保持当前位置。

进入 review mode 后：

- 新输出继续接收，但不抢回视口；
- Composer 或 Status 显示一行轻量提示：`↑ 已暂停跟随 · ↓/End 回到底部`；
- 回到底部后自动恢复 follow mode；
- 新输出累计超过一屏时，提示显示数量，例如 `↓ 12 行新内容`。

优先支持以下操作：

| 操作 | 行为 |
|---|---|
| `PageUp` / `PageDown` | 半屏滚动 |
| `Up` / `Down` | 浏览输入历史；无历史时小步滚动 |
| `Home` / `End` | 到 transcript 顶部 / 回到底部 |
| 鼠标滚轮 | 按行滚动 |
| `Ctrl+L` | 清屏显示，不改变已保存 transcript |

### 4.3 Thinking 与工具活动

思考区域采用“摘要默认、过程可读”的策略：

- thinking 开始时只占用有限高度，显示最近若干行；
- 长 reasoning 在 live 状态下不无限挤压 assistant 区域；
- assistant 开始输出后，thinking 折叠为一行摘要；
- 工具调用不重复出现在 thinking 正文中；
- Status 显示 `工具名 · 已运行时间`，工具结束后保留简短结果标记。

用户不需要理解 `reasoning.delta`、`tool.active` 等内部事件；这些事件只影响可读状态，不直接暴露协议名。

### 4.4 运行状态与计划状态

状态栏只保留一条主状态和必要上下文，优先级如下：

```text
confirm > failed > working activity > plan step > idle
```

建议显示形式：

```text
● idle · flash · workspace/huiyi
◐ thinking · 正在分析
◐ execute · run_command · 12s
◆ plan · step 2/4 · execute
! failed · exit 1 · Enter 查看
? confirm · [y]es [n]o [a]ll
```

模型和 cwd 属于辅助信息，在窄窗口时可以折叠、截断或移到 Welcome/命令帮助中，不能挤压主状态。

### 4.5 失败、取消和空结果

三种结果必须可区分：

- **取消**：用户主动停止，显示 `cancelled`，不伪装成失败；
- **失败**：工具或模型失败，显示失败原因和可继续操作；
- **空结果**：回合正常结束但没有 assistant 正文，显示明确的空结果提示。

失败信息默认短显示；完整错误仍写入 transcript，避免状态栏变成长日志。

## 5. 窄窗口规则

以 80 列为完整布局基准，以 60 列为可用布局基准：

- 60 列以上：显示完整状态、工具名和 plan step；
- 40～59 列：缩短 cwd、隐藏非关键模型信息，保留主状态；
- 少于 40 列：切换为最小状态行，不能出现横向溢出；
- 所有正文、thinking 和 notice 都必须按终端列宽换行；
- 不使用依赖透明色或浏览器 CSS 的视觉效果。

## 6. 快捷键约定

第一阶段只固定低冲突、高收益的快捷键：

| 快捷键 | 语义 | 适用状态 |
|---|---|---|
| `Enter` | 提交输入 / 确认当前选择 | idle / confirm |
| `Esc` | 取消当前回合或关闭临时提示 | working / notice |
| `Ctrl+C` | 取消当前回合；空闲时保持进程 | working / idle |
| `PageUp` / `PageDown` | 半屏滚动 | 全部 |
| `Home` / `End` | 到顶部 / 回底部 | 全部 |
| `Ctrl+L` | 清理当前屏幕 | 全部 |
| `Ctrl+D` | 退出 | idle |
| `/` | 开始 slash command | idle |

不在本阶段引入复杂的 Vim 模式或多层 modal keymap，避免与 Windows Terminal、PowerShell 和用户既有习惯冲突。

## 7. 实施边界

### 本阶段包含

- Composer 状态提示和快捷键说明；
- 输入历史；
- follow/review 模式及回到底部提示；
- 状态栏优先级和窄窗口压缩规则；
- thinking、tool、plan、confirm、cancel、failed 的统一文案；
- 对应 reducer、输入处理和布局测试。

### 本阶段不包含

- 更换 Ink 或改为 HTML/Web UI；
- 自定义终端鼠标选区和复制实现；
- 图片、复杂背景、透明卡片和自由定位；
- 多窗口、多 pane 工作区；
- 改动 Agent 事件协议的业务语义；
- 删除 legacy `prompt_toolkit` 回退路径。

## 8. 验收标准

### 功能验收

- 用户可以查看和复用当前会话输入历史；
- 用户向上滚动后，新输出不会抢回视口；
- 用户可以用一个明确快捷键回到底部并恢复跟随；
- working、confirm、cancelled、failed、idle 五种状态有稳定且互不混淆的显示；
- 60 列终端下没有横向溢出，核心操作仍可用；
- `Ctrl+C`、confirm 和退出行为在 idle/working/confirm 三种状态下不互相误触。

### 体验验收

- 一轮包含 reasoning、工具调用、plan 和长回答的对话中，Composer 始终可见；
- 用户阅读旧内容时不会被流式刷新强制拉回底部；
- 不看源码也能从 Composer 和 Status 判断下一步可执行操作；
- 失败和取消结果可以一眼区分；
- 终端缩窄后信息按优先级收缩，而不是随机截断。

### 回归验收

- `terminal-ui` 现有 reducer、输入、viewport、committed-blocks 测试继续通过；
- Ink 子进程无法启动时，legacy fallback 行为不变；
- JSONL agent 事件协议和 Python bridge 不因纯 UI 改造而改变。

## 9. 建议实施顺序

1. 先补输入状态模型和当前会话 history；
2. 再实现 follow/review 与回到底部提示；
3. 然后统一状态栏和窄窗口压缩；
4. 最后调整 thinking/tool/plan/confirm 文案和视觉层级；
5. 每一步都先补测试，再做 Windows Terminal 手工 smoke。

本文作为实现依据；每一轮改造完成后同步更新落地状态和验收结果。

## 10. 第一轮落地状态

第一轮实现已完成以下内容：

- 当前终端会话输入历史，连续重复输入去重，支持恢复编辑草稿；
- `Esc` 与 `Ctrl+C` 取消运行中的回合或确认请求；
- `Home` / `End`（含常见终端转义序列）定位 transcript 顶部和底部；
- review mode 的底部提示：`↓ 已暂停跟随 · End 回到底部`；
- working 状态提示：`◐ Agent 正在运行 · Esc/Ctrl+C 取消`；
- 状态栏在窄窗口下压缩 cwd、plan 和模型文本，极窄窗口只保留主活动状态；
- 回到底部时裁剪过长的最新回答尾部，并按 working/review 提示动态预留底部高度，避免 Composer 或 StatusPane 被推出视口；
- Markdown 代码块在视口裁剪后恢复继承的围栏，避免把结尾的 ` ``` ` 当成普通输出；
- 输入历史、取消和 transcript scroll 的单元测试。
- `Ctrl+L` 清屏并保留所有会话状态，`Ctrl+D` 仅在 idle 空输入时退出；
- 清屏、退出边界和确认状态的输入单元测试。
- review 模式累计新输出行数，显示 `N 行新内容`，回到底部后清零；
- 取消与失败结果使用不同的 transcript 和 StatusPane 视觉分类。
- 流式 reasoning/assistant 输出期间允许 PageUp、滚轮和 Home 进入 review mode。

以下内容保留在后续迭代：

- Windows Terminal 手工 smoke 与窄窗口视觉验收。

## 13. 新输出与结果块细则

### 13.1 Review 模式的新输出

- 用户向上滚动进入 review mode 后，记录进入时的 transcript 行数；
- 后续新增的 transcript 内容累计为“新内容行数”，不抢回当前视口；
- Composer 显示 `↓ N 行新内容 · End 回到底部`；没有新增内容时仍显示暂停跟随提示；
- 用户按 `End` 回到底部后清零计数，重新进入 review mode 时重新计数。

### 13.2 取消与失败结果

- 取消结果显示为 `× 已取消本轮执行`，使用中性取消色，不伪装成失败；
- 失败结果显示为 `! 失败 · <短原因>`，使用错误色；完整错误仍保留在 transcript；
- 普通 warning 继续使用 warning 样式，不进入 failed 状态；
- StatusPane 与 transcript 结果块使用同一结果分类，状态优先级高于 idle。

### 13.3 非目标

- 不改变 Python 侧取消、失败和回合结束语义；
- 不在 Composer 中重复展示完整错误堆栈；
- 不让新输出提示出现在 follow mode 或 confirm 面板中。

## 14. 流式输出滚动细则

- Agent 正在输出时，`PageUp`、鼠标滚轮和 `Home` 仍然可进入 review mode；
- 滚动预算必须包含尚未提交的 reasoning/assistant live overlay；
- 进入 review 后不因后续流式增长而抢回视口；
- 用户主动向上或向下滚动后，手动滚动优先，流式增长不得改写滚动位置；
- `End` 仍然回到底部，恢复跟随最新输出。

## 15. 取消通道细则

- Ink 发出的 `turn.cancel` 必须由 bridge 输入线程即时分发，不能等待当前 `handle_line` / Agent 回合返回；
- working 状态收到取消后立即调用 cancel guard，底层 Agent 的取消事件负责中断模型或工具执行；
- confirm 等待期间收到取消也必须解除等待，并以取消结果结束当前回合；
- `Esc` 与 `Ctrl+C` 共用同一取消路径，不能因为 Composer、确认读取或同步执行阻塞而失效。

## 16. 代码输出结尾保护

- Assistant 的代码块必须保留源文本最后一行，不能因为底部 Composer 的 margin 或视口预算被裁掉；
- 虚拟列表估算必须包含 Assistant body 的底部间距，并在切片时同步扣除；
- 代码围栏恢复只负责 Markdown 结构，不得伪造或删除 C/C++/Java 等代码主体的最后一行；
- 长代码输出的验收以最后一个非空源代码行为准，例如 C 函数的 `}`。

## 12. 清屏与退出交互细则

### 12.1 `Ctrl+L` 清屏

- `Ctrl+L` 只清理当前终端可视区域，然后立即重绘 Ink UI；
- 不清空 transcript、输入草稿、输入历史、当前模型或 working/confirm 状态；
- 不向 Python 侧发送消息，不触发新回合或取消当前回合；
- working、review 和 confirm 状态下均可使用。

### 12.2 `Ctrl+D` 退出

- 仅在 idle 且 Composer 为空时退出终端进程；
- Composer 有草稿时不退出，也不提交草稿；
- working 或 confirm 状态下不退出，继续由 `Esc` / `Ctrl+C` 处理取消；
- 退出不发送普通 `input.line`，避免把 EOF 误当成用户消息。

### 12.3 验收

- 清屏后 transcript、底部 Composer 和 StatusPane 仍可见；
- `Ctrl+D` 空输入退出，非空输入、working 和 confirm 状态均不会退出；
- 输入 reducer 对上述边界有单元测试。

## 11. Slash command 交互细则

### 11.1 命令目录

Ink Composer 在用户输入以 `/` 开头且尚未包含空格参数时，显示当前可用命令：

| 命令 | 作用 | 参数提示 |
|---|---|---|
| `/model` | 查看或切换模型 | 可选模型名 |
| `/clear` | 清空当前 transcript 显示 | 无 |
| `/compact` | 压缩当前会话上下文 | 无 |

`exit` 和 `新会话` 仍保留为普通输入命令，但不进入 slash 提示目录。

### 11.2 键盘行为

- 输入 `/` 后显示命令候选；
- 继续输入时按前缀过滤；
- `Tab` 选择当前候选并写入 Composer；
- `Up` / `Down` 在候选面板打开时优先切换候选，不浏览输入历史；
- `Enter` 仍提交当前文本；
- `Esc` 关闭候选面板但保留已输入内容；
- 输入包含空格参数后关闭候选面板，不修改参数文本。

候选面板最多显示 4 行，不能把 Composer 或 StatusPane 推出窗口。无匹配时不显示空框。

### 11.3 非目标

- 本阶段不执行本地命令补全或模型名远程查询；
- 不改变 Python 侧 slash command 解析和命令语义；
- 不把命令帮助写入 transcript；
- 不引入 modal 编辑器或 Vim 键位模式。
