# 外部项目原地接入设计

> 版本：0.1.0 · 2026-10-16
> 状态：**current · 已确认 · implemented**（2026-10-16）
> 范围：Windows Desktop/CLI 普通模式；自动模式 v1/v2 不改变 owner。

## 1. 目标

允许用户把在 Cursor、Claude、其他 IDE 或普通文件夹中已经写了一部分的项目原地接入本软件继续开发：不复制源码，后续写入直接落到外部原目录；同时复用现有 `workspace/<id>` 项目工具、权限、TASKS 和项目状态链。

## 2. 推荐方案：workspace junction

```text
外部原目录 F:\\other\\my-project
              │
              │  用户确认后创建目录 junction
              ▼
workspace/my-project  ───────────────► F:\\other\\my-project

LLM / Executor 仍只使用逻辑路径 workspace/my-project/...
实际文件读写通过 junction 落到外部原目录。
```

不把绝对外部路径直接交给模型或普通工具参数，继续复用现有项目根目录和 foreign-project guard。

## 3. 接入流程

### 3.1 发现/扫描（只读）

新增 `项目 导入 <path> [id]`，第一步只扫描，不写外部目录：

- 规范化并解析绝对路径；拒绝 agent root、workspace 根目录、会话目录和明显危险目录；
- 推断项目 ID（用户显式 ID 优先，否则取目录名并规范化）；
- 检测 Git、README、PROJECT、TASKS、VERIFY、package/pyproject/pom 等项目事实；
- 检测已有测试/构建命令，只做候选，不虚构可执行证据；
- 检测工作区是否有未提交修改并展示；
- 检测 `workspace/<id>` 是否已经存在或已被其它外部根占用。

扫描结果必须经过用户确认才创建 junction 或写入项目元数据。

### 3.2 确认接入

确认后：

1. 创建 `workspace/<id>` 目录 junction 指向外部根；
2. 创建/刷新 `.plan-agent/manifest.json`，但不得覆盖已有项目文档；
3. 如果没有 `TASKS.md`，只生成一份明确标为“导入待确认”的最小任务草稿，不能伪造已确认计划；
4. 绑定普通 project session，设置请求级 `execute` 入口但不写 `project_plan_status=confirmed`；
5. 记录外部根与 junction 关系，供项目列表、切换、恢复和安全删除使用；
6. 返回扫描事实、接入路径和下一步选项：继续实现、先整理任务、只读检查。

### 3.3 后续打开/切换

- 项目列表将 junction 当作普通项目展示；
- 打开/切换复用现有项目 session 映射；
- 如果 junction 丢失或目标外部根不存在，显示“外部项目不可用”，不自动创建空项目、不写 confirmed；
- 删除项目默认只解除 junction 和索引，不删除外部原目录；删除外部目录必须另走危险操作确认。

## 4. 安全边界

- 只允许用户显式确认后的外部根；扫描阶段绝不写；
- 外部根必须是目录，不能是 agent root、workspace 根、session/data 目录或其父级；
- junction 目标必须保持固定，检测到目标变化时 fail-closed；
- 项目内普通写入仍受 request intent、路径和 action-risk 规则约束；
- `git push`、安装、网络写、删除、发布等危险动作仍按原有确认；
- 不自动读取或保存 `.env`、密钥文件内容；扫描只记录名称/类型，不记录秘密值。

## 5. 兼容策略

- 现有 `project_root=workspace/<id>` 继续有效，避免大面积改工具路径解析；
- 外部关联保存在 agent 级项目索引/元数据，不放入 `SessionMeta` 的权限字段；
- 自动模式 v1/v2 不自动接管外部项目，只有显式开启后才使用其原有 owner；
- 外部项目已有的 `TASKS.md`、Git 历史和代码不被重写；
- 不要求外部项目预先符合本项目七文件模板。

## 6. 组件与测试范围

- `agent-core/external_project.py`：路径规范化、扫描、junction 创建/校验/解除、外部关联索引；
- `agent-core/project_cli.py`：`项目 导入` 与确认/取消流程；
- `agent-core/project_api.py` / `server.py`：`project.import.scan`、`project.import.apply`、`project.import.cancel`；
- `agent-core/project_switch.py` / `context_switch.py`：打开、恢复、失效 junction 和只解除不删除原目录；
- `agent-core/tools/executor.py`：外部根和 junction 失效时 fail-closed；
- Desktop：导入扫描卡、确认卡、失效状态提示；
- 测试：扫描只读、确认后原目录写入、已有 Git/未提交变更保留、无 TASKS 导入、junction 失效、拒绝危险根、切换恢复和自动模式隔离。

## 7. 不做的事

- 不复制整个项目作为默认行为；
- 不自动把其他软件的聊天记录或模型上下文导入；
- 不根据 README 或模型猜测直接生成 confirmed/完成状态；
- 不在第一版支持任意网络盘/符号链接链/容器挂载的复杂解析。
