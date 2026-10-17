# Test Scope and Baseline

> 版本：0.1.0 · 2026-10-17
> 状态：**draft · D7 测试范围设计，待实现计划确认**
> 关联文档：`docs/PROJECT-VERIFY.md`、`docs/PROJECT-QUALITY.md`

## 1. 问题

主仓库根目录直接执行 pytest 时，会递归收集物理存在于 `workspace/` 下的多个独立示例项目。

这些项目：

- 有自己的项目根目录；
- 有自己的 import path；
- 可能存在同名 `tests` 包；
- 可能要求从示例项目目录运行；
- 不属于 my-agent 核心测试范围。

因此根目录全量 pytest 会产生收集错误，不能作为主项目健康度结论。

## 2. 测试层级

### L0：语法与模块编译

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m compileall -q agent-core
```

用途：快速发现语法错误，不代表行为通过。

### L1：主项目 focused tests

只运行当前变更直接覆盖的测试，例如：

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q \
  agent-core/tests/test_skill_registry.py \
  agent-core/tests/test_skill_prompt_integration.py
```

用途：变更级反馈。

### L2：主项目 core regression

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests
```

用途：核心 Python 回归。结果必须区分：

```text
passed
skipped/unsupported
failed
collection_error
interrupted
```

### L3：真实运行时/模型验收

例如：

```powershell
F:\my-agent-main\.venv\Scripts\python.exe tools\e2e_0x567_flash.py --maintenance-only
```

用途：确认真实模型、工具调用、项目状态和最终证据。

### L4：仓库级收集

仓库级收集只用于检查测试范围配置：

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest --collect-only -q
```

它不应自动进入 `workspace/` 下的独立项目。

## 3. 主仓库默认范围

当前主项目提供显式入口：`F:\my-agent-main\tools\run_main_tests.py`。它不依赖 pytest 递归发现，Wave 0 默认以显式参数运行主项目测试；当前已实现 `core`、`collect` 和 `focused` 三种 scope。

主仓库默认 pytest 测试路径应固定为：

```text
agent-core/tests
```

推荐根级配置：

```toml
[tool.pytest.ini_options]
testpaths = ["agent-core/tests"]
```

如果根级配置会影响未配置独立项目的继承，则优先使用显式 runner，避免让 `workspace/` 子项目继承主项目配置。

如果需要临时验证其他目录，显式指定路径，而不是依赖递归发现：

```powershell
pytest agent-core/tests/test_skill_registry.py
pytest workspace/codebase-doctor/tests
```

## 4. workspace 独立项目规则

### 公共示例项目

项目根：

```text
workspace/codebase-doctor
```

核心测试：

```powershell
cd workspace/codebase-doctor
python -m pytest tests
```

示例项目测试：

```powershell
cd workspace/codebase-doctor/examples/sample_project
python -m pytest
```

### 私有示例项目

项目根：

```text
workspace/private-code/codebase-doctor
```

示例项目必须从自己的根目录运行，以保证 `src` 在 import path 中：

```powershell
cd workspace/private-code/codebase-doctor/example_project
python -m pytest
```

不能把多个独立项目的 `tests/` 一次性传给同一个 pytest 进程，除非它们已经完成包命名和 import path 隔离。

## 5. CI 命令

主仓库 CI 默认只运行：

```powershell
python -m compileall -q agent-core
python -m pytest -q agent-core/tests
```

workspace 独立项目必须拥有独立 CI job 或独立工作目录，不纳入主仓库默认 job。

## 6. 收集错误判定

以下情况属于测试范围/环境错误，不得直接标成代码回归：

- 示例项目 import path 缺失；
- 多个项目同名 `tests` 包冲突；
- workspace 独立项目缺少其自己的依赖；
- 当前目录不是该项目根目录；
- 未配置的 optional dependency。

报告格式：

```text
scope: workspace/codebase-doctor/examples/sample_project
command: python -m pytest
cwd: F:/my-agent-main
result: collection_error
cause: project-local import root was not active
correct_command: run from the example project root
```

## 7. 自举验收要求

Self-hosting Run 不能只记录一个总的 `pytest` 结果，必须记录：

```text
test_scope
command
cwd
python executable
dependency preflight
exit code
passed/skipped/failed/collection_error
artifact/evidence path
```

如果 L2 core regression 失败，但 L1 focused 和 L3 live acceptance 通过，最终状态只能是 `partial`/`blocked`，不能写成全仓库通过。

## 8. 迁移完成标准

- 根目录 `pytest --collect-only -q` 不再收集 workspace 独立项目；
- `agent-core/tests` 仍能独立运行；
- workspace 子项目仍能从各自根目录独立运行；
- CI 与本地默认命令一致；
- 验收报告显示明确的 scope 和 cwd；
- 不删除、不改写 workspace 示例项目以掩盖收集问题。

## 9. 非目标

- 不把所有 workspace 项目合并成一个 Python package；
- 不为解决根目录收集问题修改示例项目 import 写法；
- 不把 skipped/unsupported 伪装成 passed；
- 不因根目录收集错误直接修改业务实现。
