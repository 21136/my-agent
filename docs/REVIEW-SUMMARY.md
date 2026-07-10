# 多 LLM 评审整合摘要

> 日期：2026-07-09 · 输入：4 份评审 · 输出：`PROJECT.md` v0.2.0

## 评审来源

| 文件 | 评审者 | 总评 |
|------|--------|------|
| `reviews/2026-07-09-grok-review.md` | Grok 4.5 | 7/10 |
| `reviews/2026-07-09-review-cursor-agent.md` | Cursor Agent | 7/10 |
| `reviews/2026-07-09-claude-review.md` | Claude Cowork | 6/10 |
| `reviews/2026-07-09-gpt-5-5-review.md` | GPT-5.5 | 7/10 |

**加权共识**：方向正确（问题定义 8～9 分），执行设计不足（进化消费侧、MVP 顺序、验收指标）；整合后目标 **7.5+/10 可实施性**。

---

## 四轮共识（已全部写入 PROJECT.md v0.2.0）

### 1. 最大硬伤：只有写入、没有读取

四份评审均指出 §4.3 进化协议缺 **使用侧闭环**。  
**决议**：新增 §4.4，MVP 采用「显式 skill 调用 + memory 摘要注入 + 引用日志」。

### 2. MVP 过大且顺序错误

**决议**：

- 新增 **M0.5**（独立 Word 脚本，无 LLM）
- 砍：多 LLM adapter、SQLite、自动 L3、MVP 自动路由、完整 backup UI
- Word COM 降为 **可选增强**，非 MVP 硬依赖

### 3. 验收指标可刷、不测用户收益

**决议**：§7.2 改为主判据——1 次真实 Word 全链路 + 1 条 proposal 被接受 + 用户愿意继续用。

### 4. U 盘架构需调整

**决议**：Git 为 source of truth；U 盘为工作副本；`workspace/`、`state.json` gitignore；R2 扩展为损坏 **+ 丢失泄露**。

### 5. 安全措辞需诚实

**决议**：删除「后期可关确认」「沙箱」误导；明确 MVP 仅 **人工确认 + dry-run + 路径约定**。

### 6. 格式与生态

**决议**：`SKILL.md` 兼容 Cursor；`meta.json` 存生命周期字段。

### 7. Proposal 降噪

**决议**：每会话 ≤2 条；仅 `exit` 触发；显式请求或任务成功后。

### 8. 对话隐私

**决议**：默认只存摘要；全文需 `--record`。

### 9. Word 场景补全

**决议**：§5.4 母版生命周期；§5.5 无母版降级；填充前书签校验。

---

## 分歧与裁决

| 分歧点 | 观点 A | 观点 B | **裁决** |
|--------|--------|--------|----------|
| MVP 最小路由 | Grok：关键词选 0～1 skill | Cursor/Claude：纯显式调用 | **MVP 纯显式**；M4+ 再加自动路由 |
| Word 工具时机 | Cursor：内置 COM 提前 | Grok：手写脚本，COM 可选 | **M0.5 纯 Python 脚本**；COM 可选 |
| 最大硬伤表述 | 三份：消费侧空白 | Claude：curator 负担 | **两者都写入**：§4.2 curator + §4.4 使用侧 |
| 定期审阅 | GPT-5.5：`review evolve` 命令 | Cursor：可人工习惯 | **M4 做 `my-agent review` 命令** |

---

## 采纳 / 拒绝 / 延后清单

### 采纳

- [x] §4.4 使用侧协议
- [x] M0.5 里程碑
- [x] 行为导向验收
- [x] Git 真源架构
- [x] Cursor SKILL.md + meta.json
- [x] proposal 降噪规则
- [x] evidence 原文摘录
- [x] CLI session 边界定义
- [x] 母版生命周期
- [x] 无技术沙箱声明
- [x] 对话默认不落全文

### 拒绝

- [ ] 第二 LLM 审校 proposal（成本高，个人项目不划算）
- [ ] MVP 内完整多 LLM adapter
- [ ] MVP 内 SQLite / 向量检索

### 延后至 M4+

- [ ] 自动 skill 路由
- [ ] `expires_at` / `confidence` 逻辑
- [ ] 进程级沙箱
- [ ] Web / TUI
- [ ] `backup/` 自动快照 UI

---

## 下一步行动（给作者）

1. **M1**：实现 `agent-core/main.py` CLI 骨架
2. **Git**：首次 push 到私有远端
3. 用 **任意真实任务** 沉淀第一条 skill/memory（不必是文档类）

> **v0.2.1 修订**：作者决定不对 Word 专项化；下文 M0.5 / Word 验收等建议 **不再作为项目基线**，仅作评审历史参考。

---

## 评审文件索引

原始评审保留于 `docs/reviews/`，未修改，供日后对照。
