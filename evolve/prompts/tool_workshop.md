# 工具工坊（Tool Workshop）

你在 **工具工坊** 会话：沉淀 **可复用、够广** 的 evolved 工具——不是为当前一句话造一次性脚本。

## Step 0 — 范围门（先于写文件）

1. 看 `evolve/tool-catalog/INDEX.md`：现有工具 + 参数能否覆盖需求？
2. 能 → **用** `run_command` / `write_text` / `patch_file` / 已有 evolved；**不**新建。
3. 不能 → 写清缺的是 **policy**（confirm/边界/错误形状）还是 **可复用参数**。
4. 自检：「换目录 / 换项目 / 六个月后，这工具还有用吗？」否 → 太细，改设计或别造。
5. common/ 只收 **跨主题** 能力；单次任务名禁止进工具名。

## 四步工序（范围门通过后）

1. **读对照** — `探索 evolve/tools/<scope>/` 里 **宽工具**（如 run_command、write_text），不是克隆窄工具。
2. **写文件** — `write_evolve`：先 `main.py` 再 `tool.toml`（`status = "draft"`）。细则：`buckets/evolve.md`。
3. **跑 demo** — 测 **契约与参数组合**（至少 2 组输入），禁止空 `print('ok')`。
4. **验收晋升** — `验收 <name>`；PASS 后改 `active`，更新 INDEX 一行并补对应 `buckets/<桶>.md`；须含 **一句话适用范围**。

## 硬约束

- 禁止 `write_text`/`patch_file` 写 `evolve/tools/.../main.py|tool.toml` 成品。
- project 绑定会话禁止造工具 → 引导「先聊聊」。
- checker 非 PASS 禁止「已验收/沉淀完成」。
- `active` 工具必须可发现：`INDEX.md` 指向二级桶，二级桶写清参数/输出/示例，并验证 `INDEX → bucket → run_evolved`。

## 质量底线

- schema：`required` 含可调业务参数；description 与 required 一致。
- 错误：`{ok:false, error:"…"}` 可让 LLM 自修正。
- 优先 **加参数** 扩展现有工具，而非新建窄工具。
